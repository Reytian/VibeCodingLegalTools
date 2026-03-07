#!/usr/bin/env python3
"""
Convert raw legal documents into instruction-tuning pairs for legal drafting.

Input:  Directory of .txt legal documents (from ../raw/)
Output: JSONL file at ../drafting_train.jsonl

Each example uses chat-style messages compatible with mlx-lm fine-tuning.
Key terms are extracted heuristically and used to generate a natural drafting
instruction. The assistant response is the actual document text.
"""

import json
import logging
import os
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "raw"
OUTPUT_FILE = BASE_DIR / "drafting_train.jsonl"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a legal drafting assistant for a New York-based cross-border law firm. "
    "Draft professional legal documents following standard legal conventions. "
    "Use precise legal language, proper section numbering, and appropriate disclaimers."
)

# ---------------------------------------------------------------------------
# Document type detection
# ---------------------------------------------------------------------------
DOC_TYPE_PATTERNS = [
    (r"employment\s+agreement", "employment agreement"),
    (r"consulting\s+agreement", "consulting agreement"),
    (r"independent\s+contractor\s+agreement", "independent contractor agreement"),
    (r"non[-\s]?disclosure\s+agreement|nda|confidentiality\s+agreement", "non-disclosure agreement"),
    (r"non[-\s]?compete\s+agreement|non[-\s]?competition", "non-compete agreement"),
    (r"stock\s+(?:option|purchase)\s+(?:agreement|plan)", "stock option agreement"),
    (r"severance\s+agreement", "severance agreement"),
    (r"separation\s+agreement", "separation agreement"),
    (r"merger\s+agreement", "merger agreement"),
    (r"asset\s+purchase\s+agreement", "asset purchase agreement"),
    (r"stock\s+purchase\s+agreement", "stock purchase agreement"),
    (r"loan\s+agreement|credit\s+agreement", "loan agreement"),
    (r"lease\s+agreement|rental\s+agreement", "lease agreement"),
    (r"license\s+agreement|licensing\s+agreement", "license agreement"),
    (r"services?\s+agreement|master\s+services?\s+agreement", "services agreement"),
    (r"subscription\s+agreement", "subscription agreement"),
    (r"partnership\s+agreement", "partnership agreement"),
    (r"operating\s+agreement", "operating agreement"),
    (r"shareholders?\s+agreement", "shareholder agreement"),
    (r"indemnification\s+agreement|indemnity\s+agreement", "indemnification agreement"),
    (r"settlement\s+agreement", "settlement agreement"),
    (r"amendment|first\s+amendment|second\s+amendment", "amendment"),
    (r"power\s+of\s+attorney", "power of attorney"),
    (r"promissory\s+note", "promissory note"),
    (r"guaranty|guarantee\s+agreement", "guaranty agreement"),
    (r"letter\s+agreement", "letter agreement"),
]

# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

RE_AMOUNT = re.compile(
    r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"
    r"(?:\s?(?:million|billion|thousand|m|b|k))?",
    re.IGNORECASE,
)

RE_DATE = re.compile(
    r"\b(?:"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4}"
    r"|"
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r")\b",
    re.IGNORECASE,
)

RE_ORG = re.compile(
    r"\b((?:[A-Z][A-Za-z&.']+\s+){0,5}"
    r"(?:Inc\.?|Corp(?:oration)?\.?|LLC|LLP|Ltd\.?|L\.?P\.?|"
    r"Company|Co\.?|Group|Holdings|Partners|Associates))\b"
)

RE_PERSON_TITLE = re.compile(
    r"(?:(?:Mr\.|Mrs\.|Ms\.|Dr\.)\s+)"
    r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)"
)

# Title/role extraction
RE_TITLE = re.compile(
    r"\b(?:as\s+(?:the\s+)?|position\s+of\s+(?:the\s+)?|role\s+of\s+(?:the\s+)?|"
    r"title\s+of\s+(?:the\s+)?|serve\s+as\s+(?:the\s+)?)"
    r"([A-Z][A-Za-z\s]{3,50}?)(?:\s*[.,;(]|\s+of\s+|\s+at\s+|\s+for\s+|\s+with\s+)",
    re.IGNORECASE,
)

