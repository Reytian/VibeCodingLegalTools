---
name: client-memory
description: >
  Load a client's memory file and use it to populate contracts or answer
  client-specific questions. Triggered when the user references a specific
  client and asks to fill in information, draft with client details, or
  look up client-specific data. All processing stays LOCAL — client data
  never reaches cloud AI.
---

# Client Memory Skill

## When to Use

Activate this skill when the user:
- Says "This is [Client X]'s file" or "Fill in [Client X]'s information"
- Asks you to populate a contract template with a specific client's details
- Asks about a client's preferences, contacts, entities, or active matters
- Provides a new document and asks to associate it with a client

## Access Control

**CRITICAL — READ THIS FIRST:**

- Client memory files are stored at `clients/<slug>.yaml` in this workspace.
- These files contain **real client PII** (names, addresses, tax IDs, banking details).
- **NEVER** include client memory data in messages to other agents (Claptrap, PM, Spark).
- **NEVER** pass client YAML content to cloud AI (Claude Code, Kimi, etc.) without anonymization.
- **NEVER** write client data to shared memory files, daily logs, or MEMORY.md.
- If you need cloud AI to edit a document that contains client data, use the
  `legal-doc-anonymizer` skill first.
- When responding to inter-agent requests, provide only **non-sensitive metadata**
  (e.g., "I have 3 active matters for this client") — never raw PII.

## Workflow: Populate a Contract

1. **Identify the client.** Parse the user's message for a client name or slug.

2. **Load the client file.** Read `clients/<slug>.yaml`.
   - If the slug is ambiguous, list available clients: `ls clients/*.yaml`
   - If no match, ask the user to clarify or create a new client file.

3. **Read the contract/template.** The user will provide or reference a file.

4. **Fill in client details using LOCAL model only.**
   - This is a **local-only operation**. Use your own context (Kimi/Ollama) to
     map YAML fields to contract blanks.
   - Common mappings:
     - `legal_name` → Party name, "the Company", etc.
     - `registered_address` → formatted as single line
     - `signatories[N].name` + `signatories[N].title` → signature blocks
     - `preferences.governing_law` → governing law clause
     - `preferences.arbitration_venue` → arbitration clause
     - `tax_id` → tax identification references
     - `related_entities[N].*` → subsidiary/affiliate party details
   - For bilingual contracts: use `legal_name_local` alongside `legal_name`.

5. **Return the populated document** to the user. Summarize what was filled in.

## Workflow: Create a New Client

1. Copy `clients/_template.yaml` to `clients/<new-slug>.yaml`.
2. Ask the user for the key details, or extract them from a provided document.
3. Fill in the YAML fields.
4. Confirm with the user before saving.

## Workflow: Update Client Info

1. Load `clients/<slug>.yaml`.
2. Apply the user's requested changes.
3. Show a diff/summary of what changed.
4. Save the updated file.

## Workflow: Extract Client Info from a Document

When the user says "This is Client X's file. Fill in their information":
1. Read the provided document (contract, engagement letter, corporate filing, etc.).
2. Load `clients/<slug>.yaml` (create if it doesn't exist).
3. Extract entity details from the document and map to YAML fields.
4. Show the user what was extracted and ask for confirmation.
5. Save the updated YAML.

**Use LOCAL model for extraction** — the document likely contains sensitive data.
If the document is too complex for the local model, use the `legal-doc-anonymizer`
pipeline to anonymize first, then extract from the anonymized version via cloud AI.

## File Format

Client files use YAML format. See `clients/_template.yaml` for the full schema.
Key sections: identity, address, contacts, signatories, preferences, banking,
related entities, matters, counterparties, notes.

## Notes

- Target size: under 2KB per client for core entity info.
- One file per client *relationship* (the engaging entity). Related entities
  (subsidiaries, affiliates) are nested under `related_entities`.
- If a related entity becomes a separate client, create a new file and
  cross-reference via notes.
