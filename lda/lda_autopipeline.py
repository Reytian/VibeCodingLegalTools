#!/usr/bin/env python3
"""
LDA Auto-Pipeline Runner

Runs every 5 minutes via LaunchAgent. For each completed LDA batch:
1. If batch has saved instructions -> run CC pipeline -> deliver results via Signal
2. If no instructions -> send notification to user

Pipeline: group by folder -> CC (all docs in folder together) -> deanonymize -> format -> deliver
Supports related_batches for cross-batch grouping into a single unified memo.
"""

import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

JOBS_DIR = Path.home() / ".openclaw/tools/lda/jobs"
META_DIR = JOBS_DIR / ".batch_meta"
OUTPUT_DIR = Path.home() / ".openclaw/tools/lda/output"
LOCK_FILE = JOBS_DIR / ".pipeline.lock"
PROCESSED_FILE = JOBS_DIR / ".processed_batches"
NOTIFIED_FILE = JOBS_DIR / ".notified_batches"
LOG_FILE = JOBS_DIR / ".autopipeline.log"
VENV_PYTHON = str(Path.home() / ".openclaw/tools/lda/.venv/bin/python")
LDA_CLI = str(Path.home() / ".openclaw/tools/lda/lda_cli.py")
LEGAL_REF = str(Path.home() / ".openclaw/tools/legal-reference.docx")
MEMO_TEMPLATES = str(Path.home() / ".openclaw/tools/memo-templates.md")
SIGNAL_RPC = "http://127.0.0.1:8080/api/v1/rpc"
USER_NUMBER = "+12063498761"
ENV = {**os.environ, "PATH": "/opt/homebrew/bin:/opt/homebrew/sbin:" + os.environ.get("PATH", "")}

PROGRESS_MIN_INTERVAL = 60

# Legal memo prompt template for CC
# The template reads the memo-templates.md file for structure guidance
MEMO_TEMPLATE_FILE = str(Path.home() / ".openclaw/skills/legal-memo/references/template.md")
DRAFTING_GUIDE_FILE = str(Path.home() / ".openclaw/skills/legal-memo/drafting-guide.md")
DD_SKILL_FILE = str(Path.home() / ".openclaw/skills/legal-due-diligence/SKILL.md")

def build_memo_prompt(instructions, file_list, section_instructions, date):
    """Build the CC prompt for memo generation, reading skill files at runtime."""
    # Read the drafting guide (editable training instructions)
    drafting_guidance = ""
    guide_path = Path(DRAFTING_GUIDE_FILE)
    if guide_path.exists():
        drafting_guidance = (
            "## Drafting Guide\n\n"
            "Follow these drafting instructions carefully:\n\n"
            + guide_path.read_text() + "\n\n"
        )

    # Read the DD review skill for analysis standards
    dd_guidance = ""
    dd_path = Path(DD_SKILL_FILE)
    if dd_path.exists():
        dd_guidance = (
            "## Due Diligence Review Standard\n\n"
            "Follow these analysis standards for document review:\n\n"
            + dd_path.read_text() + "\n\n"
        )

    # Read the memo template for structure reference
    template_guidance = ""
    template_path = Path(MEMO_TEMPLATE_FILE)
    if template_path.exists():
        template_guidance = (
            "## Memo Template Reference\n\n"
            "Follow the Office Memo structure from this template file: "
            + str(template_path) + "\n"
            "Read the file and follow its heading structure, section ordering, and formatting conventions exactly.\n\n"
        )
    else:
        alt_path = Path(MEMO_TEMPLATES)
        if alt_path.exists():
            template_guidance = (
                "## Memo Template Reference\n\n"
                "Follow the Office Memo structure from: " + str(alt_path) + "\n"
                "Read the file and use the Office Memo (Internal) template.\n\n"
            )

    return (
        "You are drafting a legal memorandum analyzing the documents provided.\n\n"
        + drafting_guidance
        + dd_guidance
        + "## Instructions from the attorney:\n"
        + instructions + "\n\n"
        "## Documents to analyze:\n"
        + file_list + "\n\n"
        + template_guidance
        + "## Memo Header\n\n"
        "Use this exact header:\n\n"
        "MEMORANDUM\n\n"
        "TO:\n"
        "[Leave as placeholder -- will be filled by attorney]\n\n"
        "FROM:\n"
        "Haotian Yi, Attorney -- Haotian Legal\n\n"
        "DATE:\n"
        + date + "\n\n"
        "RE:\n"
        "[Descriptive subject line based on the analysis]\n\n"
        "## Section Structure\n\n"
        "I. Executive Summary\n\n"
        "[1-2 paragraph overview of key findings across ALL document categories.]\n\n"
        "**Bottom line:** [One-sentence actionable recommendation.]\n\n"
        + section_instructions + "\n\n"
        "[Final section]. Recommendations / Next Steps\n\n"
        "[Consolidated action items, prioritized.]\n\n"
        "Please do not hesitate to reach out if you have any questions regarding the foregoing.\n\n"
        "Very truly yours,\n\n"
        "___________________________\n"
        "Haotian Yi\n"
        "Attorney\n"
        "Haotian Legal\n\n"
        "## CRITICAL RULES:\n"
        "- Preserve ALL {PLACEHOLDER} tokens exactly as they appear\n"
        "- Do NOT invent or guess real names\n"
        "- Use Roman numeral section headings (I., II., III.)\n"
        "- Use markdown ## for section headings so pandoc can style them\n"
        "- Your response must start IMMEDIATELY with \"MEMORANDUM\" -- no preamble, no commentary\n"
    )


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(ts + " " + msg + "\n")


