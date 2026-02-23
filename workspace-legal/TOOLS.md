# TOOLS.md - Counsel

## Claude Code CLI (Primary Cloud Tool)

The main delegation target for tasks requiring advanced reasoning.

```bash
# Basic delegation
export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:$PATH
claude --print --dangerously-skip-permissions -p "<prompt>"

# With file access
claude --print --dangerously-skip-permissions --allowedTools "Read,Write,Edit" -p "<prompt>"

# With full tool access (for coding/debugging tasks)
claude --print --dangerously-skip-permissions --allowedTools "Read,Write,Edit,Bash" -p "<prompt>"
```

**Important:** Files must be under `~/` — Claude Code sandbox blocks `/tmp/`.

## LDA (Legal Document Anonymizer)

Local anonymization/deanonymization tool for sensitive documents.

```bash
# Anonymize
~/.openclaw/tools/lda/.venv/bin/python ~/.openclaw/tools/lda/lda_cli.py anonymize \
  --input <file> --output-dir <dir>

# Deanonymize
~/.openclaw/tools/lda/.venv/bin/python ~/.openclaw/tools/lda/lda_cli.py deanonymize \
  --input <file> --mapping <mapping.json> --output <file>
```

## Moonshot API (Fallback)

Fallback when Claude Code CLI is unavailable.

```bash
curl -s https://api.moonshot.ai/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MOONSHOT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"kimi-k2.5","messages":[{"role":"user","content":"<prompt>"}]}'
```

## Other Tools

- Brave Search — for legal research
- macOS textutil — for .doc file conversion
- python-docx — for .docx file handling
