---
name: cloud-pass-through
description: >
  Delegate non-sensitive tasks to Claude Code CLI for high-quality output.
  Use when the task requires advanced reasoning, long-form writing, complex analysis,
  or code generation that exceeds local model capabilities.
  DO NOT use for tasks involving confidential client information — use legal-doc-anonymizer instead.
---

# Cloud Pass-Through Skill

Counsel acts as a routing agent — receive the user's request, delegate to Claude Code for heavy lifting, then relay the result.

## When to Use

- Legal research that needs deep analysis
- Drafting complex documents (non-confidential templates, memos)
- Code generation, debugging, automation tasks
- Any task where Counsel's local model (Qwen3 30B) struggles with quality

## When NOT to Use

- Tasks involving real client names, addresses, amounts, or other PII
- Confidential legal documents → use `legal-doc-anonymizer` skill instead
- Simple questions Counsel can answer directly

## Process

### Step 1: Classify the Request

Determine if the task involves sensitive client information:
- **YES** → Stop. Use `legal-doc-anonymizer` skill instead.
- **NO** → Proceed with cloud delegation.

### Step 2: Prepare the Prompt

Summarize the user's request into a clear, self-contained prompt for Claude Code.
Include relevant context but strip any incidental PII.

### Step 3: Delegate to Claude Code

```bash
export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:$PATH
claude --print --dangerously-skip-permissions -p "<PREPARED PROMPT>"
```

For tasks requiring file I/O, ensure files are under the home directory (`~/`).

For tasks requiring multiple tools:
```bash
claude --print --dangerously-skip-permissions --allowedTools "Read,Write,Edit,Bash" -p "<PROMPT>"
```

### Step 4: Review and Relay

- Review Claude Code's output for quality and relevance
- Format the response appropriately for the user's channel (Telegram, Signal, etc.)
- If the result is a file, save it and send the file to the user

## Fallback

If Claude Code CLI is unavailable (not authenticated, network issue):
```bash
# Fallback to Moonshot API
curl -s https://api.moonshot.ai/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MOONSHOT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"kimi-k2.5","messages":[{"role":"user","content":"<PROMPT>"}]}'
```

## Examples

**User:** "Draft an NDA template for a consulting engagement"
→ Non-sensitive (template, no real names) → delegate to Claude Code

**User:** "Summarize the key provisions of NY RPC Rule 1.6"
→ Non-sensitive (public legal rules) → delegate to Claude Code

**User:** "Review this contract and add a governing law clause"
→ **SENSITIVE** (real contract) → use legal-doc-anonymizer instead
