---
name: skill-creator
description: >
  Create or revise OpenClaw skills using Claude Code.
  Use when Haotian asks to create a new skill, modify an existing skill,
  or improve the agent's capabilities. Delegates the actual writing to Claude Code
  since skill creation requires higher reasoning than the local model provides.
---

# Skill Creator

Counsel delegates skill creation and revision to Claude Code, which has the reasoning capability to write well-structured SKILL.md files.

## When to Use

- "Create a skill for..."
- "Add a new capability to..."
- "Update the [skill-name] skill"
- "Make Counsel able to..."

## Process

### Step 1: Gather Requirements

Ask the user (if not already clear):
- What should this skill do?
- When should it trigger?
- What tools/APIs does it need?
- Which workspace should it go in?

### Step 2: Check Existing Skills

```bash
ls ~/.openclaw/workspace-legal/skills/
```

If updating an existing skill, read its current content:
```bash
cat ~/.openclaw/workspace-legal/skills/<skill-name>/SKILL.md
```

### Step 3: Delegate to Claude Code

```bash
export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:$PATH
claude --print --dangerously-skip-permissions -p "Create an OpenClaw skill file (SKILL.md) with the following requirements:

Skill name: <name>
Description: <what it does>
Trigger: <when to activate>
Process: <step-by-step workflow>

The SKILL.md must follow this format:
1. YAML frontmatter with name and description
2. Clear 'When to Use' section
3. Step-by-step 'Process' section with bash commands where needed
4. Error handling section
5. Examples section

Write the complete SKILL.md content to ~/.openclaw/workspace-legal/skills/<name>/SKILL.md"
```

### Step 4: Verify and Report

- Read the created skill file to confirm it was written correctly
- Report the new skill to the user with a summary
- Suggest testing the skill with a sample request

## Example

**User:** "Create a skill that searches case law on CourtListener"

**Counsel's action:**
1. Classify: non-sensitive, skill creation → use this skill
2. Delegate to Claude Code with requirements
3. Claude Code creates `~/.openclaw/workspace-legal/skills/case-law-search/SKILL.md`
4. Report back to user: "Created case-law-search skill. It searches CourtListener API for relevant cases. Try: 'Search for NY cases on attorney-client privilege'"
