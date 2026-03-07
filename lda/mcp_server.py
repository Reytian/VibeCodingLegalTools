#!/usr/bin/env python3
"""
LDA MCP Server — Legal Document Anonymizer

Provides tools for the OpenClaw Counsel agent to anonymize, edit,
and restore legal documents while maintaining Rule 1.6 confidentiality.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "legal-document-anonymizer",
    instructions=(
        "Legal Document Anonymizer tools for processing sensitive legal documents. "
        "Use 'lda_full_pipeline' for end-to-end processing. "
        "Anonymization runs locally with Qwen3.5 35B — real client data never leaves this machine. Deanonymization is mechanical string replacement (no LLM). "
        "For batch processing (multiple files or zips), use batch_submit/batch_status/batch_results. "
        "Use scan_for_pii to self-check outgoing text for PII leaks. "
        "IMPORTANT: Tool outputs are concise summaries. Do NOT read the intermediate files "
        "into the conversation — just relay the final restored_file path to the user."
    ),
)

LDA_DIR = os.path.expanduser("~/.openclaw/tools/lda")
VENV_PYTHON = os.path.join(LDA_DIR, ".venv", "bin", "python")
LDA_CLI = os.path.join(LDA_DIR, "lda_cli.py")
LDA_BATCH = os.path.join(LDA_DIR, "lda_batch.py")
LDA_CACHE = os.path.join(LDA_DIR, "cache")
JOBS_DIR = os.path.expanduser("~/.openclaw/workspace-legal/jobs")
CLIENTS_DIR = os.path.expanduser("~/.openclaw/workspace-legal/clients")
CLAUDE_PATH = "/opt/homebrew/bin/claude"
OLLAMA_URL = "http://127.0.0.1:11434"

# PII regex patterns (shared with anonymizer.py for consistency)
_PII_PATTERNS = [
    ("email", re.compile(r'[\w.+-]+@[\w.-]+\.\w{2,}')),
    ("phone", re.compile(r'\+\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}')),
    ("phone_cn", re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')),
    ("bank_account", re.compile(r"\b\d{16,19}\b")),
    ("ssn", re.compile(r'\b\d{3}-\d{2}-\d{4}\b')),
    ("credit_code", re.compile(r'\b[0-9A-HJ-NP-Z]{2}\d{6}[0-9A-HJ-NP-Z]{10}\b')),
    ("offshore_reg", re.compile(r'\b\d{4}-[A-Z]-\d{4,8}\b')),
    ("id_number_cn", re.compile(r'\b\d{17}[\dXx]\b')),
    ("passport", re.compile(r'\b[A-Z]{1,2}\d{7,8}\b')),
    ("ip_address", re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')),
]


def _create_job_dir() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    job_dir = os.path.join(JOBS_DIR, f"lda-{timestamp}")
    os.makedirs(job_dir, exist_ok=True)
    return job_dir


def _run_lda(args: list[str], timeout: int = 300) -> dict:
    result = subprocess.run(
        [VENV_PYTHON, LDA_CLI] + args,
        capture_output=True, text=True, timeout=timeout, cwd=LDA_DIR,
    )
    if result.returncode != 0:
        return {"status": "error", "message": result.stderr.strip()[:200]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "message": f"Unexpected output: {result.stdout[:200]}"}


def _run_batch(args: list[str], timeout: int = 60) -> dict:
    result = subprocess.run(
        [VENV_PYTHON, LDA_BATCH] + args,
        capture_output=True, text=True, timeout=timeout, cwd=LDA_DIR,
    )
    if result.returncode != 0:
        return {"status": "error", "message": result.stderr.strip()[:200]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "message": f"Unexpected output: {result.stdout[:200]}"}


@mcp.tool()
def anonymize_document(file_path: str) -> str:
    """Anonymize a legal document using local Qwen3.5 35B via Ollama.
    Returns a concise summary with file paths — does NOT return document content.

    Args:
        file_path: Absolute path to the legal document (.txt, .doc, .docx)
    """
    if not os.path.exists(file_path):
        return json.dumps({"status": "error", "message": f"File not found: {file_path}"})

    job_dir = _create_job_dir()
    ext = os.path.splitext(file_path)[1]
    original_path = os.path.join(job_dir, f"original{ext}")
    shutil.copy2(file_path, original_path)

    result = _run_lda(["anonymize", "--input", original_path, "--output-dir", job_dir])
    # Return only essential info, not file contents
    return json.dumps({
        "status": result.get("status", "error"),
        "job_dir": job_dir,
        "anonymized_file": result.get("anonymized_file"),
        "mapping_file": result.get("mapping_file"),
        "entity_count": result.get("entity_count", 0),
        "document_type": result.get("document_type", "Unknown"),
        "message": result.get("message", ""),
    })


@mcp.tool()
def deanonymize_document(
    edited_file_path: str, mapping_file_path: str, output_file_path: str
) -> str:
    """Restore real names and sensitive data in an edited document.

    Args:
        edited_file_path: Path to the edited anonymized text file
        mapping_file_path: Path to the mapping.json from the anonymization step
        output_file_path: Path for the restored output file
    """
    result = _run_lda([
        "deanonymize",
        "--input", edited_file_path,
        "--mapping", mapping_file_path,
        "--output", output_file_path,
    ], timeout=120)
    stats = result.get("stats", {})
    return json.dumps({
        "status": result.get("status", "error"),
        "output_file": result.get("output_file", output_file_path),
        "remaining_placeholders": stats.get("remaining_placeholders", "?"),
        "message": result.get("message", ""),
    })


@mcp.tool()
def edit_with_claude_code(
    anonymized_file_path: str, instructions: str, output_file_path: str
) -> str:
    """Send an anonymized document to Claude Code CLI for editing.
    ONLY anonymized text is sent to cloud AI. Mapping never leaves the machine.

    Args:
        anonymized_file_path: Path to the anonymized.txt file
        instructions: The user's editing instructions
        output_file_path: Path to write the edited result
    """
    prompt = (
        f"Read {anonymized_file_path}. {instructions} "
        f"CRITICAL: You MUST preserve ALL {{PLACEHOLDER}} tokens exactly as they appear — "
        f"do not modify, remove, or expand any placeholder. "
        f"Write the edited result to {output_file_path}"
    )

    env = os.environ.copy()
    env["PATH"] = f"/opt/homebrew/bin:/opt/homebrew/sbin:{env.get('PATH', '')}"

    try:
        result = subprocess.run(
            [CLAUDE_PATH, "--print", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True, text=True, timeout=300, env=env,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "message": "Claude Code timed out after 300s"})
    except FileNotFoundError:
        return json.dumps({"status": "error", "message": f"Claude Code CLI not found at {CLAUDE_PATH}"})

    if result.returncode != 0:
        return json.dumps({"status": "error", "message": f"Claude Code failed: {result.stderr[:200]}"})

    if os.path.exists(output_file_path):
        return json.dumps({"status": "success", "output_file": output_file_path})
    else:
        return json.dumps({"status": "error", "message": "Claude Code did not create the output file"})


@mcp.tool()
def lda_full_pipeline(file_path: str, instructions: str) -> str:
    """Full end-to-end pipeline: anonymize, edit with Claude Code, deanonymize.
    Use this when a user sends a legal document with editing instructions.

    The entire workflow maintains Rule 1.6 confidentiality:
      1. Anonymize locally with Qwen3.5 (sensitive data never leaves machine)
      2. Send ONLY the anonymized version to Claude Code for editing
      3. Restore real names/data locally from the mapping

    IMPORTANT: Returns only a summary and the restored file path.
    Send the restored file to the user — do NOT read it into the conversation.

    Args:
        file_path: Absolute path to the legal document (.txt, .doc, .docx)
        instructions: The user's editing instructions
    """
    # Step 1: Anonymize
    anon_result = json.loads(anonymize_document(file_path))
    if anon_result.get("status") != "success":
        return json.dumps({"status": "error", "step": "anonymize", "message": anon_result.get("message", "Anonymization failed")})

    job_dir = anon_result["job_dir"]
    anonymized_file = anon_result["anonymized_file"]
    mapping_file = anon_result["mapping_file"]

    # Step 2: Edit with Claude Code
    edited_file = os.path.join(job_dir, "edited.txt")
    edit_result = json.loads(edit_with_claude_code(anonymized_file, instructions, edited_file))
    if edit_result.get("status") != "success":
        return json.dumps({"status": "error", "step": "claude_edit", "message": edit_result.get("message", "Editing failed"), "job_dir": job_dir})

    # Step 3: Deanonymize
    ext = os.path.splitext(file_path)[1] or ".txt"
    restored_file = os.path.join(job_dir, f"restored{ext}")
    deanon_result = json.loads(deanonymize_document(edited_file, mapping_file, restored_file))

    remaining = deanon_result.get("remaining_placeholders", 0)
    summary = {
        "status": "success",
        "restored_file": restored_file,
        "job_dir": job_dir,
        "entity_count": anon_result.get("entity_count", 0),
        "document_type": anon_result.get("document_type", "Unknown"),
    }
    if remaining and remaining != 0:
        summary["warning"] = f"{remaining} placeholders could not be restored — please review."
    return json.dumps(summary)


@mcp.tool()
def batch_submit(file_path: str) -> str:
    """Submit a file or zip archive for batch anonymization.
    Use this for multiple documents — returns a batch_id for tracking.

    Args:
        file_path: Absolute path to a document (.txt, .doc, .docx, .pdf) or .zip archive
    """
    if not os.path.exists(file_path):
        return json.dumps({"status": "error", "message": f"File not found: {file_path}"})

    result = _run_batch(["submit", file_path])
    return json.dumps(result)


@mcp.tool()
def batch_status(batch_id: str) -> str:
    """Check progress of a batch of documents. Returns total/completed/failed counts
    and an all_done flag.

    Args:
        batch_id: The batch ID returned by batch_submit
    """
    result = _run_batch(["batch-status", batch_id])
    return json.dumps(result)


@mcp.tool()
def batch_results(batch_id: str) -> str:
    """Get results for all completed jobs in a batch.
    Only call this after batch_status shows all_done=true.

    Args:
        batch_id: The batch ID returned by batch_submit
    """
    # First get the batch status to find all job IDs
    status_result = _run_batch(["batch-status", batch_id])
    if "error" in status_result:
        return json.dumps(status_result)

    jobs = status_result.get("jobs", [])
    results = []
    for job in jobs:
        if job.get("status") == "completed":
            job_result = _run_batch(["result", job["job_id"]])
            results.append(job_result)
        elif job.get("status") == "failed":
            results.append({
                "job_id": job["job_id"],
                "status": "failed",
                "filename": job.get("filename", "unknown"),
            })

    return json.dumps({
        "batch_id": batch_id,
        "total": len(jobs),
        "results": results,
    })


@mcp.tool()
def batch_retry_failed(batch_id: str) -> str:
    """Re-submit all failed jobs in a batch. Returns new job IDs.

    Args:
        batch_id: The batch ID to retry failed jobs from
    """
    status_result = _run_batch(["batch-status", batch_id])
    if "error" in status_result:
        return json.dumps(status_result)

    jobs = status_result.get("jobs", [])
    failed_jobs = [j for j in jobs if j.get("status") == "failed"]

    if not failed_jobs:
        return json.dumps({"status": "ok", "message": "No failed jobs to retry", "batch_id": batch_id})

    resubmitted = []
    for job in failed_jobs:
        # Read the original job's status to get the input file
        job_dir = os.path.join(LDA_DIR, "jobs", job["job_id"])
        status_file = os.path.join(job_dir, "status.json")
        if not os.path.exists(status_file):
            continue

        with open(status_file) as f:
            job_data = json.load(f)

        # Find the input file in the job directory
        input_files = [f for f in os.listdir(job_dir) if f.startswith("input.")]
        if not input_files:
            continue

        input_path = os.path.join(job_dir, input_files[0])
        result = _run_batch(["submit", input_path])
        if "job_id" in result:
            resubmitted.append({
                "original_job_id": job["job_id"],
                "new_job_id": result["job_id"],
            })

    return json.dumps({
        "batch_id": batch_id,
        "failed_count": len(failed_jobs),
        "resubmitted": len(resubmitted),
        "jobs": resubmitted,
    })


@mcp.tool()
def scan_for_pii(text: str) -> str:
    """Scan arbitrary text for potential PII matches using regex patterns.
    Use this to self-check before sending long text to users or cloud APIs.

    Returns any detected PII patterns. An empty matches list means no PII found.

    Args:
        text: The text to scan for PII
    """
    matches = []
    for pii_type, pattern in _PII_PATTERNS:
        for match in pattern.finditer(text):
            matched_text = match.group(0)
            # Skip common false positives for IP addresses (version numbers, etc.)
            if pii_type == "ip_address":
                parts = matched_text.split(".")
                if all(0 <= int(p) <= 255 for p in parts):
                    # Only flag non-loopback, non-broadcast
                    if matched_text in ("0.0.0.0", "127.0.0.1", "255.255.255.255"):
                        continue
                else:
                    continue

            context_start = max(0, match.start() - 20)
            context_end = min(len(text), match.end() + 20)
            matches.append({
                "type": pii_type,
                "value": matched_text,
                "position": match.start(),
                "context": text[context_start:context_end],
            })

    return json.dumps({
        "pii_found": len(matches) > 0,
        "match_count": len(matches),
        "matches": matches,
    })


def _compute_hash(file_path: str) -> str:
    """SHA-256 hash (first 12 hex chars) of file content."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