def send_signal(message, attachments=None):
    params = {"recipient": USER_NUMBER, "message": message}
    if attachments:
        params["attachments"] = attachments
    payload = json.dumps({"jsonrpc": "2.0", "method": "send", "params": params, "id": 1}).encode()
    req = urllib.request.Request(SIGNAL_RPC, data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read())
    except Exception as e:
        log("Signal send failed: " + str(e))
        return None


def progress_bar(done, total, width=20):
    if total == 0:
        return "[" + "\u2591" * width + "] 0%"
    pct = done / total
    filled = int(width * pct)
    return "[" + "\u2588" * filled + "\u2591" * (width - filled) + "] " + str(int(pct * 100)) + "%"


def format_duration(seconds):
    if seconds < 60:
        return str(int(seconds)) + "s"
    elif seconds < 3600:
        m, s = int(seconds // 60), int(seconds % 60)
        return (str(m) + "m " + str(s) + "s") if s else (str(m) + "m")
    else:
        h, m = int(seconds // 3600), int((seconds % 3600) // 60)
        return (str(h) + "h " + str(m) + "m") if m else (str(h) + "h")


def estimate_remaining(elapsed, done, total):
    if done == 0:
        return "calculating..."
    return format_duration((elapsed / done) * (total - done))


def get_completed_batches():
    batches = {}
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir() or job_dir.name.startswith(".") or job_dir.name.startswith("unzip"):
            continue
        status_file = job_dir / "status.json"
        if not status_file.exists():
            continue
        try:
            data = json.loads(status_file.read_text())
            bid = data.get("batch_id", "")
            if not bid:
                continue
            if bid not in batches:
                batches[bid] = {"jobs": [], "total": 0, "completed": 0, "failed": 0}
            batches[bid]["total"] += 1
            batches[bid]["jobs"].append(data)
            if data.get("status") == "completed":
                batches[bid]["completed"] += 1
            elif data.get("status") == "failed":
                batches[bid]["failed"] += 1
        except Exception:
            pass
    return {bid: info for bid, info in batches.items()
            if info["completed"] + info["failed"] == info["total"] and info["total"] > 0}


def is_in_file(filepath, value):
    return filepath.exists() and value in filepath.read_text()


def append_to_file(filepath, value):
    with open(filepath, "a") as f:
        f.write(value + "\n")


def get_batch_instructions(batch_id):
    meta_file = META_DIR / (batch_id + ".json")
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text())
        except Exception:
            pass
    return None


def check_cc_login():
    try:
        result = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", "-p", "Reply with just the word OK"],
            capture_output=True, text=True, timeout=60, env=ENV,
        )
        if "login" in result.stderr.lower() or "not logged in" in result.stdout.lower():
            return False
        return result.returncode == 0 and "OK" in result.stdout
    except Exception:
        return False


