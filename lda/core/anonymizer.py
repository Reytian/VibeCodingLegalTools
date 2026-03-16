"""
Anonymization engine — LLM extraction + regex supplement + replacement.

Workflow:
0. Regex pre-filter: Catch structured PII (emails, phones, addresses, amounts, etc.)
1. Pass 1 (LLM): Extract entity definitions and alias relationships from key sections
   (receives pre-filter hints so LLM can focus on names and contextual entities)
2. Pass 2 (LLM): Scan full document segment by segment — SKIPPED for short documents
   where regex + Pass 1 already provide good coverage
3. Regex supplement: Catch any remaining patterns the LLM missed
4. Execute replacement: Replace sensitive items with placeholders, generate mapping
"""

import re
import logging
from datetime import datetime
from core.llm_client import call_llm, parse_json_response
from core.prompts import PASS1_PROMPT, PASS1_PROMPT_WITH_PREFILTER, PASS2_PROMPT
from core.section_detector import detect_key_sections

logger = logging.getLogger(__name__)

# Role labels that should NOT be anonymized — they are structural, not PII
ROLE_LABELS = {
    "甲方", "乙方", "丙方", "丁方",
    "转让方", "受让方", "出让方", "承租方", "出租方",
    "借款方", "贷款方", "担保方", "委托方", "受托方",
    "发起方", "接收方", "买方", "卖方",
    "Party A", "Party B", "Party C", "Party D",
    "Transferor", "Transferee", "Buyer", "Seller",
    "Lender", "Borrower", "Guarantor", "Lessor", "Lessee",
    "Licensor", "Licensee", "Assignor", "Assignee",
    "目标公司", "Target", "Target Company", "the Company",
    "the Target", "the Buyer", "the Seller",
}

# Regex patterns for sensitive data the LLM commonly misses
_REGEX_PATTERNS = [
    # Email addresses
    ("email", re.compile(r'[\w.+-]+@[\w.-]+\.\w{2,}')),
    # Phone: +86 138-1234-5678, +1 (212) 555-0199, etc.
    ("phone", re.compile(r'\+\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}')),
    # Bank account numbers (16-19 consecutive digits)
    ("bank", re.compile(r"\b\d{16,19}\b")),
    # Unified Social Credit Code (18 chars: digits + uppercase letters)
    ("regnum", re.compile(r'\b[0-9A-HJ-NP-Z]{2}\d{6}[0-9A-HJ-NP-Z]{10}\b')),
    # Cayman/BVI registration numbers like 2024-C-001234
    ("regnum", re.compile(r'\b\d{4}-[A-Z]-\d{4,8}\b')),
    # US street addresses: "7 Clyde Road, Somerset New Jersey 08873"
    # Matches both state abbreviation (NJ) and full state name (New Jersey)
    ("address", re.compile(
        r'\d+\s+[\w\s]+?\b(?:Road|Street|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Circle|Cir|Terrace|Ter|Trail|Trl|Pike|Highway|Hwy)\b'
        r'[.,]?\s*(?:(?:Suite|Ste|Apt|Unit|Floor|Fl|#)\s*[\w-]+[.,]?\s*)?'
        r'[\w\s]+?[,\s]+(?:\b[A-Z]{2}\b|(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New\s+Hampshire|New\s+Jersey|New\s+Mexico|New\s+York|North\s+Carolina|North\s+Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode\s+Island|South\s+Carolina|South\s+Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West\s+Virginia|Wisconsin|Wyoming))\s+\d{5}(?:-\d{4})?',
        re.IGNORECASE,
    )),
    # Dollar amounts: $300,000 or $1,500,000.00
    ("amount", re.compile(r'\$[\d,]+(?:\.\d{1,2})?')),
]


def _regex_entity_scan(text: str):
    """Scan text with regex patterns to find entities the LLM may have missed."""
    found = []
    for entity_type, pattern in _REGEX_PATTERNS:
        for match in pattern.finditer(text):
            found.append({
                "text": match.group(0),
                "type": entity_type,
                "canonical": "",
            })
    return found


