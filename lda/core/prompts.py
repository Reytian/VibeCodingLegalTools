"""
Prompt templates for the LLM.

Contains two prompts:
- PASS1_PROMPT: Extract entity definitions and aliases from key sections
- PASS2_PROMPT: Identify all sensitive items in each document segment

Optimized for small models (Qwen3 4B): concise, no example values to copy,
explicit verbatim-copy rule.
"""

# ============================================================
# Pass 1 prompt: extract entity definitions from key contract sections
# ============================================================
PASS1_PROMPT = """Read these legal contract excerpts carefully. Extract ALL sensitive information.

RULES:
1. Copy each entity's text EXACTLY from the document — character by character
2. Do NOT translate (if text is Chinese, keep Chinese; if English, keep English)
3. Find EVERY entity, not just a few
4. Return valid JSON only, no other text

Entity types to find: person, company, address, phone, email, regnum, bank, amount, date

Return format:
{{
  "document_type": "<type in English>",
  "aliases": [
    {{"canonical": "<full formal name copied from text>", "aliases": ["<abbreviation>"], "type": "<type>"}}
  ],
  "entities": [
    {{"text": "<exact text from document>", "type": "<type>"}}
  ]
}}

The "entities" array must include ALL of these found in the text:
- Every person name (Chinese and English) — including names in signature blocks near "/s/" markers
- Every company/organization name (Chinese and English) — including names in document headers, titles, and captions
- Every full street address (e.g. "7 Clyde Road, Somerset New Jersey 08873") — include street number, street name, city, state, and ZIP as ONE entity
- Every phone number (with country code if present)
- Every email address
- Every registration/ID number (USCC, Cayman reg, passport, etc.)
- Every bank account number
- Every bank name
- Every monetary/dollar amount (e.g. "$300,000", "USD 1,000,000.00") — include the currency symbol/code

IMPORTANT — commonly missed items:
- Person names that appear BELOW "/s/" signature lines (formatted name after the signature marker)
- Company names in document headers or title lines (first few lines of the document)
- Full US street addresses with ZIP codes
- Dollar amounts like "$300,000" or "$1,500,000.00"

---
Contract excerpts:
{key_sections_text}"""

# ============================================================
# Pass 2 prompt: identify all sensitive items per document segment
# ============================================================
PASS2_PROMPT = """Read this legal document segment. Find ALL sensitive information not yet found.

Known entities already found:
{entity_aliases_context}

Find any ADDITIONAL sensitive items in this segment. Types: person, company, address, phone, email, regnum, bank, amount, date.

Pay special attention to:
- Person names near "/s/" signature markers (the formatted name below the signature line)
- Company names in headers, titles, or captions
- Full street addresses (street + city + state + ZIP)
- Dollar amounts ("$300,000", "$1,500,000.00")

RULES:
1. Copy text EXACTLY from the document — do NOT translate
2. Only include items NOT already in the known entities list above
3. Return valid JSON array only, no other text

Return format:
[
  {{"text": "<exact text from document>", "type": "<type>", "canonical": ""}}
]

If no new entities found, return: []

---
Document segment:
{document_segment}"""