# Jurisdiction / governing law
RE_JURISDICTION = re.compile(
    r"(?:govern(?:ed|ing)\s+(?:by\s+)?(?:the\s+)?laws?\s+of\s+(?:the\s+)?(?:State\s+of\s+)?"
    r"|jurisdiction\s+of\s+(?:the\s+)?(?:State\s+of\s+)?)"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    re.IGNORECASE,
)

# Term / duration
RE_TERM = re.compile(
    r"\b(?:term\s+of\s+|period\s+of\s+|duration\s+of\s+)"
    r"(\d+\s+(?:year|month|day|week)s?)",
    re.IGNORECASE,
)

# Compensation patterns
RE_COMPENSATION = re.compile(
    r"(?:base\s+salary|annual\s+(?:salary|compensation)|compensation)\s+(?:of|shall\s+be|is|equal\s+to)\s+"
    r"(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?:\s?(?:million|thousand|m|k))?)",
    re.IGNORECASE,
)


def detect_doc_type(text: str) -> str:
    """Detect the type of legal document from its content."""
    text_lower = text[:3000].lower()
    for pattern, doc_type in DOC_TYPE_PATTERNS:
        if re.search(pattern, text_lower):
            return doc_type
    return "legal agreement"


def extract_parties(text: str) -> list[str]:
    """Extract party names from the document."""
    parties = []

    # Look for "between X and Y" pattern
    between_match = re.search(
        r"(?:between|by\s+and\s+between)\s+"
        r"([A-Z][A-Za-z&.,'\s]+?)(?:\s*\(|,\s*a\s)",
        text[:2000],
    )
    if between_match:
        parties.append(between_match.group(1).strip().rstrip(","))

    # Organization names
    for m in RE_ORG.finditer(text[:2000]):
        org = m.group(1).strip()
        if org and org not in parties and len(org) > 3:
            parties.append(org)
            if len(parties) >= 2:
                break

    # Person names with titles
    for m in RE_PERSON_TITLE.finditer(text[:2000]):
        name = m.group(1).strip()
        if name and name not in parties:
            parties.append(name)
            if len(parties) >= 3:
                break

    return parties[:3]


def extract_key_terms(text: str) -> dict:
    """Extract key contractual terms from the document."""
    terms = {}

    # Document type
    terms["doc_type"] = detect_doc_type(text)

    # Parties
    parties = extract_parties(text)
    if parties:
        terms["parties"] = parties

    # Job title / position
    title_match = RE_TITLE.search(text[:3000])
    if title_match:
        title = title_match.group(1).strip()
        # Clean up common noise
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) < 60:
            terms["position"] = title

    # Compensation
    comp_match = RE_COMPENSATION.search(text)
    if comp_match:
        terms["compensation"] = comp_match.group(1).strip()
    else:
        # Fallback: first dollar amount
        amounts = RE_AMOUNT.findall(text[:3000])
        if amounts:
            terms["compensation"] = amounts[0]

    # Term / duration
    term_match = RE_TERM.search(text)
    if term_match:
        terms["term"] = term_match.group(1).strip()

    # Jurisdiction
    jur_match = RE_JURISDICTION.search(text)
    if jur_match:
        terms["jurisdiction"] = jur_match.group(1).strip()

    # Effective date
    dates = RE_DATE.findall(text[:2000])
    if dates:
        terms["effective_date"] = dates[0]

    return terms


