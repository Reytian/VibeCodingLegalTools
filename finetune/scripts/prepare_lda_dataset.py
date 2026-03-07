#!/usr/bin/env python3
"""
Convert raw legal documents into instruction-tuning pairs for PII anonymization.

Input:  Directory of .txt legal documents (from ../raw/)
Output: JSONL file at ../lda_train.jsonl

Each example uses chat-style messages compatible with mlx-lm fine-tuning.
PII detection is heuristic/regex-based -- produces bootstrap training data.
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
OUTPUT_FILE = BASE_DIR / "lda_train.jsonl"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a legal document anonymizer. Identify all personally identifiable "
    "information (PII) in the document and replace each instance with a typed "
    "placeholder. Use these placeholder types: {PERSON_N} for names, {ORG_N} "
    "for organizations, {ADDRESS_N} for addresses, {DATE_N} for specific dates, "
    "{AMOUNT_N} for monetary amounts, {SSN_N} for social security numbers, "
    "{PHONE_N} for phone numbers, {EMAIL_N} for email addresses. Return the "
    "full document with all PII replaced, followed by a mapping section."
)

# ---------------------------------------------------------------------------
# Regex patterns for PII detection
# ---------------------------------------------------------------------------

# Email
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

# SSN  (xxx-xx-xxxx or xxx xx xxxx)
RE_SSN = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")

# Phone  (various US formats)
RE_PHONE = re.compile(
    r"(?<!\d)"
    r"(?:\+?1[-.\s]?)?"
    r"(?:\(?\d{3}\)?[-.\s]?)"
    r"\d{3}[-.\s]?\d{4}"
    r"(?!\d)"
)

# Dollar amounts  ($1,234.56 or $1234 or $1.5 million etc.)
RE_AMOUNT = re.compile(
    r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"
    r"(?:\s?(?:million|billion|thousand|m|b|k))?"
    , re.IGNORECASE
)

# Dates  (various formats)
RE_DATE = re.compile(
    r"\b(?:"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4}"
    r"|"
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r"|"
    r"\d{1,2}-\d{1,2}-\d{2,4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r")\b"
    , re.IGNORECASE
)

# US street addresses (number + street name + type)
RE_ADDRESS = re.compile(
    r"\b\d{1,6}\s+"
    r"(?:[A-Z][a-z]+\s?)+"
    r"(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Drive|Dr\.?|"
    r"Road|Rd\.?|Lane|Ln\.?|Court|Ct\.?|Place|Pl\.?|Way|Circle|Cir\.?|"
    r"Suite|Ste\.?|Floor|Fl\.?)"
    r"(?:[,\s]+(?:[A-Z][a-z]+\s?)+)?"           # city
    r"(?:[,\s]+[A-Z]{2})?"                       # state
    r"(?:[,\s]+\d{5}(?:-\d{4})?)?"               # zip
    , re.MULTILINE
)

# Titles that signal a nearby person name
TITLE_WORDS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "professor",
    "ceo", "cfo", "cto", "coo", "cio", "president", "director",
    "chairman", "chairwoman", "vice president", "vp", "secretary",
    "treasurer", "officer", "manager", "partner", "counsel",
    "attorney", "esquire", "esq.", "judge", "honorable",
    "executive", "chief", "senior", "managing",
}

# Person names near titles
RE_PERSON_TITLE = re.compile(
    r"(?:(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.)\s+)"
    r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)"
)

# Capitalized name pairs near role keywords (e.g. "John Smith, CEO")
RE_PERSON_ROLE = re.compile(
    r"\b([A-Z][a-z]{1,20}\s+(?:[A-Z]\.?\s+)?[A-Z][a-z]{1,20})\b"
    r"(?:\s*,\s*(?:" + "|".join(re.escape(t) for t in TITLE_WORDS if len(t) > 3) + r"))"
    , re.IGNORECASE
)

# Organizations: words ending in Inc., Corp., LLC, LLP, Ltd., etc.
RE_ORG = re.compile(
    r"\b((?:[A-Z][A-Za-z&.']+\s+){0,5}"
    r"(?:Inc\.?|Corp(?:oration)?\.?|LLC|LLP|Ltd\.?|L\.?P\.?|"
    r"Company|Co\.?|Group|Holdings|Partners|Associates|"
    r"Foundation|Trust|Bank|Fund|Capital|Ventures|"
    r"Technologies|Solutions|Services|Systems|International))"
    r"\b"
)

# Standalone capitalized name pairs (fallback, lower priority)
RE_PERSON_CAPS = re.compile(
    r"\b([A-Z][a-z]{1,20}\s+(?:[A-Z]\.?\s+)?[A-Z][a-z]{1,20})\b"
)

# Common false-positive names to skip
FALSE_POSITIVE_NAMES = {
    "United States", "New York", "New Jersey", "New Mexico", "New Hampshire",
    "North Carolina", "North Dakota", "South Carolina", "South Dakota",
    "West Virginia", "Rhode Island", "District Columbia", "Puerto Rico",
    "San Francisco", "Los Angeles", "Las Vegas",
    "Supreme Court", "District Court", "Circuit Court",
    "Internal Revenue", "Securities Exchange", "Federal Reserve",
    "Section Number", "Article Number", "General Counsel",
    "Chief Executive", "Chief Financial", "Chief Operating",
    "Vice President", "Managing Director", "Senior Vice",
    "Employment Agreement", "Service Agreement", "Stock Option",
    "Annual Report", "Fiscal Year", "Calendar Year",
    "Effective Date", "Termination Date", "Closing Date",
    "Good Reason", "Change Control", "Base Salary",
}


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


def chunk_text(text: str, max_tokens: int = 2000) -> list[str]:
    """Split text into chunks of approximately max_tokens tokens."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return [text]

    chunks = []
    paragraphs = text.split("\n\n")
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # account for \n\n
        if current_len + para_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        # If a single paragraph exceeds max, split by sentences
        if para_len > max_chars:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sent in sentences:
                sent_len = len(sent) + 1
                if current_len + sent_len > max_chars and current:
                    chunks.append("\n\n".join(current))
                    current = []
                    current_len = 0
                current.append(sent)
                current_len += sent_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Anonymization engine