def extract_folder(input_file_path):
    """Extract the folder path from the job's input_file path.

    For grouped batches, returns the full relative path under unzip dir.
    Example: .../unzip-xxx/TECHNOLOGY & IP/Binar Code/file.pdf -> 'TECHNOLOGY & IP/Binar Code'
             .../unzip-xxx/EXPIRED/file.pdf -> 'EXPIRED'
             .../file.pdf -> '' (root)
    """
    parts = Path(input_file_path).parts
    for i, part in enumerate(parts):
        if part.startswith("unzip-"):
            rel_parts = parts[i + 1:-1]
            if rel_parts:
                return "/".join(rel_parts)
            return ""
    return ""


def extract_top_folder(input_file_path):
    """Extract just the top-level folder name (first level under unzip dir).

    Example: .../unzip-xxx/TECHNOLOGY & IP/subfolder/file.pdf -> 'TECHNOLOGY & IP'
             .../unzip-xxx/file.pdf -> ''
    """
    parts = Path(input_file_path).parts
    for i, part in enumerate(parts):
        if part.startswith("unzip-"):
            rel_parts = parts[i + 1:-1]
            if rel_parts:
                return rel_parts[0]
            return ""
    return ""


def group_jobs_by_folder(jobs):
    """Group completed jobs by their folder within the zip."""
    groups = defaultdict(list)
    for job in jobs:
        if job.get("status") != "completed":
            continue
        folder = extract_folder(job.get("input_file", ""))
        groups[folder].append(job)
    return dict(groups)


def group_jobs_by_top_folder(jobs):
    """Group completed jobs by top-level folder (for cross-batch grouping)."""
    groups = defaultdict(list)
    for job in jobs:
        if job.get("status") != "completed":
            continue
        folder = extract_top_folder(job.get("input_file", ""))
        groups[folder or "(ungrouped)"].append(job)
    return dict(groups)


def merge_mappings(jobs):
    """Merge entity mappings from multiple jobs into one.

    Outputs the format expected by lda_cli.py deanonymize:
    {"mappings": {"{PERSON_1}": {"value": "...", "type": "..."}}, "replacement_log": [...]}
    """
    merged_mappings = {}
    merged_log = []
    for job in jobs:
        mapping_file = JOBS_DIR / job["job_id"] / "mapping.json"
        if not mapping_file.exists():
            continue
        try:
            raw = json.loads(mapping_file.read_text())
            # Collect replacement_log entries
            for entry in raw.get("replacement_log", []):
                merged_log.append(entry)
            # Merge mappings
            entries = raw.get("mappings", raw)
            for placeholder, info in entries.items():
                if placeholder in ("metadata", "replacement_log"):
                    continue
                if isinstance(info, dict):
                    real_value = info.get("value", "")
                    entry_type = info.get("type", "unknown")
                else:
                    real_value = str(info)
                    entry_type = "unknown"
                if placeholder in merged_mappings:
                    existing_val = merged_mappings[placeholder].get("value", "")
                    if real_value and real_value not in existing_val:
                        merged_mappings[placeholder]["value"] = existing_val + " / " + real_value
                else:
                    merged_mappings[placeholder] = {"value": real_value, "type": entry_type}
        except Exception:
            pass
    return {"mappings": merged_mappings, "replacement_log": merged_log}