def build_user_prompt(terms: dict) -> str:
    """Generate a natural-language drafting instruction from extracted terms."""
    doc_type = terms.get("doc_type", "legal agreement")
    parties = terms.get("parties", [])

    # Build the core instruction
    parts = [f"Draft a {doc_type}"]

    if len(parties) >= 2:
        parts.append(f"between {parties[0]} and {parties[1]}")
    elif len(parties) == 1:
        parts.append(f"for {parties[0]}")

    details = []

    if "position" in terms:
        details.append(f"for the position of {terms['position']}")

    if "compensation" in terms:
        details.append(f"with annual compensation of {terms['compensation']}")

    if "term" in terms:
        details.append(f"for a term of {terms['term']}")

    if "effective_date" in terms:
        details.append(f"effective {terms['effective_date']}")

    if "jurisdiction" in terms:
        details.append(f"governed by the laws of {terms['jurisdiction']}")

    if details:
        parts.append("with the following terms: " + "; ".join(details))

    prompt = " ".join(parts) + "."

    # Add any extra parties
    if len(parties) > 2:
        prompt += f" Additional party: {parties[2]}."

    return prompt


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------
def clean_document(text: str) -> str:
    """Clean up document text for use as assistant response."""
    # Normalize whitespace
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove EDGAR-specific headers/footers
    text = re.sub(r"^.*?(?=\n\s*\n)", "", text, count=0)  # keep as-is for now
    # Strip leading/trailing whitespace
    text = text.strip()
    return text


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    return len(text) // 4


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------
def read_file_safe(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def process_document(path: Path) -> list[dict]:
    """Process a single document into training examples."""
    text = read_file_safe(path)
    if not text.strip():
        log.warning("Skipping empty file: %s", path.name)
        return []

    text = clean_document(text)

    # Skip very short documents
    if len(text) < 200:
        log.warning("Skipping short file (%d chars): %s", len(text), path.name)
        return []

    terms = extract_key_terms(text)
    user_prompt = build_user_prompt(terms)

    # If the document is very long, we still keep it as one example
    # (the model should learn to produce full documents)
    # But we cap at ~8000 tokens to avoid excessive examples
    max_chars = 8000 * 4
    if len(text) > max_chars:
        # Truncate at a paragraph boundary
        truncated = text[:max_chars]
        last_para = truncated.rfind("\n\n")
        if last_para > max_chars // 2:
            text = truncated[:last_para].strip()
        else:
            text = truncated.strip()
        text += "\n\n[Document continues...]"

    example = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": text},
        ]
    }

    return [example]


def main():
    if not RAW_DIR.exists():
        log.error("Raw directory does not exist: %s", RAW_DIR)
        log.info("Create it and add .txt legal documents, then re-run.")
        sys.exit(1)

    txt_files = sorted(RAW_DIR.glob("*.txt"))
    if not txt_files:
        log.error("No .txt files found in %s", RAW_DIR)
        sys.exit(1)

    log.info("Found %d .txt files in %s", len(txt_files), RAW_DIR)

    all_examples = []
    doc_type_counts = {}
    total_tokens = 0
    terms_found = {
        "parties": 0, "position": 0, "compensation": 0,
        "term": 0, "jurisdiction": 0, "effective_date": 0,
    }

    for path in txt_files:
        examples = process_document(path)
        if examples:
            # Track stats
            for ex in examples:
                user_msg = ex["messages"][1]["content"]
                # Count extracted terms from the prompt
                for key in terms_found:
                    if key == "parties":
                        if "between" in user_msg or "for " in user_msg[:30]:
                            terms_found[key] += 1
                    elif key == "position" and "position of" in user_msg:
                        terms_found[key] += 1
                    elif key == "compensation" and "compensation" in user_msg:
                        terms_found[key] += 1
                    elif key == "term" and "term of" in user_msg:
                        terms_found[key] += 1
                    elif key == "jurisdiction" and "laws of" in user_msg:
                        terms_found[key] += 1
                    elif key == "effective_date" and "effective" in user_msg:
                        terms_found[key] += 1

            all_examples.extend(examples)
            log.info("  %s -> %d examples", path.name, len(examples))

            # Count doc types
            terms = extract_key_terms(read_file_safe(path))
            dt = terms.get("doc_type", "unknown")
            doc_type_counts[dt] = doc_type_counts.get(dt, 0) + 1

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for ex in all_examples:
            token_count = sum(estimate_tokens(m["content"]) for m in ex["messages"])
            total_tokens += token_count
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # Print stats
    log.info("=" * 50)
    log.info("Drafting Dataset Summary")
    log.info("=" * 50)
    log.info("Total examples: %d", len(all_examples))
    if all_examples:
        avg_tokens = total_tokens // len(all_examples)
        log.info("Average tokens per example: ~%d", avg_tokens)

    log.info("Document types:")
    for dt, count in sorted(doc_type_counts.items(), key=lambda x: -x[1]):
        log.info("  %-35s %d", dt, count)

    log.info("Extracted term coverage:")
    for key, count in terms_found.items():
        pct = (count / len(all_examples) * 100) if all_examples else 0
        log.info("  %-20s %d (%.0f%%)", key, count, pct)

    log.info("Output: %s", OUTPUT_FILE)
    log.info("=" * 50)


if __name__ == "__main__":
    main()