# ============================================================
# Regex pre-filter: run BEFORE LLM passes to pre-identify structured PII
# ============================================================
def run_regex_prefilter(text: str):
    """Run regex patterns on full text to pre-identify structured PII.

    Returns deduplicated list of entities found by regex. These are passed
    as hints to Pass 1 so the LLM can focus on names and contextual entities.
    """
    raw = _regex_entity_scan(text)

    # Deduplicate by (text, type)
    seen = set()
    unique = []
    for entity in raw:
        key = (entity["text"], entity["type"])
        if key not in seen:
            seen.add(key)
            unique.append(entity)

    # Log summary by type
    type_counts = {}
    for e in unique:
        t = e["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    if unique:
        summary = ", ".join(f"{t}={c}" for t, c in sorted(type_counts.items()))
        logger.info("Regex pre-filter found %d entities: %s", len(unique), summary)
        for e in unique:
            logger.debug("  Pre-filter: [%s] %s", e["type"], e["text"])
    else:
        logger.info("Regex pre-filter found 0 entities")

    return unique


def _format_prefilter_for_prompt(prefilter_results):
    """Format pre-filter results as a readable list for inclusion in the LLM prompt."""
    if not prefilter_results:
        return "(none)"

    lines = []
    for e in prefilter_results:
        lines.append(f"- [{e['type']}] {e['text']}")
    return "\n".join(lines)


def should_skip_pass2(
    text: str,
    pass1_result: dict,
    prefilter_results,
) -> bool:
    """Decide whether Pass 2 can be skipped to save an LLM call.

    Pass 2 is safe to skip when:
    1. Document is short enough that Pass 1 covered the full text (< 20K chars,
       which is the segment size used by _split_into_segments)
    2. Pass 1 + regex pre-filter found a reasonable number of entities

    Returns True if Pass 2 should be skipped.
    """
    doc_len = len(text)

    # Only skip for short documents where Pass 1 sees everything
    if doc_len >= 20000:
        logger.info(
            "Pass 2 NOT skipped: document too long (%d chars >= 20K threshold)",
            doc_len,
        )
        return False

    # Count entities from Pass 1 and pre-filter combined
    pass1_entity_count = len(pass1_result.get("entities", []))
    prefilter_count = len(prefilter_results)
    total = pass1_entity_count + prefilter_count

    # Need at least some entities to justify skipping
    if total == 0:
        logger.info("Pass 2 NOT skipped: no entities found by Pass 1 + pre-filter")
        return False

    # Check that Pass 1 found at least one person or company name
    # (the things regex cannot find). If Pass 1 found names, we likely
    # have good coverage and don't need Pass 2 for a short doc.
    name_types = {"person", "company"}
    pass1_has_names = any(
        e.get("type", "").lower() in name_types
        for e in pass1_result.get("entities", [])
    )

    if not pass1_has_names:
        logger.info(
            "Pass 2 NOT skipped: Pass 1 found no person/company names "
            "(total entities: %d pass1 + %d prefilter)",
            pass1_entity_count, prefilter_count,
        )
        return False

    logger.info(
        "Pass 2 SKIPPED: short document (%d chars), good coverage "
        "(%d pass1 entities + %d prefilter entities, names found)",
        doc_len, pass1_entity_count, prefilter_count,
    )
    return True


def _sweep_signature_block_names(text: str, entities):
    """Find person names near /s/ signature markers that the LLM missed.

    Looks for known person entity names (or parts of multi-word names) appearing
    within a few lines after a /s/ marker.
    """
    person_entities = [
        e for e in entities
        if e.get("type") == "person" and e.get("text", "").strip()
    ]
    if not person_entities:
        return []

    # Find all /s/ marker positions
    sig_pattern = re.compile(r'/s/', re.IGNORECASE)
    found = []
    known_texts = {e.get("text", "") for e in entities}

    for sig_match in sig_pattern.finditer(text):
        # Look at the 300 chars after each /s/ marker
        window_start = sig_match.end()
        window_end = min(len(text), window_start + 300)
        window = text[window_start:window_end]

        for person in person_entities:
            person_name = person.get("text", "").strip()
            if not person_name:
                continue
            canonical = person.get("canonical", "").strip() or person_name

            # Check for name parts (first name, last name) in the window
            # Even if the full name exists, there may be split/formatted variants
            name_parts = person_name.split()
            for part in name_parts:
                if len(part) < 3:
                    continue
                if part in window and part not in known_texts:
                    found.append({
                        "text": part,
                        "type": "person",
                        "canonical": canonical,
                    })
                    known_texts.add(part)
                    logger.info("Signature sweep found name part: %s (canonical: %s)", part, canonical)

    return found


def _sweep_header_company_names(text: str, entities):
    """Ensure company names found by the LLM are also caught in the document header.

    If a company entity was identified anywhere in the document, scan the first
    500 characters for that same company name. If present but not yet in the
    entity list, add it so the replacement step catches it.

    This is a no-op for replacement (same text will map to same placeholder),
    but ensures the header occurrence is not missed during replacement.
    """
    company_entities = [
        e for e in entities
        if e.get("type") == "company" and e.get("text", "").strip()
    ]
    if not company_entities:
        return []

    header = text[:500]
    found = []
    known_texts = {e.get("text", "") for e in entities}

    for company in company_entities:
        company_name = company.get("text", "").strip()
        if not company_name:
            continue
        # The company name is already in entities — just verify it appears in header
        # The replacement engine will handle it. But if there are partial variants
        # in the header (e.g. without "Inc." suffix), try to catch those too.
        canonical = company.get("canonical", "").strip() or company_name

        # Also check for company name without common suffixes
        base_variants = [company_name]
        for suffix in [", Inc.", " Inc.", ", LLC", " LLC", ", Ltd.", " Ltd.",
                       ", Corp.", " Corp.", ", Co.", " Co.", ", L.P.", " L.P."]:
            if company_name.endswith(suffix):
                base_variants.append(company_name[: -len(suffix)])

        for variant in base_variants:
            if variant in header and variant not in known_texts:
                found.append({
                    "text": variant,
                    "type": "company",
                    "canonical": canonical,
                })
                known_texts.add(variant)
                logger.info("Header sweep found company variant: %s", variant)

    return found


# ============================================================
# Pass 1: Extract entity definitions and aliases
# ============================================================
def run_first_pass(text: str, prefilter_results: object = None):
    key_sections = detect_key_sections(text)
    logger.info("Pass 1: Extracted key sections (%d chars)", len(key_sections))

    if prefilter_results:
        prefilter_text = _format_prefilter_for_prompt(prefilter_results)
        prompt = PASS1_PROMPT_WITH_PREFILTER.format(
            prefilter_entities=prefilter_text,
            key_sections_text=key_sections,
        )
        logger.info("Pass 1: Using pre-filter aware prompt (%d pre-identified entities)", len(prefilter_results))
    else:
        prompt = PASS1_PROMPT.format(key_sections_text=key_sections)

    messages = [{"role": "user", "content": prompt}]

    response_text = call_llm(messages)
    result = parse_json_response(response_text)

    if "aliases" not in result:
        result["aliases"] = []
    if "entities" not in result:
        result["entities"] = []
    if "document_type" not in result:
        result["document_type"] = "Document"

    logger.info(
        "Pass 1 complete: %d aliases, %d entities, type=%s",
        len(result["aliases"]),
        len(result["entities"]),
        result.get("document_type"),
    )

    return result


# ============================================================
# Pass 2: Scan full document for all sensitive items
# ============================================================
def _split_into_segments(text: str, max_chars: int = 20000):
    paragraphs = text.split("\n")
    segments = []
    current_segment = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current_segment.strip():
                segments.append(current_segment.strip())
                current_segment = ""
            sentences = re.split(r"(?<=[。.！!？?])\s*", paragraph)
            temp = ""
            for sentence in sentences:
                if len(temp) + len(sentence) > max_chars and temp:
                    segments.append(temp.strip())
                    temp = ""
                temp += sentence
            if temp.strip():
                segments.append(temp.strip())
            continue

        if len(current_segment) + len(paragraph) + 1 > max_chars and current_segment.strip():
            segments.append(current_segment.strip())
            current_segment = ""

        current_segment += paragraph + "\n"

    if current_segment.strip():
        segments.append(current_segment.strip())

    return segments


def _build_alias_context(pass1_result: dict):
    lines = []
    for alias_group in pass1_result.get("aliases", []):
        canonical = alias_group.get("canonical", "")
        aliases = alias_group.get("aliases", [])
        entity_type = alias_group.get("type", "")
        alias_str = ", ".join(aliases) if aliases else "none"
        lines.append(f"- {canonical} (type: {entity_type}) = {alias_str}")

    if not lines:
        return "(no known entity definitions)"

    return "\n".join(lines)


def merge_pass1_and_prefilter(
    text: str,
    pass1_result: dict,
    prefilter_results,
):
    """Merge Pass 1 entities with pre-filter results when Pass 2 is skipped.

    This produces the same kind of entity list that run_second_pass would return,
    including regex supplement, signature sweep, and header sweep.
    """
    all_entities = list(pass1_result.get("entities", []))
    all_entities.extend(prefilter_results)

    # Run regex again on full text (catches anything in sections Pass 1 didn't see)
    regex_entities = _regex_entity_scan(text)
    all_entities.extend(regex_entities)

    # Context-aware sweeps
    sig_entities = _sweep_signature_block_names(text, all_entities)
    if sig_entities:
        logger.info("Signature sweep found %d additional name parts (no-pass2 mode)", len(sig_entities))
        all_entities.extend(sig_entities)

    header_entities = _sweep_header_company_names(text, all_entities)
    if header_entities:
        logger.info("Header sweep found %d additional company variants (no-pass2 mode)", len(header_entities))
        all_entities.extend(header_entities)

    # Deduplicate
    seen = set()
    unique = []
    for entity in all_entities:
        key = (entity.get("text", ""), entity.get("type", ""))
        if key not in seen and entity.get("text", "").strip():
            seen.add(key)
            unique.append(entity)

    _link_aliases(unique, pass1_result)

    logger.info("Merged entities (no Pass 2): %d unique entities", len(unique))
    return unique


def run_second_pass(
    text: str,
    pass1_result: dict,
    progress_callback=None,
):
    segments = _split_into_segments(text)
    alias_context = _build_alias_context(pass1_result)
    logger.info("Pass 2: Scanning %d segments", len(segments))

    all_entities = []
    for i, segment in enumerate(segments):
        if progress_callback:
            progress_callback(i + 1, len(segments))

        logger.info("Pass 2: Processing segment %d/%d", i + 1, len(segments))

        prompt = PASS2_PROMPT.format(
            entity_aliases_context=alias_context,
            document_segment=segment,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            response_text = call_llm(messages)
            entities = parse_json_response(response_text)
            if isinstance(entities, dict) and "entities" in entities:
                entities = entities["entities"]
            if isinstance(entities, list):
                all_entities.extend(entities)
        except Exception as e:
            logger.warning("Segment %d LLM scan failed: %s", i + 1, e)
            continue

    # Regex supplement: catch patterns the LLM missed
    regex_entities = _regex_entity_scan(text)
    if regex_entities:
        logger.info("Regex supplement found %d additional candidates", len(regex_entities))
        all_entities.extend(regex_entities)

    # Context-aware sweeps: catch names near /s/ markers and companies in headers
    sig_entities = _sweep_signature_block_names(text, all_entities)
    if sig_entities:
        logger.info("Signature sweep found %d additional name parts", len(sig_entities))
        all_entities.extend(sig_entities)

    header_entities = _sweep_header_company_names(text, all_entities)
    if header_entities:
        logger.info("Header sweep found %d additional company variants", len(header_entities))
        all_entities.extend(header_entities)

    # De-duplicate by (text, type)
    seen = set()
    unique_entities = []
    for entity in all_entities:
        key = (entity.get("text", ""), entity.get("type", ""))
        if key not in seen and entity.get("text", "").strip():
            seen.add(key)
            unique_entities.append(entity)

    # Fallback: if Pass 2 + regex found no entities, use Pass 1 entities
    if not unique_entities and pass1_result.get("entities"):
        logger.warning(
            "Pass 2 found no entities — falling back to %d entities from Pass 1",
            len(pass1_result["entities"]),
        )
        unique_entities = [
            e for e in pass1_result["entities"]
            if e.get("text", "").strip()
        ]
    elif not unique_entities:
        # Use Pass 1 entities as base, then merge
        unique_entities = [
            e for e in pass1_result.get("entities", [])
            if e.get("text", "").strip()
        ]
    else:
        # Merge Pass 1 entities into Pass 2 results (Pass 1 as base)
        pass1_entities = pass1_result.get("entities", [])
        pass1_seen = {(e.get("text", ""), e.get("type", "")) for e in unique_entities}
        for e in pass1_entities:
            key = (e.get("text", ""), e.get("type", ""))
            if key not in pass1_seen and e.get("text", "").strip():
                unique_entities.append(e)
                pass1_seen.add(key)

    # Always merge regex results with whatever we have
    existing_texts = {e.get("text", "") for e in unique_entities}
    for re_entity in regex_entities:
        if re_entity["text"] not in existing_texts:
            unique_entities.append(re_entity)
            existing_texts.add(re_entity["text"])
            logger.info("Regex added: [%s] %s", re_entity["type"], re_entity["text"])

    _link_aliases(unique_entities, pass1_result)

    logger.info("Pass 2 complete: %d unique entities found", len(unique_entities))

    return unique_entities


def _link_aliases(entities, pass1_result: dict):
    for entity in entities:
        if entity.get("canonical"):
            continue

        for alias_group in pass1_result.get("aliases", []):
            canonical = alias_group.get("canonical", "")
            aliases = alias_group.get("aliases", [])
            all_names = [canonical] + aliases

            if entity.get("text") in all_names:
                entity["canonical"] = canonical
                break


def _is_role_label(text: str) -> bool:
    """Check if text is a structural role label that should NOT be anonymized."""
    text_stripped = text.strip()
    if text_stripped in ROLE_LABELS:
        return True
    if text_stripped.lower() in {r.lower() for r in ROLE_LABELS}:
        return True
    return False


# ============================================================
# Execute replacement: generate anonymized text + mapping table
# ============================================================
def execute_replacement(
    text: str,
    entities,
    pass1_result: dict,
    source_filename: str = "",
    exclude_types=None,
):
    """
    Execute anonymization: replace sensitive items with placeholders.

    Key design decision: role labels are NOT replaced. They are structural
    contract elements, not PII. Only actual sensitive values are replaced.
    """
    alias_role_labels = set()
    for alias_group in pass1_result.get("aliases", []):
        for alias in alias_group.get("aliases", []):
            if _is_role_label(alias):
                alias_role_labels.add(alias)

    # Filter out excluded entity types
    if exclude_types:
        logger.info("Excluding entity types from anonymization: %s", exclude_types)

    # Step 1: Assign placeholders grouped by canonical entity
    canonical_groups = {}

    for entity in entities:
        entity_text = entity.get("text", "").strip()
        entity_type = entity.get("type", "unknown")
        canonical = entity.get("canonical", "").strip()

        if not entity_text:
            continue

        if _is_role_label(entity_text):
            logger.debug("Skipping role label: %s", entity_text)
            continue

        if exclude_types and entity_type.lower() in exclude_types:
            logger.debug("Skipping excluded type: %s (%s)", entity_type, entity_text)
            continue

        group_key = canonical if canonical else entity_text

        if group_key not in canonical_groups:
            canonical_groups[group_key] = {
                "type": entity_type,
                "texts": set(),
            }

        canonical_groups[group_key]["texts"].add(entity_text)
        if canonical and not _is_role_label(canonical):
            canonical_groups[group_key]["texts"].add(canonical)

    # Add ONLY non-role-label aliases from Pass 1
    for alias_group in pass1_result.get("aliases", []):
        canonical = alias_group.get("canonical", "")
        if canonical in canonical_groups:
            for alias in alias_group.get("aliases", []):
                if not _is_role_label(alias) and len(alias) >= 4:
                    canonical_groups[canonical]["texts"].add(alias)

    # Assign numbered placeholders per type
    type_counters = {}
    placeholder_map = {}
    text_to_placeholder = {}

    for canonical, group_info in canonical_groups.items():
        entity_type = group_info["type"].upper()

        if entity_type not in type_counters:
            type_counters[entity_type] = 1
        else:
            type_counters[entity_type] += 1

        placeholder = "{" + f"{entity_type}_{type_counters[entity_type]}" + "}"

        replaceable_texts = {t for t in group_info["texts"] if not _is_role_label(t)}

        placeholder_map[placeholder] = {
            "value": canonical,
            "type": group_info["type"],
            "aliases": list(replaceable_texts - {canonical}),
        }

        for t in replaceable_texts:
            text_to_placeholder[t] = placeholder

    # Step 2: Replace longest-first to avoid substring conflicts
    sorted_texts = sorted(text_to_placeholder.keys(), key=len, reverse=True)

    replacement_log = []
    anonymized_text = text

    for entity_text in sorted_texts:
        placeholder = text_to_placeholder[entity_text]

        # For short texts (< 8 chars), use word boundary matching to avoid
        # substring collisions (e.g., "Comp" matching inside "Company")
        if len(entity_text) < 8:
            pattern = re.compile(r'(?<!\w)' + re.escape(entity_text) + r'(?!\w)')
            for m in pattern.finditer(anonymized_text):
                pos = m.start()
                context_before = anonymized_text[max(0, pos - 40) : pos]
                context_after = anonymized_text[
                    pos + len(entity_text) : pos + len(entity_text) + 40
                ]
                replacement_log.append({
                    "placeholder": placeholder,
                    "original_text": entity_text,
                    "position": pos,
                    "context_before": context_before,
                    "context_after": context_after,
                })
            # Apply replacements (use re.sub for word boundary)
            anonymized_text = pattern.sub(placeholder, anonymized_text)
        else:
            search_start = 0
            while True:
                pos = anonymized_text.find(entity_text, search_start)
                if pos == -1:
                    break

                context_before = anonymized_text[max(0, pos - 40) : pos]
                context_after = anonymized_text[
                    pos + len(entity_text) : pos + len(entity_text) + 40
                ]

                replacement_log.append({
                    "placeholder": placeholder,
                    "original_text": entity_text,
                    "position": pos,
                    "context_before": context_before,
                    "context_after": context_after,
                })

                anonymized_text = (
                    anonymized_text[:pos]
                    + placeholder
                    + anonymized_text[pos + len(entity_text) :]
                )

                search_start = pos + len(placeholder)

    # Step 2b: Whitespace-normalized replacement pass
    # Catches entities split across lines (e.g., "CareCloud,\nInc." or "A.\n Hadi Chaudhry")
    for entity_text in sorted_texts:
        if len(entity_text) < 4:
            continue
        placeholder = text_to_placeholder[entity_text]
        # Build a regex that treats any whitespace in the entity as flexible whitespace
        words = entity_text.split()
        if len(words) < 2:
            continue
        # Escape each word for regex, join with flexible whitespace pattern
        flex_pattern = r'[\s]+'.join(re.escape(w) for w in words)
        try:
            replaced = True
            while replaced:
                replaced = False
                for m in re.finditer(flex_pattern, anonymized_text):
                    matched_text = m.group(0)
                    # Skip if this is already a placeholder or exact match (already handled)
                    if '{' in matched_text or matched_text == entity_text:
                        continue
                    replacement_log.append({
                        "placeholder": placeholder,
                        "original_text": matched_text,
                        "position": m.start(),
                        "context_before": anonymized_text[max(0, m.start() - 40):m.start()],
                        "context_after": anonymized_text[m.end():m.end() + 40],
                    })
                    anonymized_text = (
                        anonymized_text[:m.start()]
                        + placeholder
                        + anonymized_text[m.end():]
                    )
                    replaced = True
                    break  # Restart finditer since positions shifted
        except re.error:
            continue

    # Step 3: Assemble mapping table
    mapping = {
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "source_file": source_filename,
            "entity_count": len(canonical_groups),
            "excluded_types": sorted(exclude_types) if exclude_types else [],
        },
        "mappings": placeholder_map,
        "replacement_log": replacement_log,
    }

    logger.info(
        "Replacement complete: %d entity groups, %d replacements made",
        len(canonical_groups),
        len(replacement_log),
    )

    return anonymized_text, mapping