# ---------------------------------------------------------------------------
class Anonymizer:
    """Heuristic PII detector and replacer."""

    def __init__(self):
        self.counters = {}
        self.mappings = {}  # placeholder -> original
        self.seen = {}      # original (lowered) -> placeholder
        self.stats = {
            "PERSON": 0, "ORG": 0, "ADDRESS": 0, "DATE": 0,
            "AMOUNT": 0, "SSN": 0, "PHONE": 0, "EMAIL": 0,
        }

    def _next_placeholder(self, pii_type: str) -> str:
        self.counters[pii_type] = self.counters.get(pii_type, 0) + 1
        return "{" + f"{pii_type}_{self.counters[pii_type]}" + "}"

    def _register(self, pii_type: str, original: str) -> str:
        key = original.strip().lower()
        if key in self.seen:
            return self.seen[key]
        placeholder = self._next_placeholder(pii_type)
        self.mappings[placeholder] = original.strip()
        self.seen[key] = placeholder
        self.stats[pii_type] += 1
        return placeholder

    def anonymize(self, text: str) -> tuple[str, str]:
        """Return (anonymized_text, mapping_block)."""
        # Order matters: replace more specific patterns first

        # 1. SSNs (before phone to avoid overlap)
        text = RE_SSN.sub(lambda m: self._register("SSN", m.group(0)), text)

        # 2. Emails
        text = RE_EMAIL.sub(lambda m: self._register("EMAIL", m.group(0)), text)

        # 3. Phone numbers
        text = RE_PHONE.sub(lambda m: self._register("PHONE", m.group(0)), text)

        # 4. Dollar amounts
        text = RE_AMOUNT.sub(lambda m: self._register("AMOUNT", m.group(0)), text)

        # 5. Dates
        text = RE_DATE.sub(lambda m: self._register("DATE", m.group(0)), text)

        # 6. Addresses (before orgs/names to avoid partial matches)
        text = RE_ADDRESS.sub(lambda m: self._register("ADDRESS", m.group(0)), text)

        # 7. Organizations
        text = RE_ORG.sub(lambda m: self._register("ORG", m.group(1)), text)

        # 8. Person names (titled)
        text = RE_PERSON_TITLE.sub(
            lambda m: m.group(0).replace(m.group(1), self._register("PERSON", m.group(1))),
            text,
        )

        # 9. Person names (with role)
        text = RE_PERSON_ROLE.sub(
            lambda m: m.group(0).replace(m.group(1), self._register("PERSON", m.group(1))),
            text,
        )

        # 10. Standalone capitalized name pairs (only if not a false positive)
        def replace_caps_name(m):
            name = m.group(1)
            if name in FALSE_POSITIVE_NAMES:
                return m.group(0)
            # Skip if it looks like a section heading or all-caps word pair
            if name.isupper():
                return m.group(0)
            return m.group(0).replace(name, self._register("PERSON", name))

        text = RE_PERSON_CAPS.sub(replace_caps_name, text)

        # Build mapping block
        mapping_lines = ["--- MAPPING ---"]
        for placeholder, original in self.mappings.items():
            mapping_lines.append(f"{placeholder}: {original}")

        return text, "\n".join(mapping_lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def read_file_safe(path: Path) -> str:
    """Read a text file, handling encoding issues gracefully."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Last resort: read as bytes and decode with replacement
    return path.read_bytes().decode("utf-8", errors="replace")


def process_document(path: Path) -> list[dict]:
    """Process a single document into training examples."""
    text = read_file_safe(path)
    if not text.strip():
        log.warning("Skipping empty file: %s", path.name)
        return []

    # Clean up common artifacts
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)

    chunks = chunk_text(text, max_tokens=2000)
    examples = []

    for chunk in chunks:
        if len(chunk.strip()) < 100:
            continue

        anon = Anonymizer()
        anonymized_text, mapping_block = anon.anonymize(chunk)

        # Skip chunks with no PII found
        if not anon.mappings:
            log.debug("No PII found in chunk from %s, skipping", path.name)
            continue

        assistant_content = anonymized_text.strip() + "\n\n" + mapping_block

        example = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": chunk.strip()},
                {"role": "assistant", "content": assistant_content},
            ]
        }
        examples.append(example)

    return examples


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
    total_pii_stats = {
        "PERSON": 0, "ORG": 0, "ADDRESS": 0, "DATE": 0,
        "AMOUNT": 0, "SSN": 0, "PHONE": 0, "EMAIL": 0,
    }
    total_tokens = 0

    for path in txt_files:
        examples = process_document(path)
        all_examples.extend(examples)
        log.info("  %s -> %d examples", path.name, len(examples))

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for ex in all_examples:
            # Gather token stats
            token_count = sum(estimate_tokens(m["content"]) for m in ex["messages"])
            total_tokens += token_count
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # Print stats
    log.info("=" * 50)
    log.info("LDA Dataset Summary")
    log.info("=" * 50)
    log.info("Total examples: %d", len(all_examples))
    if all_examples:
        avg_tokens = total_tokens // len(all_examples)
        log.info("Average tokens per example: ~%d", avg_tokens)
    log.info("Output: %s", OUTPUT_FILE)
    log.info("=" * 50)


if __name__ == "__main__":
    main()