@mcp.tool()
def check_document_cache(file_path: str) -> str:
    """Check if a document has already been anonymized (by content hash).
    Returns cached result paths if found, or cache_hit=false if not.
    Use this before submitting a document to LDA to avoid re-processing.

    Args:
        file_path: Absolute path to the document to check
    """
    if not os.path.exists(file_path):
        return json.dumps({"status": "error", "message": f"File not found: {file_path}"})

    content_hash = _compute_hash(file_path)
    cache_entry = os.path.join(LDA_CACHE, content_hash)

    if os.path.isdir(cache_entry):
        anon = os.path.join(cache_entry, "anonymized.txt")
        mapping = os.path.join(cache_entry, "mapping.json")
        meta_path = os.path.join(cache_entry, "metadata.json")
        if os.path.exists(anon) and os.path.exists(mapping):
            meta = {}
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
            return json.dumps({
                "cache_hit": True,
                "content_hash": content_hash,
                "anonymized_file": anon,
                "mapping_file": mapping,
                "cached_at": meta.get("cached_at", "unknown"),
            })

    # Also check client doc caches
    if os.path.isdir(CLIENTS_DIR):
        for client_dir in os.listdir(CLIENTS_DIR):
            docs_dir = os.path.join(CLIENTS_DIR, client_dir, "docs", content_hash)
            if os.path.isdir(docs_dir):
                anon = os.path.join(docs_dir, "anonymized.txt")
                mapping = os.path.join(docs_dir, "mapping.json")
                if os.path.exists(anon) and os.path.exists(mapping):
                    return json.dumps({
                        "cache_hit": True,
                        "content_hash": content_hash,
                        "client": client_dir,
                        "anonymized_file": anon,
                        "mapping_file": mapping,
                    })

    return json.dumps({"cache_hit": False, "content_hash": content_hash})


