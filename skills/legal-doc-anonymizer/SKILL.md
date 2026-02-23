---
name: legal-doc-anonymizer
description: >
  Anonymize a legal document with local LLM, send to Claude Code for editing,
  then deanonymize. Use when user provides a legal file with editing instructions.
  Maintains Rule 1.6 confidentiality — real client data never reaches cloud AI.
---

# Legal Document Anonymizer Skill

When a user sends a legal document (`.txt`, `.doc`, `.docx`) with editing instructions, follow this exact 6-step pipeline. All anonymization/deanonymization runs locally — real client data never leaves this machine.

## Prerequisites

- LDA tool installed at `~/.openclaw/tools/lda/`
- Local Ollama running with `qwen3:30b-a3b` model
- Claude Code CLI installed and authenticated (`claude` command available)

## Pipeline

### Step 1: Create Job Directory

```bash
JOB_DIR=~/.openclaw/workspace-legal/jobs/lda-$(date +%Y%m%d-%H%M%S)
mkdir -p "$JOB_DIR"
```

### Step 2: Save User's File

Save the user's uploaded file to the job directory as `original.<ext>` (preserving the original extension).

### Step 3: Anonymize (Local LLM)

```bash
~/.openclaw/tools/lda/.venv/bin/python ~/.openclaw/tools/lda/lda_cli.py anonymize \
  --input "$JOB_DIR/original.<ext>" \
  --output-dir "$JOB_DIR"
```

This produces:
- `$JOB_DIR/anonymized.txt` — document with all sensitive info replaced by `{PLACEHOLDER}` tokens
- `$JOB_DIR/mapping.json` — entity mapping table (stays local, never sent to cloud)

**Verify**: Check the JSON output for `"status": "success"` and `"entity_count" > 0`.

### Step 4: Edit with Claude Code (Cloud AI)

Send ONLY the anonymized text to Claude Code for editing. The mapping file NEVER leaves the machine.

**Important**: Files must be under `~/` (home directory) — Claude Code's sandbox blocks `/tmp/`.

```bash
export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:$PATH
claude --print --dangerously-skip-permissions \
  -p "Read $JOB_DIR/anonymized.txt. [INSERT USER'S EDITING INSTRUCTIONS HERE]. CRITICAL: You MUST preserve ALL {PLACEHOLDER} tokens exactly as they appear — do not modify, remove, or expand any placeholder. Write the edited result to $JOB_DIR/edited.txt"
```

**Important**: Always include the instruction to preserve `{PLACEHOLDER}` tokens.

### Step 5: Deanonymize (Local)

```bash
~/.openclaw/tools/lda/.venv/bin/python ~/.openclaw/tools/lda/lda_cli.py deanonymize \
  --input "$JOB_DIR/edited.txt" \
  --mapping "$JOB_DIR/mapping.json" \
  --output "$JOB_DIR/restored.<ext>"
```

**Verify**: Check the JSON output for `"remaining_placeholders": 0`. If any placeholders remain, warn the user.

### Step 6: Return Result

Send `$JOB_DIR/restored.<ext>` back to the user with a summary:
- Document type identified
- Number of entities anonymized
- Number of edits made (from user instructions)
- Whether all placeholders were successfully restored

## Error Handling

- If Ollama is not running: `curl -s http://localhost:11434/api/tags | head -1` to check
- If anonymization finds 0 entities: warn user, ask if they want to proceed without anonymization
- If Claude Code fails: check that `claude` is in PATH and authenticated (run `claude /login` in Terminal.app if needed)
- If deanonymization has remaining placeholders: list them and ask user to review

## Security Notes

- The `mapping.json` file contains the real-to-placeholder mapping. It MUST stay local.
- Never include `mapping.json` content in any cloud API call.
- Job directories can be cleaned up after the user confirms the result: `rm -rf "$JOB_DIR"`
