# Rule 1.6-Compliant AI Workflow for Legal Practice

**Use consumer AI applications for legal work without violating your duty of confidentiality.**

This repository provides an open-source, self-hosted workflow that lets lawyers use consumer-grade AI tools (Claude, ChatGPT, etc.) to draft and edit confidential legal documents — while staying compliant with ABA Model Rule 1.6 and its state equivalents.

Two deployment versions are included: a **Maximum Security** version that runs entirely on local hardware, and a **Standard** version that uses cloud APIs to orchestrate local anonymization. Both achieve the same result: **real client data never reaches third-party AI services**.

---

## Table of Contents

- [The Problem: Rule 1.6 and Consumer AI](#the-problem-rule-16-and-consumer-ai)
- [How This Workflow Solves It](#how-this-workflow-solves-it)
- [Version 1: Maximum Security (Local-First)](#version-1-maximum-security-local-first)
- [Version 2: Standard (API-Orchestrated)](#version-2-standard-api-orchestrated)
- [Comparison: This Workflow vs. Harvey AI](#comparison-this-workflow-vs-harvey-ai)
- [What's Included](#whats-included)
- [Quick Start](#quick-start)
- [How the Anonymizer Works](#how-the-anonymizer-works)
- [Client Memory System](#client-memory-system)
- [Supported File Types](#supported-file-types)
- [Ethics & Compliance](#ethics--compliance)
- [License](#license)

---

## The Problem: Rule 1.6 and Consumer AI

### What Rule 1.6 Requires

ABA Model Rule 1.6(a) states that a lawyer "shall not reveal information relating to the representation of a client unless the client gives informed consent." Rule 1.6(c) further requires lawyers to "make reasonable efforts to prevent the inadvertent or unauthorized disclosure of, or unauthorized access to, information relating to the representation of a client."

This is not limited to courtroom secrets. "Information relating to the representation" is interpreted broadly — it covers **all** information learned during the attorney-client relationship, including names, addresses, financial details, deal terms, and business strategies, regardless of the source.

### What ABA Formal Opinion 512 Says About AI

In July 2024, the ABA Standing Committee on Ethics and Professional Responsibility issued [Formal Opinion 512](https://www.americanbar.org/content/dam/aba/administrative/professional_responsibility/ethics-opinions/aba-formal-opinion-512.pdf), the first comprehensive ethics guidance on lawyers' use of generative AI. The Opinion addresses six areas — competence (Rule 1.1), confidentiality (Rule 1.6), communication (Rule 1.4), candor (Rules 3.1/3.3), supervision (Rules 5.1/5.3), and fees — and draws on earlier opinions regarding cloud computing and outsourcing.

On confidentiality specifically, the Opinion states:

> Before lawyers enter information related to client representation into a [generative AI] tool, they must assess the potential that the information entered into the tool will be "disclosed to or accessed by" other individuals inside and outside the firm.

The Opinion analogizes AI tools to cloud computing services, confirming that lawyers must:

1. **Investigate** the reliability, security measures, and data-handling policies of any AI tool
2. **Ensure** the tool is configured to protect confidentiality and security
3. **Confirm** that confidentiality obligations are enforceable (e.g., contractual)
4. **Monitor** for breaches or changes in the provider's practices

### Why Consumer AI Tools Are Problematic

When you paste a client contract into ChatGPT, Claude, or any consumer AI application, the text is transmitted to the provider's servers. Even with enterprise data retention policies, this raises serious Rule 1.6 concerns:

- **Data transmission**: Client PII leaves your control and enters a third party's infrastructure
- **Training risk**: Consumer-tier products may use inputs for model training (check the ToS carefully)
- **Breach exposure**: You are now dependent on the provider's security for *your* ethical obligation
- **Audit gap**: You cannot verify what happens to the data after transmission
- **Informed consent**: Obtaining client consent for every AI interaction is impractical at scale

Most lawyers respond to this by either (a) not using AI at all (losing competitive advantage) or (b) using AI anyway and hoping for the best (risking disciplinary action). Neither is a good answer.

### The Solution: Anonymize Before Transmission

The key insight is simple: **if the AI never sees real client data, there is no Rule 1.6 issue.**

By automatically anonymizing documents before they reach any cloud AI service — replacing real names, addresses, and identifiers with generic placeholders — and then restoring the original data locally after editing, lawyers can leverage AI assistance while maintaining full compliance. The cloud AI edits `{COMPANY_1}` and `{PERSON_1}`, not "Acme Corp" and "John Smith."

This approach satisfies every requirement of Formal Opinion 512:
- Confidential information is never entered into the AI tool
- The anonymization/deanonymization happens entirely on hardware you control
- No investigation of the AI provider's data policies is necessary (they never receive protected data)
- No client consent is required for the AI interaction (no confidential information is disclosed)

---

## How This Workflow Solves It

```
User sends document + instructions
    ↓
Local Agent (Counsel) classifies the request
    ├─ Contains client data? → Anonymize locally → Cloud AI edits → Deanonymize locally → User
    ├─ Fill client info?     → Load client memory (LOCAL only) → Populate template → User
    ├─ Non-sensitive task?   → Delegate to cloud AI directly → User
    └─ Simple question?      → Answer locally → User
```

The pipeline for sensitive documents:

```
Original Document (with real client data)
    ↓ [YOUR MACHINE: Local LLM scans and identifies all sensitive entities]
Anonymized Document ({COMPANY_1}, {PERSON_1}, etc.) + mapping.json
    ↓ [CLOUD AI: Sees only placeholders — edits, drafts, revises as instructed]
Edited Anonymized Document (new clauses added, placeholders preserved)
    ↓ [YOUR MACHINE: Deterministic deanonymization restores real data]
Final Document (real client data restored, AI edits applied)
```

**mapping.json** (the Rosetta Stone between real data and placeholders) **never leaves your machine**.

---

## Version 1: Maximum Security (Local-First)

**For lawyers who want zero cloud dependency for sensitive operations.**

In this version, a local LLM runs on your own hardware (e.g., a Mac Mini with Apple Silicon). It handles:
- Request classification and routing
- Document anonymization (2-pass entity extraction)
- Client memory management (loading/populating client details)
- Template filling from per-client YAML files

Only the anonymized document is sent to a consumer AI app (Claude Code, ChatGPT, etc.) for the actual editing work. The local LLM never needs an internet connection for any sensitive operation.

### Architecture

```
                        YOUR MACHINE (Mac Mini / MacBook)
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  User ──→ Counsel Agent (local Ollama, e.g. Qwen3 30B)          │
│              │                                                   │
│              ├─→ LDA Anonymizer (local LLM) ──→ anonymized.txt   │
│              │                                   + mapping.json  │
│              │                                        │          │
│              │         ┌──────────────────────────────────────┐   │
│              │         │  CLOUD (consumer AI app)             │   │
│              │         │  Sees ONLY: {COMPANY_1}, {PERSON_1}  │   │
│              │         │  Returns: edited anonymized text      │   │
│              │         └──────────────────────────────────────┘   │
│              │                                        │          │
│              ├─→ LDA Deanonymizer (deterministic) ←───┘          │
│              │         → restored document with real data        │
│              │                                                   │
│              ├─→ Client Memory (clients/*.yaml) ──→ LOCAL only   │
│              │                                                   │
│              └─→ Non-sensitive tasks ──→ Cloud AI pass-through   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Requirements

| Component | Specification |
|-----------|---------------|
| Hardware | Mac with Apple Silicon (M1+), 32GB RAM recommended |
| Local LLM | [Ollama](https://ollama.ai/) + `qwen3:30b-a3b` (~18GB) |
| Python | 3.13+ with `requests`, `python-docx` |
| Consumer AI | [Claude Code CLI](https://claude.ai/code), ChatGPT, or any AI editor |
| Orchestrator | [OpenClaw](https://openclaw.ai/) (optional, for agent automation) |

### Cost

| Item | Cost |
|------|------|
| Hardware (Mac Mini M4 32GB) | ~$800 one-time |
| Ollama + local model | Free (open source) |
| Claude Code CLI | ~$20/month (Pro plan) |
| OpenClaw | Free (open source) |
| **Total ongoing** | **~$20/month** |

---

## Version 2: Standard (API-Orchestrated)

**For lawyers who prefer a cloud-hosted agent but still need confidentiality protection.**

In this version, your primary agent runs on a cloud API (e.g., Kimi K2.5, Claude Sonnet, GPT-4o) and acts as the orchestrator. When it encounters a sensitive document, it invokes the LDA anonymizer **on your local machine** to strip all client data before sending the sanitized text to a consumer AI app for editing.

The key difference from Version 1: the orchestrating agent itself runs in the cloud, but it only sees the document **after** you've initiated the anonymization locally — or it instructs a local process to anonymize before forwarding.

### Architecture

```
User ──→ Cloud Agent (Kimi / Claude API)
            │
            ├─ "Anonymize this document"
            │     ↓
            │   YOUR MACHINE: LDA anonymizer (local Ollama)
            │     → anonymized.txt + mapping.json (stays local)
            │     ↓
            ├─ Cloud Agent receives anonymized text only
            │     → edits / drafts / revises
            │     ↓
            ├─ "Deanonymize the result"
            │     ↓
            │   YOUR MACHINE: LDA deanonymizer (deterministic)
            │     → restored document
            │     ↓
            └─ User receives final document
```

### Requirements

| Component | Specification |
|-----------|---------------|
| Hardware | Mac with Apple Silicon (M1+), 16GB+ RAM |
| Local LLM | [Ollama](https://ollama.ai/) + `qwen3:30b-a3b` (for anonymization only) |
| Python | 3.13+ with `requests`, `python-docx` |
| Cloud Agent API | Kimi K2.5 (free tier available), Claude API, or OpenAI API |
| Consumer AI | Claude Code CLI, ChatGPT, or any AI editor |

### Cost

| Item | Cost |
|------|------|
| Hardware (existing Mac) | $0 (use your current machine) |
| Ollama + local model | Free (open source) |
| Cloud agent API | $0-50/month (depending on provider and usage) |
| Consumer AI tool | ~$20/month |
| **Total ongoing** | **~$20-70/month** |

### When to Choose Version 2 Over Version 1

- You don't have 32GB RAM for a large local model to handle routing
- You prefer a cloud agent for non-sensitive tasks (faster, more capable)
- You're comfortable with the cloud agent seeing non-sensitive metadata
- You want a simpler setup (no local agent orchestration)

---

## Comparison: This Workflow vs. Harvey AI

[Harvey AI](https://www.harvey.ai/) is the leading enterprise legal AI platform, used by firms like Allen & Overy and PwC. Here's how this open-source workflow compares:

| Dimension | This Workflow | Harvey AI |
|-----------|--------------|-----------|
| **Monthly cost** | $20-70/month | ~$1,000-1,200/user/month |
| **Annual cost (solo)** | $240-840/year | ~$12,000-14,400/year (min 20 seats = ~$288,000/year) |
| **Target user** | Solo practitioners, small firms, cross-border lawyers | Am Law 100 firms, Fortune 500 legal departments |
| **Setup time** | 1-2 hours (install Ollama, clone repo, configure) | Weeks-months (enterprise sales, demos, implementation) |
| **Confidentiality approach** | Client data never leaves your machine (anonymize-before-transmit) | Enterprise data agreements, SOC 2 compliance, contractual guarantees |
| **Rule 1.6 compliance** | Structural (data never transmitted) | Contractual (data transmitted under NDA/DPA) |
| **Customization** | Full control — edit prompts, models, skills, client memory | Limited to Harvey's feature set and configuration options |
| **Client memory** | Per-client YAML files with entity details, preferences, banking info | Firm-wide knowledge base (Harvey's "vault") |
| **Model choice** | Any model — local (Ollama), Claude, GPT, Kimi, open-source | Harvey's proprietary fine-tuned models (OpenAI-based) |
| **Bilingual support** | Built-in (tested with Chinese/English cross-border contracts) | English-primary; limited multilingual support |
| **Offline capability** | Full (Version 1); partial (Version 2) | None (cloud-only) |
| **Open source** | Yes (MIT license) | No (proprietary) |
| **Vendor lock-in** | None — swap any component at any time | High — enterprise contract, proprietary platform |

### The Confidentiality Argument

Harvey's approach to Rule 1.6 is contractual: they sign enterprise data processing agreements, maintain SOC 2 Type II certification, and promise not to train on your data. This is the standard approach for legal tech SaaS, and it satisfies many ethics committees.

**This workflow takes a fundamentally different approach**: rather than trusting a third party with your client data under contract, it ensures the third party **never receives** client data at all. The cloud AI sees `{COMPANY_1}` acquiring a 30% stake in `{COMPANY_2}` — it doesn't know (and can't know) who the real parties are.

Both approaches are defensible under Formal Opinion 512. But the anonymize-before-transmit approach has a significant advantage: **it requires no investigation of the AI provider's data practices, no contractual negotiation, and no client consent** — because no protected information is ever disclosed.

This makes it particularly suitable for:
- **Solo practitioners and small firms** who lack the bargaining power to negotiate enterprise DPAs
- **Cross-border lawyers** dealing with multiple privacy regimes (GDPR, PIPL, etc.) where contractual protections may be insufficient
- **Matters with heightened confidentiality** (M&A, whistleblower, government investigations) where even contractual disclosure is unacceptable
- **Cost-conscious practices** that want AI capabilities without enterprise pricing

---

## What's Included

```
├── README.md                          # This file
├── openclaw-config/
│   ├── openclaw-legal-agent.json      # Agent config (local-first model)
│   └── openclaw-providers.json        # Provider templates (add your API keys)
├── workspace-legal/
│   ├── SOUL.md                        # Agent personality + routing logic
│   ├── AGENTS.md                      # Workflow instructions + skill routing
│   ├── TOOLS.md                       # Available tools reference
│   ├── IDENTITY.md                    # Agent identity
│   ├── USER.md                        # User context template
│   └── clients/
│       ├── _template.yaml             # Blank client memory template
│       └── sample-globalventures.yaml # Sample client (fictional data)
├── skills/
│   ├── client-memory/SKILL.md         # Per-client memory management
│   ├── legal-doc-anonymizer/SKILL.md  # Sensitive doc pipeline
│   ├── cloud-pass-through/SKILL.md    # Non-sensitive delegation
│   └── skill-creator/SKILL.md         # Skill creation via cloud AI
└── lda/
    ├── lda_cli.py                     # CLI entry point
    ├── core/
    │   ├── anonymizer.py              # 2-pass LLM anonymization engine
    │   ├── deanonymizer.py            # 3-step deterministic restoration
    │   ├── file_handler.py            # .txt/.doc/.docx file I/O
    │   ├── llm_client.py              # Ollama API client
    │   ├── prompts.py                 # English prompts for bilingual docs
    │   └── section_detector.py        # Key section extraction
    └── tests/
        └── sample_contract.txt        # Sample bilingual contract for testing
```

---

## Quick Start

### 1. Install Ollama + Model

```bash
# Install Ollama (macOS)
brew install ollama
ollama pull qwen3:30b-a3b
```

### 2. Set Up LDA Tool

```bash
cd lda/
python3 -m venv .venv
source .venv/bin/activate
pip install requests python-docx
```

### 3. Test Anonymization

```bash
python lda_cli.py anonymize --input tests/sample_contract.txt --output-dir /tmp/lda-test
# Check: /tmp/lda-test/anonymized.txt should have {PLACEHOLDER} tokens
# Check: /tmp/lda-test/mapping.json should have entity mappings
```

### 4. Test Full Pipeline

```bash
# Anonymize
python lda_cli.py anonymize --input tests/sample_contract.txt --output-dir /tmp/lda-test

# Edit with Claude Code (only sees anonymized text)
claude --print --dangerously-skip-permissions \
  -p "Read /tmp/lda-test/anonymized.txt. Add a governing law clause choosing New York law. Preserve ALL {PLACEHOLDER} tokens exactly. Write to /tmp/lda-test/edited.txt"

# Deanonymize (restore real data)
python lda_cli.py deanonymize \
  --input /tmp/lda-test/edited.txt \
  --mapping /tmp/lda-test/mapping.json \
  --output /tmp/lda-test/restored.txt
```

---

## How the Anonymizer Works

### Pass 1: Entity Definition Extraction

Scans definition clauses, notice sections, and signature pages to identify:
- Party definitions and aliases ("Party A" = "Acme Corp")
- All sensitive entities (names, companies, addresses, etc.)

### Pass 2: Full Document Scan

Scans every segment of the document, using Pass 1 context to catch:
- All entity mentions (including aliases and abbreviations)
- Financial amounts, phone numbers, emails, IDs, dates, etc.

### Replacement

- Groups entities by canonical name (aliases share one placeholder)
- Replaces longest strings first to avoid substring conflicts
- Records position + context for precise deanonymization

### Deanonymization (3-step)

1. **Position-based**: Uses recorded positions for exact restoration
2. **Context-based**: Fuzzy-matches surrounding text when positions shift
3. **Canonical fallback**: Uses the formal entity name as last resort

---

## Client Memory System

Per-client memory files store entity details, contacts, preferences, and active matters in YAML format. These files are **Counsel-exclusive** — no other agent may access them, and they are never transmitted to cloud AI.

### How It Works

```
User: "This is Global Ventures' file. Fill in their information."
  → Counsel identifies client slug ("global-ventures")
  → Loads clients/global-ventures.yaml (~2KB)
  → Reads the contract template
  → Fills in client details using LOCAL model only
  → Returns populated contract — no cloud AI touched client PII
```

### Client File Format (YAML)

Each client file contains:
- **Entity identity**: legal name, jurisdiction, registration number, tax ID
- **Contacts & signatories**: names, titles, authority levels
- **Preferences**: governing law, arbitration venue, language, currency
- **Banking details**: for payment/escrow clauses
- **Related entities**: subsidiaries, affiliates, parent companies
- **Active matters**: current engagements and their status
- **Counterparties**: frequent opposing parties

See `workspace-legal/clients/_template.yaml` for the full schema and `sample-globalventures.yaml` for a working example.

### Access Control (3 layers)

1. **Structural**: Client files live in Counsel's workspace only (`workspace-legal/clients/`)
2. **Counsel's instructions**: SOUL.md, AGENTS.md, and SKILL.md all prohibit sharing client PII with other agents, cloud AI, or shared memory
3. **Other agents' instructions**: Other agents' configurations mark `workspace-legal/clients/` as off-limits

### Local Model Performance

Tested with Qwen3 30B on Mac Mini M4 (32GB):
- **Full template fill** (8+ fields including bilingual Chinese/English): 100% accuracy, ~60s
- **Targeted extraction** (3-4 fields): 100% accuracy, ~12s
- **Zero hallucinations** across all tests

---

## Supported File Types

| Format | Read | Write | Notes |
|--------|------|-------|-------|
| `.txt` | Yes | Yes | UTF-8/GBK auto-detection |
| `.docx` | Yes | Yes | Preserves formatting via python-docx |
| `.doc` | Yes | Yes | Via macOS `textutil` conversion |

---

## Ethics & Compliance

This workflow is designed to help lawyers comply with:

- **ABA Model Rule 1.6** — Duty of confidentiality
- **ABA Formal Opinion 512** (2024) — Ethics guidance on generative AI use
- **NY RPC Rule 1.6** — Confidentiality of information
- **GDPR Article 32** — Security of processing (for cross-border matters)

By anonymizing before cloud transmission, lawyers can leverage consumer AI assistance while maintaining their ethical obligations. The cloud AI provider is never a "recipient" of confidential information under Rule 1.6, because the information it receives is not identifiable.

### Disclaimer

This repository provides a technical workflow and does not constitute legal advice. Lawyers should evaluate this workflow against their jurisdiction's specific ethics rules and obtain their own ethics guidance as appropriate. The authors make no representations regarding the sufficiency of this workflow for compliance with any particular ethics rule or opinion.

---

## References

- [ABA Model Rules of Professional Conduct, Rule 1.6](https://www.americanbar.org/groups/professional_responsibility/publications/model_rules_of_professional_conduct/rule_1_6_confidentiality_of_information/)
- [ABA Formal Opinion 512 — Generative Artificial Intelligence Tools (2024)](https://www.americanbar.org/content/dam/aba/administrative/professional_responsibility/ethics-opinions/aba-formal-opinion-512.pdf)
- [ABA Ethics Opinion on Generative AI Offers Useful Framework](https://www.americanbar.org/groups/business_law/resources/business-law-today/2024-october/aba-ethics-opinion-generative-ai-offers-useful-framework/)

---

## License

MIT