@mcp.tool()
def cache_client_document(job_id: str, client_slug: str) -> str:
    """Store an anonymized document in a client's local doc cache.
    Call this after LDA completes so the same file won't need re-anonymization.

    Args:
        job_id: The LDA job ID (e.g., "lda-20260304-150628-4qr1")
        client_slug: Client identifier matching the clients/*.yaml file (e.g., "acme-corp")
    """
    # Find the job directory
    job_dir = os.path.join(LDA_DIR, "jobs", job_id)
    if not os.path.isdir(job_dir):
        return json.dumps({"status": "error", "message": f"Job not found: {job_id}"})

    status_path = os.path.join(job_dir, "status.json")
    if not os.path.exists(status_path):
        return json.dumps({"status": "error", "message": "No status.json in job"})

    with open(status_path) as f:
        status_data = json.load(f)

    output_dir = status_data.get("output_dir", job_dir)
    anon_file = os.path.join(output_dir, "anonymized.txt")
    mapping_file = os.path.join(output_dir, "mapping.json")

    if not os.path.exists(anon_file) or not os.path.exists(mapping_file):
        return json.dumps({"status": "error", "message": "Job not completed or missing output files"})

    # Compute content hash from original input
    input_files = [f for f in os.listdir(job_dir) if f.startswith("input.")]
    if not input_files:
        return json.dumps({"status": "error", "message": "Input file not found in job dir"})

    input_path = os.path.join(job_dir, input_files[0])
    content_hash = _compute_hash(input_path)

    # Create client docs directory
    client_docs = os.path.join(CLIENTS_DIR, client_slug, "docs", content_hash)
    os.makedirs(client_docs, exist_ok=True)

    # Copy files
    shutil.copy2(anon_file, os.path.join(client_docs, "anonymized.txt"))
    shutil.copy2(mapping_file, os.path.join(client_docs, "mapping.json"))

    # Write metadata
    meta = {
        "content_hash": content_hash,
        "job_id": job_id,
        "client_slug": client_slug,
        "cached_at": datetime.now().isoformat(),
        "source_filename": status_data.get("input_filename", "unknown"),
        "safe_filename": status_data.get("safe_filename", "unknown"),
    }
    with open(os.path.join(client_docs, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)

    return json.dumps({
        "status": "success",
        "client_slug": client_slug,
        "content_hash": content_hash,
        "cache_path": client_docs,
        "message": f"Cached in clients/{client_slug}/docs/{content_hash}/",
    })


@mcp.tool()
def check_lda_status() -> str:
    """Check if all LDA dependencies are available and working."""
    checks = {}
    checks["lda_cli"] = os.path.exists(LDA_CLI)
    checks["venv"] = os.path.exists(VENV_PYTHON)

    try:
        r = subprocess.run(["curl", "-s", f"{OLLAMA_URL}/api/tags"],
                           capture_output=True, text=True, timeout=5)
        tags = json.loads(r.stdout)
        models = [m["name"] for m in tags.get("models", [])]
        checks["ollama"] = True
        checks["qwen3"] = any("qwen3" in m for m in models)
        checks["available_models"] = models
    except Exception:
        checks["ollama"] = False
        checks["qwen3"] = False

    env = os.environ.copy()
    env["PATH"] = f"/opt/homebrew/bin:/opt/homebrew/sbin:{env.get('PATH', '')}"
    try:
        r = subprocess.run([CLAUDE_PATH, "--version"],
                           capture_output=True, text=True, timeout=10, env=env)
        checks["claude_code"] = r.returncode == 0
    except Exception:
        checks["claude_code"] = False

    checks["all_ok"] = all([checks.get("lda_cli"), checks.get("venv"),
                            checks.get("ollama"), checks.get("qwen3"),
                            checks.get("claude_code")])
    return json.dumps(checks)


if __name__ == "__main__":
    mcp.run()