def remap_placeholders(anon_text, mapping_data, job_suffix):
    """Rename placeholders to be globally unique by adding job suffix.

    {PERSON_1} -> {PERSON_1_J01} to avoid cross-job conflicts.
    """
    remapped_text = anon_text
    remapped_mapping = {}
    remapped_log = []

    mappings = mapping_data.get("mappings", mapping_data)
    for placeholder, info in mappings.items():
        if not placeholder.startswith("{") or placeholder in ("metadata", "replacement_log"):
            continue
        # {PERSON_1} -> {PERSON_1_J01}
        new_placeholder = placeholder[:-1] + "_" + job_suffix + "}"
        remapped_text = remapped_text.replace(placeholder, new_placeholder)
        if isinstance(info, dict):
            remapped_mapping[new_placeholder] = info
        else:
            remapped_mapping[new_placeholder] = {"value": str(info), "type": "unknown"}

    for entry in mapping_data.get("replacement_log", []):
        new_entry = dict(entry)
        old_ph = entry.get("placeholder", "")
        if old_ph.startswith("{") and old_ph.endswith("}"):
            new_entry["placeholder"] = old_ph[:-1] + "_" + job_suffix + "}"
        remapped_log.append(new_entry)

    return remapped_text, {"mappings": remapped_mapping, "replacement_log": remapped_log}



def process_folder_group(folder_name, jobs, instructions, batch_output):
    """Process all files in a folder group as a single CC call."""
    safe_folder = folder_name.replace("/", "_").replace(" ", "_") or "root"

    anon_files = []
    for job in jobs:
        anon_file = JOBS_DIR / job["job_id"] / "anonymized.txt"
        if anon_file.exists():
            anon_files.append((job["job_id"], anon_file, job.get("input_filename", "unknown")))

    if not anon_files:
        return {"folder": folder_name, "status": "skipped", "reason": "no anonymized files"}

    file_refs = []
    for i, (job_id, anon_path, orig_name) in enumerate(anon_files, 1):
        file_refs.append("Document " + str(i) + " (job " + job_id + "): " + str(anon_path))

    prompt = (
        "You are analyzing a group of " + str(len(anon_files)) + " related documents "
        "from the folder '" + folder_name + "'. "
        "These documents are all connected and MUST be analyzed together as a set.\n\n"
        "Read ALL of the following files:\n"
        + "\n".join("- " + ref for ref in file_refs)
        + "\n\n" + instructions + "\n\n"
        "IMPORTANT:\n"
        "- Analyze all documents in conjunction, not individually.\n"
        "- Identify relationships, cross-references, and dependencies between documents.\n"
        "- Provide conclusions about the group as a whole.\n"
        "- Preserve ALL {PLACEHOLDER} tokens exactly as they appear.\n"
        "- Output ONLY the analysis/memo content, no preamble."
    )

    cc_output = batch_output / (safe_folder + "_cc.md")
    try:
        cc_result = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True, text=True, timeout=600, env=ENV,
        )
        if cc_result.returncode != 0:
            log("CC failed for folder " + folder_name + ": " + cc_result.stderr[:200])
            return {"folder": folder_name, "status": "cc_failed"}
        cc_output.write_text(cc_result.stdout)
    except subprocess.TimeoutExpired:
        log("CC timeout for folder " + folder_name)
        return {"folder": folder_name, "status": "cc_timeout"}

    merged = merge_mappings(jobs)
    merged_mapping_file = batch_output / (safe_folder + "_mapping.json")
    merged_mapping_file.write_text(json.dumps(merged, indent=2))

    deanon_file = batch_output / (safe_folder + "_final.md")
    try:
        subprocess.run(
            [VENV_PYTHON, LDA_CLI, "deanonymize",
             "--input", str(cc_output),
             "--mapping", str(merged_mapping_file),
             "--output", str(deanon_file)],
            capture_output=True, text=True, timeout=60,
        )
        if not deanon_file.exists():
            return {"folder": folder_name, "status": "deanon_failed"}
    except Exception:
        return {"folder": folder_name, "status": "deanon_failed"}

    docx_file = batch_output / (safe_folder + ".docx")
    try:
        subprocess.run(
            ["pandoc", str(deanon_file), "-o", str(docx_file),
             "--reference-doc=" + LEGAL_REF],
            capture_output=True, text=True, timeout=60, env=ENV,
        )
    except Exception:
        pass

    if docx_file.exists():
        format_docx(docx_file)
    output_file = str(docx_file) if docx_file.exists() else str(deanon_file)
    return {"folder": folder_name, "status": "success", "file": output_file, "doc_count": len(anon_files)}



