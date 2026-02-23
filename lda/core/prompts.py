"""
Prompt templates for the LLM.

Contains two prompts:
- PASS1_PROMPT: Extract entity definitions and aliases from key sections
- PASS2_PROMPT: Identify all sensitive items in each document segment

Prompts are in English and handle bilingual (Chinese/English) legal documents.
"""

# ============================================================
# Pass 1 prompt: extract entity definitions from key contract sections
# ============================================================
PASS1_PROMPT = """You are a legal document analysis assistant. Below are key excerpts from a legal contract (including definition clauses, notice clauses, and signature pages). The document may be in English, Chinese, or bilingual.

Extract all party and entity definitions, as well as all sensitive information found in these excerpts.

What to extract:
1. Entity definition relationships: which abbreviations/aliases refer to the same entity
   Examples: "Party A" = "Shanghai Starchen Technology Co., Ltd.", "the Target" = "XYZ Technology Limited"
2. All sensitive information: personal names, company names, addresses, phone numbers, emails, amounts, etc.

Also determine the document type (e.g.: Share Purchase Agreement, Employment Agreement, Loan Agreement, NDA, Equity Transfer Agreement, etc.) — use a concise English label.

Return ONLY valid JSON, no markdown fencing, no other text:
{{
  "document_type": "Equity Transfer Agreement",
  "aliases": [
    {{
      "canonical": "Shanghai Example Technology Co., Ltd.",
      "aliases": ["Party A", "Transferor"],
      "type": "company"
    }}
  ],
  "entities": [
    {{"text": "Shanghai Example Technology Co., Ltd.", "type": "company"}},
    {{"text": "john@example.com", "type": "email"}}
  ]
}}

---
Key contract excerpts:
{key_sections_text}"""

# ============================================================
# Pass 2 prompt: identify all sensitive items per document segment
# ============================================================
PASS2_PROMPT = """You are a legal document anonymization assistant. Carefully read the following legal document segment and identify ALL sensitive information. The document may be in English, Chinese, or bilingual.

[Known Entity Definitions (from contract definition clauses)]
{entity_aliases_context}

Based on the above definitions, abbreviations like "Party A", "Target Company" (or their Chinese equivalents) should also be treated as sensitive information.

Sensitive information includes but is not limited to:
- Personal names (Chinese and English)
- Company/organization names (Chinese and English, including abbreviations/aliases from definitions above)
- Monetary amounts (numbers with currency symbols)
- Phone numbers, fax numbers
- Email addresses
- ID numbers: national ID, passport, SSN, EIN
- Bank account numbers
- Cryptocurrency wallet addresses
- Physical addresses (street-level)
- Company registration numbers (including Unified Social Credit Code, Cayman/BVI registration numbers)
- Specific dates (contract signing dates, deadlines — NOT general legal effective dates)

Return ONLY a valid JSON array, no markdown fencing, no other text:
[
  {{
    "text": "Sensitive information as it appears in the original text",
    "type": "person/company/amount/phone/email/id/bank/wallet/address/regnum/date",
    "canonical": "If this is a known alias of an entity, put the formal name here; otherwise empty string"
  }}
]

---
Document segment:
{document_segment}"""