def format_docx(docx_path):
    """Post-process .docx to fix formatting: center MEMORANDUM title, fix spacing."""
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
    except ImportError:
        # Try LDA venv
        import subprocess, sys
        subprocess.run([VENV_PYTHON, "-c", 
            "from docx import Document; "
            "from docx.enum.text import WD_ALIGN_PARAGRAPH; "
            "from docx.shared import Pt; "
            "doc = Document('" + str(docx_path) + "'); "
            "[setattr(p.paragraph_format, 'alignment', WD_ALIGN_PARAGRAPH.CENTER) or "
            " setattr(p.runs[0].font, 'size', Pt(16)) or "
            " setattr(p.runs[0].font, 'bold', True) "
            " for p in doc.paragraphs[:1] if 'MEMORANDUM' in p.text and p.runs]; "
            "doc.save('" + str(docx_path) + "')"],
            capture_output=True, timeout=30)
        return
    doc = Document(str(docx_path))
    for p in doc.paragraphs:
        if "MEMORANDUM" in p.text and p.text.strip() == "MEMORANDUM":
            p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                run.font.size = Pt(16)
                run.font.bold = True
            break
    doc.save(str(docx_path))

def process_unified_memo(all_batch_ids, all_jobs, instructions, output_dir):
    """Process multiple batches as a single unified memo using legal-memo format.

    Groups all files by top-level folder, feeds everything to CC in one call
    with the Office Memo template, then deanonymizes and formats.
    """
    group_id = all_batch_ids[0]  # Use primary batch as group identifier
    batch_output = output_dir / ("group-" + group_id)
    batch_output.mkdir(parents=True, exist_ok=True)

    completed_jobs = [j for j in all_jobs if j.get("status") == "completed"]
    folder_groups = group_jobs_by_top_folder(completed_jobs)

    # Build file list for the prompt
    file_list_parts = []
    section_instructions_parts = []
    section_num = 2  # Start at II (I is Executive Summary)

    all_remapped_mappings = {"mappings": {}, "replacement_log": []}
    job_counter = 0
    for folder_name in sorted(folder_groups.keys()):
        jobs = folder_groups[folder_name]
        file_list_parts.append("\n### " + folder_name + " (" + str(len(jobs)) + " documents)")
        for job in jobs:
            anon_file = JOBS_DIR / job["job_id"] / "anonymized.txt"
            mapping_file = JOBS_DIR / job["job_id"] / "mapping.json"
            if anon_file.exists() and mapping_file.exists():
                job_counter += 1
                job_suffix = "J" + str(job_counter).zfill(2)
                try:
                    anon_text = anon_file.read_text()
                    raw_mapping = json.loads(mapping_file.read_text())
                    remapped_text, remapped_map = remap_placeholders(anon_text, raw_mapping, job_suffix)
                    remapped_file = batch_output / (job_suffix + "_anon.txt")
                    remapped_file.write_text(remapped_text)
                    file_list_parts.append("- " + str(remapped_file) + " (job " + job["job_id"] + ")")
                    all_remapped_mappings["mappings"].update(remapped_map["mappings"])
                    all_remapped_mappings["replacement_log"].extend(remapped_map["replacement_log"])
                except Exception as e:
                    file_list_parts.append("- " + str(anon_file) + " (job " + job["job_id"] + ")")

        roman = _to_roman(section_num)
        section_instructions_parts.append(
            roman + ". " + folder_name + "\n\n"
            "[Analyze ALL documents in the " + folder_name + " category together. "
            "Identify key issues, obligations, risks, and notable provisions. "
            "Use sub-headings for each major issue found.]\n"
        )
        section_num += 1

    file_list = "\n".join(file_list_parts)
    section_instructions = "\n".join(section_instructions_parts)
    today = datetime.now().strftime("%B %d, %Y")

    prompt = build_memo_prompt(instructions, file_list, section_instructions, today)
    prompt += "\n\nRead ALL files listed above before writing the memo."

    total_files = len(completed_jobs)
    send_signal(
        "Unified memo pipeline started\n"
        "Batches: " + ", ".join(all_batch_ids) + "\n"
        "Files: " + str(total_files) + " across " + str(len(folder_groups)) + " categories\n"
        + "\n".join("  " + k + ": " + str(len(v)) + " docs" for k, v in sorted(folder_groups.items()))
        + "\n\nGenerating unified legal memo with CC..."
    )

    # Single CC call for the entire memo
    cc_output = batch_output / "memo_cc.md"
    try:
        cc_result = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True, text=True, timeout=900, env=ENV,  # 15 min for large unified memo
        )
        if cc_result.returncode != 0:
            log("CC failed for unified memo: " + cc_result.stderr[:200])
            send_signal("CC failed for unified memo. Error: " + cc_result.stderr[:200])
            return None
        # Strip any AI preamble before "MEMORANDUM"
        cc_text = cc_result.stdout
        memo_start = cc_text.find("MEMORANDUM")
        if memo_start > 0:
            cc_text = cc_text[memo_start:]
        cc_output.write_text(cc_text)
    except subprocess.TimeoutExpired:
        log("CC timeout for unified memo")
        send_signal("CC timed out generating the unified memo (15 min limit).")
        return None

    send_signal("CC analysis complete. Deanonymizing and formatting...")

    # Use pre-built remapped mappings (no cross-job placeholder conflicts)
    merged_mapping_file = batch_output / "memo_mapping.json"
    merged_mapping_file.write_text(json.dumps(all_remapped_mappings, indent=2))

    # Deanonymize
    deanon_file = batch_output / "memo_final.md"
    try:
        subprocess.run(
            [VENV_PYTHON, LDA_CLI, "deanonymize",
             "--input", str(cc_output),
             "--mapping", str(merged_mapping_file),
             "--output", str(deanon_file)],
            capture_output=True, text=True, timeout=60,
        )
        if not deanon_file.exists():
            send_signal("Deanonymization failed.")
            return None
    except Exception as e:
        log("Deanon failed: " + str(e))
        send_signal("Deanonymization failed: " + str(e))
        return None

    # Fallback: replace any remaining placeholders the deanonymizer missed
    try:
        text = deanon_file.read_text()
        remaining = 0
        for placeholder, info in all_remapped_mappings["mappings"].items():
            value = info.get("value", "") if isinstance(info, dict) else str(info)
            if placeholder in text and value:
                text = text.replace(placeholder, value)
                remaining += 1
        if remaining > 0:
            deanon_file.write_text(text)
            log("Fallback replaced " + str(remaining) + " remaining placeholders")
    except Exception:
        pass

    # Format to .docx
    docx_file = batch_output / "memo.docx"
    try:
        subprocess.run(
            ["pandoc", str(deanon_file), "-o", str(docx_file),
             "--reference-doc=" + LEGAL_REF],
            capture_output=True, text=True, timeout=60, env=ENV,
        )
    except Exception:
        pass

    if docx_file.exists():
        format_docx(docx_file)
    output_file = str(docx_file) if docx_file.exists() else str(deanon_file)
    return {"status": "success", "file": output_file, "total_files": total_files,
            "categories": len(folder_groups)}


def _to_roman(num):
    """Convert integer to Roman numeral."""
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
            (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    result = ""
    for val, numeral in vals:
        while num >= val:
            result += numeral
            num -= val
    return result


def run_pipeline(batch_id, batch_info, instructions):
    """Run CC pipeline grouped by folder with progress updates (single-batch mode)."""
    batch_output = OUTPUT_DIR / batch_id
    batch_output.mkdir(parents=True, exist_ok=True)

    completed_jobs = [j for j in batch_info["jobs"] if j.get("status") == "completed"]
    folder_groups = group_jobs_by_folder(completed_jobs)
    total_groups = len(folder_groups)
    total_files = len(completed_jobs)
    results = []
    start_time = time.time()

    folder_desc = "\n".join(
        "  " + (name or "(root)") + ": " + str(len(jobs)) + " docs"
        for name, jobs in folder_groups.items()
    )
    send_signal(
        "Auto-pipeline started\n"
        "Batch: " + batch_id + "\n"
        "Files: " + str(total_files) + " in " + str(total_groups) + " folder(s)\n"
        + folder_desc + "\n\n"
        "Documents in each folder will be analyzed together.\n"
        + progress_bar(0, total_groups)
    )
    log("Starting pipeline for " + batch_id + " (" + str(total_files) + " files, " + str(total_groups) + " folders)")

    for i, (folder_name, jobs) in enumerate(folder_groups.items()):
        display_name = folder_name or "(root)"
        send_signal(
            progress_bar(i, total_groups) + "\n"
            "Processing folder " + str(i + 1) + "/" + str(total_groups) + ": " + display_name + "\n"
            "Analyzing " + str(len(jobs)) + " documents together with CC..."
        )

        result = process_folder_group(folder_name, jobs, instructions, batch_output)
        results.append(result)

        elapsed = time.time() - start_time
        done = i + 1
        status = "\u2713" if result["status"] == "success" else "\u2717"
        eta = estimate_remaining(elapsed, done, total_groups)

        send_signal(
            progress_bar(done, total_groups) + "\n"
            + status + " " + display_name + " (" + str(result.get("doc_count", "?")) + " docs)\n"
            "Elapsed: " + format_duration(elapsed) + " | ETA: ~" + eta
        )

    return results


def deliver_results(batch_id, results, start_time):
    success_files = [r["file"] for r in results if r.get("status") == "success" and r.get("file")]
    fail_count = len([r for r in results if r.get("status") != "success"])
    total_time = format_duration(time.time() - start_time)

    bar = progress_bar(len(results), len(results))
    msg = (
        bar + "\n"
        "Batch complete: " + str(len(success_files)) + "/" + str(len(results)) + " folders processed\n"
        "Total time: " + total_time
    )
    if fail_count:
        msg += "\n\u2717 " + str(fail_count) + " failed"

    if not success_files:
        send_signal(msg)
        return

    for i in range(0, len(success_files), 5):
        chunk = success_files[i:i + 5]
        chunk_msg = msg if i == 0 else "Files " + str(i + 1) + "-" + str(i + len(chunk)) + " of " + str(len(success_files)) + ":"
        resp = send_signal(chunk_msg, attachments=chunk)
        if resp and resp.get("error"):
            send_signal(chunk_msg + "\nFiles saved at:\n" + "\n".join(chunk))
        time.sleep(1)


def deliver_unified_result(result, batch_ids, start_time):
    """Deliver a unified memo result."""
    total_time = format_duration(time.time() - start_time)
    if not result or result.get("status") != "success":
        send_signal("Unified memo pipeline failed after " + total_time)
        return

    msg = (
        progress_bar(1, 1) + "\n"
        "Unified memo complete\n"
        "Batches: " + str(len(batch_ids)) + " | "
        "Files: " + str(result.get("total_files", "?")) + " | "
        "Categories: " + str(result.get("categories", "?")) + "\n"
        "Total time: " + total_time
    )
    resp = send_signal(msg, attachments=[result["file"]])
    if resp and resp.get("error"):
        send_signal(msg + "\nFile saved at: " + result["file"])


def resolve_batch_groups(batches, all_completed):
    """Resolve batch groups from meta instructions.

    Returns list of:
      - ("single", batch_id, meta) for standalone batches
      - ("group", [batch_ids], meta) for grouped batches (only once per group)
    """
    seen_groups = set()
    result = []

    for batch_id in batches:
        if is_in_file(PROCESSED_FILE, batch_id):
            continue

        meta = get_batch_instructions(batch_id)
        if not meta or not meta.get("instructions"):
            continue

        # Check if this batch points to a primary batch (it's a related batch)
        primary = meta.get("primary_batch")
        if primary:
            # Skip — will be handled when we encounter the primary batch
            continue

        related = meta.get("related_batches", [])
        if related:
            # This is a primary batch with related batches
            group_key = tuple(sorted([batch_id] + related))
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)

            # Check all related batches are complete
            all_ready = all(bid in all_completed for bid in [batch_id] + related)
            if not all_ready:
                continue  # Wait for all to complete

            result.append(("group", [batch_id] + related, meta))
        else:
            result.append(("single", batch_id, meta))

    return result


def main():
    META_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        return

    try:
        all_completed = get_completed_batches()
        items = resolve_batch_groups(all_completed, all_completed)

        for item in items:
            if item[0] == "group":
                _, batch_ids, meta = item
                # Check if primary is already processed
                if is_in_file(PROCESSED_FILE, batch_ids[0]):
                    continue

                if not check_cc_login():
                    if not is_in_file(NOTIFIED_FILE, batch_ids[0]):
                        total = sum(all_completed[bid]["completed"] for bid in batch_ids if bid in all_completed)
                        send_signal(
                            "LDA batch group ready (" + str(total) + " files across "
                            + str(len(batch_ids)) + " batches) "
                            "but Claude Code CLI is not logged in.\n"
                            "Please run `claude /login` on the Mac Mini."
                        )
                        append_to_file(NOTIFIED_FILE, batch_ids[0])
                    continue

                start_time = time.time()
                all_jobs = []
                for bid in batch_ids:
                    if bid in all_completed:
                        all_jobs.extend(all_completed[bid]["jobs"])

                total = sum(all_completed.get(bid, {}).get("completed", 0) for bid in batch_ids)
                log("Starting unified memo for " + str(len(batch_ids)) + " batches (" + str(total) + " files)")

                result = process_unified_memo(batch_ids, all_jobs, meta["instructions"], OUTPUT_DIR)
                deliver_unified_result(result, batch_ids, start_time)

                # Mark all batches as processed
                for bid in batch_ids:
                    append_to_file(PROCESSED_FILE, bid)

                status = "OK" if result and result.get("status") == "success" else "FAILED"
                log("Unified memo done for " + ",".join(batch_ids) + ": " + status)

            elif item[0] == "single":
                _, batch_id, meta = item

                if not check_cc_login():
                    if not is_in_file(NOTIFIED_FILE, batch_id):
                        info = all_completed[batch_id]
                        send_signal(
                            "LDA batch " + batch_id + " ready (" + str(info["completed"]) + " files) "
                            "but Claude Code CLI is not logged in.\n"
                            "Please run `claude /login` on the Mac Mini."
                        )
                        append_to_file(NOTIFIED_FILE, batch_id)
                    continue

                info = all_completed[batch_id]
                start_time = time.time()
                results = run_pipeline(batch_id, info, meta["instructions"])
                deliver_results(batch_id, results, start_time)
                append_to_file(PROCESSED_FILE, batch_id)
                ok = len([r for r in results if r["status"] == "success"])
                log("Pipeline done for " + batch_id + ": " + str(ok) + "/" + str(len(results)) + " folder groups OK")

        # Handle batches with no instructions (notification only)
        for batch_id, info in all_completed.items():
            if is_in_file(PROCESSED_FILE, batch_id) or is_in_file(NOTIFIED_FILE, batch_id):
                continue
            meta = get_batch_instructions(batch_id)
            if meta and (meta.get("instructions") or meta.get("primary_batch")):
                continue  # Has instructions or is part of a group — skip notification
            msg = (
                "LDA batch complete\n"
                "Files: " + str(info["completed"]) + "/" + str(info["total"]) + " anonymized"
            )
            if info["failed"]:
                msg += " (" + str(info["failed"]) + " failed)"
            msg += "\nReply with instructions to start processing."
            send_signal(msg)
            append_to_file(NOTIFIED_FILE, batch_id)
            log("Notified (no instructions) batch " + batch_id)

    except Exception as e:
        log("Autopipeline error: " + str(e))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
