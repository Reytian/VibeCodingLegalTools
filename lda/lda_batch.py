#!/usr/bin/env python3
"""
LDA Batch Processor — sequential job queue for long document anonymization.

Jobs are queued and processed one at a time by a background worker.
Submit and status commands return instantly. Queued jobs can be cancelled.

Usage:
  lda_batch.py submit <input-file> [--output-dir <dir>]
  lda_batch.py status <job-id>
  lda_batch.py result <job-id>
  lda_batch.py cancel <job-id>
  lda_batch.py list
  lda_batch.py clean [--days 7]
  lda_batch.py batch-status <batch-id>
  lda_batch.py worker          (internal — started automatically by submit)
"""

import argparse
import json
import os
import shutil
import string
import subprocess
import sys
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
import zipfile

JOBS_DIR = Path(__file__).parent / "jobs"
CACHE_DIR = Path(__file__).parent / "cache"
WORKER_PID_FILE = JOBS_DIR / ".worker.pid"
VENV_PYTHON = Path(__file__).parent / ".venv" / "bin" / "python"
LDA_CLI = Path(__file__).parent / "lda_cli.py"
BATCH_SCRIPT = Path(__file__)
SUPPORTED_EXTENSIONS = {'.txt', '.doc', '.docx', '.pdf'}

OLLAMA_URL = "http://127.0.0.1:11434"
JOB_TIMEOUT = 600  # seconds per job
MAX_RETRIES = 2


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of file content (first 12 hex chars)."""
    import hashlib
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def check_cache(file_path: Path) -> "Path | None":
    """Check if this file has been anonymized before. Returns cache dir or None."""
    if not CACHE_DIR.exists():
        return None
    content_hash = compute_file_hash(file_path)
    cache_entry = CACHE_DIR / content_hash
    if cache_entry.exists() and (cache_entry / "anonymized.txt").exists() and (cache_entry / "mapping.json").exists():
        return cache_entry
    return None


def store_in_cache(file_path: Path, output_dir: Path):
    """Store anonymized result in content-addressed cache."""
    content_hash = compute_file_hash(file_path)
    cache_entry = CACHE_DIR / content_hash
    cache_entry.mkdir(parents=True, exist_ok=True)

    anon_src = output_dir / "anonymized.txt"
    map_src = output_dir / "mapping.json"
    if anon_src.exists():
        shutil.copy2(anon_src, cache_entry / "anonymized.txt")
    if map_src.exists():
        shutil.copy2(map_src, cache_entry / "mapping.json")

    # Write metadata
    meta = {
        "content_hash": content_hash,
        "cached_at": datetime.now().isoformat(),
        "source_file": str(file_path),
        "source_size": file_path.stat().st_size if file_path.exists() else 0,
    }
    with open(cache_entry / "metadata.json", "w") as f:
        json.dump(meta, f, indent=2)


def generate_job_id() -> str:
    now = datetime.now()
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"lda-{now.strftime('%Y%m%d-%H%M%S')}-{suffix}"


def generate_batch_id() -> str:
    now = datetime.now()
    return f"batch-{now.strftime('%Y%m%d-%H%M%S')}"


def get_job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def read_status(job_dir: Path) -> dict:
    status_file = job_dir / "status.json"
    if not status_file.exists():
        return {}
    with open(status_file) as f:
        return json.load(f)


def write_status(job_dir: Path, data: dict):
    with open(job_dir / "status.json", "w") as f:
        json.dump(data, f, indent=2)


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def derive_state(job_dir: Path, status_data: dict) -> str:
    """Derive current job state from status field, PID, and output files."""
    explicit = status_data.get("status")

    # Explicit terminal/queue states
    if explicit == "cancelled":
        return "cancelled"
    if explicit == "queued":
        return "queued"

    # Running: check if PID is still alive
    pid = status_data.get("pid")
    if explicit == "running" and pid and is_pid_alive(pid):
        return "running"

    # Check output files
    output_dir = Path(status_data.get("output_dir", str(job_dir)))
    anon_file = output_dir / "anonymized.txt"
    mapping_file = output_dir / "mapping.json"

    if anon_file.exists() and mapping_file.exists():
        return "completed"

    # Process finished but no output
    log_file = job_dir / "log.txt"
    if log_file.exists():
        log_content = log_file.read_text()
        if "Traceback" in log_content or "Error" in log_content:
            return "failed"

    if explicit == "running" and pid and not is_pid_alive(pid):
        return "failed"

    return explicit or "unknown"


def get_queued_jobs() -> list[tuple[str, dict]]:
    """Return queued jobs sorted by submission time (oldest first)."""
    queued = []
    if not JOBS_DIR.exists():
        return queued
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir() or not job_dir.name.startswith("lda-"):
            continue
        data = read_status(job_dir)
        if data.get("status") == "queued":
            queued.append((job_dir.name, data))
    queued.sort(key=lambda x: x[1].get("submitted_at", ""))
    return queued


def is_worker_running() -> bool:
    """Check if a worker process is alive."""
    if not WORKER_PID_FILE.exists():
        return False
    try:
        pid = int(WORKER_PID_FILE.read_text().strip())
        return is_pid_alive(pid)
    except (ValueError, OSError):
        return False


def ensure_worker():
    """Start the background worker if it's not already running."""
    if is_worker_running():
        return
    log = JOBS_DIR / ".worker.log"
    with open(log, "a") as log_fh:
        proc = subprocess.Popen(
            [str(VENV_PYTHON), str(BATCH_SCRIPT), "worker"],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    WORKER_PID_FILE.write_text(str(proc.pid))


def prewarm_ollama():
    """Send a minimal prompt to Ollama to load the model into memory."""
    try:
        import requests
        lda_model = os.environ.get("LDA_MODEL", "qwen3.5:35b-a3b")
        requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": lda_model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "options": {"num_predict": 1},
            },
            timeout=120,
        )
    except Exception:
        pass  # Best-effort; the actual job will retry if needed


# ── Commands ──────────────────────────────────────────────


def _submit_single(input_path: Path, output_dir: "Path | None", batch_id: str = "") -> dict:
    """Submit a single file to the queue. Returns job info dict."""
    job_id = generate_job_id()
    job_dir = get_job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    # Copy input preserving extension (binary-safe)
    ext = input_path.suffix.lower() or ".txt"
    input_copy = job_dir / f"input{ext}"
    shutil.copy2(input_path, input_copy)

    out = output_dir if output_dir else job_dir

    status_data = {
        "job_id": job_id,
        "status": "queued",
        "input_file": str(input_path),
        "input_filename": input_path.name,
        "safe_filename": f"{job_id}{ext}",
        "output_dir": str(out),
        "submitted_at": datetime.now().isoformat(),
        "input_size": input_path.stat().st_size,
        "retry_count": 0,
    }
    if batch_id:
        status_data["batch_id"] = batch_id

    write_status(job_dir, status_data)

    return {
        "job_id": job_id,
        "filename": f"{job_id}{ext}",
        "input_size_kb": round(input_path.stat().st_size / 1024, 1),
        "output_dir": str(out),
    }


def _extract_zip(zip_path: Path) -> list[Path]:
    """Extract a zip file and return paths to supported documents inside."""
    extract_dir = JOBS_DIR / f"unzip-{zip_path.stem}"
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    found = []
    for f in sorted(extract_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        # Skip hidden/system files (check relative path, not absolute)
        rel = f.relative_to(extract_dir)
        if any(part.startswith(".") or part.startswith("__") for part in rel.parts):
            continue
        found.append(f)
    return found


def cmd_submit(args):
    """Submit file(s) to the queue. Accepts single files or .zip archives."""
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(json.dumps({"error": f"Input file not found: {input_path}"}))
        sys.exit(1)

    output_dir = Path(args.output_dir).resolve() if args.output_dir else None

    # Handle .zip: extract and queue each supported file
    if input_path.suffix.lower() == ".zip":
        try:
            extracted = _extract_zip(input_path)
        except zipfile.BadZipFile:
            print(json.dumps({"error": "Invalid or corrupted zip file"}))
            sys.exit(1)

        if not extracted:
            print(json.dumps({
                "error": "No supported files found in zip",
                "supported": list(SUPPORTED_EXTENSIONS),
            }))
            sys.exit(1)

        batch_id = generate_batch_id()
        jobs = []
        for file_path in extracted:
            info = _submit_single(file_path, output_dir, batch_id=batch_id)
            jobs.append(info)

        ensure_worker()

        queued = get_queued_jobs()
        for job in jobs:
            pos = next(
                (i + 1 for i, (jid, _) in enumerate(queued) if jid == job["job_id"]),
                0,
            )
            job["queue_position"] = pos

        result = {
            "status": "queued",
            "batch_id": batch_id,
            "files_found": len(extracted),
            "jobs": jobs,
            "message": f"{len(extracted)} files extracted and queued for anonymization. Batch ID: {batch_id}",
        }
        print(json.dumps(result, indent=2))
        return

    # Single file
    if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        print(json.dumps({
            "error": f"Unsupported file type: {input_path.suffix}",
            "supported": list(SUPPORTED_EXTENSIONS),
        }))
        sys.exit(1)

    batch_id = getattr(args, "batch_id", None) or ""
    info = _submit_single(input_path, output_dir, batch_id=batch_id)
    ensure_worker()

    queued = get_queued_jobs()
    position = next(
        (i + 1 for i, (jid, _) in enumerate(queued) if jid == info["job_id"]),
        len(queued),
    )

    result = {
        "status": "queued",
        "job_id": info["job_id"],
        "queue_position": position,
        "filename": info["filename"],
        "input_size_kb": info["input_size_kb"],
        "output_dir": info["output_dir"],
        "message": f"Job queued at position {position}. Use lda_batch.py status {info['job_id']} to check progress.",
    }
    if batch_id:
        result["batch_id"] = batch_id
    print(json.dumps(result, indent=2))


def cmd_cancel(args):
    """Cancel a queued job. Only queued (not yet running) jobs can be cancelled."""
    job_dir = get_job_dir(args.job_id)
    if not job_dir.exists():
        print(json.dumps({"error": f"Job not found: {args.job_id}"}))
        sys.exit(1)

    status_data = read_status(job_dir)
    state = derive_state(job_dir, status_data)

    if state != "queued":
        print(json.dumps({
            "error": f"Cannot cancel job with status '{state}'. Only queued jobs can be cancelled.",
            "job_id": args.job_id,
        }))
        sys.exit(1)

    status_data["status"] = "cancelled"
    status_data["cancelled_at"] = datetime.now().isoformat()
    write_status(job_dir, status_data)

    print(json.dumps({
        "status": "cancelled",
        "job_id": args.job_id,
        "message": "Job cancelled. It will not be processed.",
    }))


def cmd_status(args):
    """Check the status of a job."""
    job_dir = get_job_dir(args.job_id)
    if not job_dir.exists():
        print(json.dumps({"error": f"Job not found: {args.job_id}"}))
        sys.exit(1)

    status_data = read_status(job_dir)
    state = derive_state(job_dir, status_data)

    result = {
        "job_id": args.job_id,
        "status": state,
        "submitted_at": status_data.get("submitted_at", ""),
        "filename": status_data.get("safe_filename", args.job_id),
        "input_size_kb": round(status_data.get("input_size", 0) / 1024, 1),
    }

    if status_data.get("batch_id"):
        result["batch_id"] = status_data["batch_id"]

    if state == "queued":
        queued = get_queued_jobs()
        position = next(
            (i + 1 for i, (jid, _) in enumerate(queued) if jid == args.job_id),
            0,
        )
        result["queue_position"] = position

    elif state == "running":
        log_file = job_dir / "log.txt"
        if log_file.exists():
            log_lines = log_file.read_text().strip().split("\n")
            for line in reversed(log_lines):
                if "Processing segment" in line:
                    result["progress"] = line.strip().split("] ")[-1] if "] " in line else line.strip()
                    break

    elif state == "failed":
        log_file = job_dir / "log.txt"
        if log_file.exists():
            log_lines = log_file.read_text().strip().split("\n")
            result["last_log_lines"] = log_lines[-5:]
        error = status_data.get("error")
        if error:
            result["error"] = error

    elif state == "completed":
        result["message"] = "Use 'lda_batch.py result <job-id>' to get output file paths."

    print(json.dumps(result, indent=2))


def cmd_result(args):
    """Get results of a completed job."""
    job_dir = get_job_dir(args.job_id)
    if not job_dir.exists():
        print(json.dumps({"error": f"Job not found: {args.job_id}"}))
        sys.exit(1)

    status_data = read_status(job_dir)
    state = derive_state(job_dir, status_data)

    if state != "completed":
        print(json.dumps({
            "error": f"Job is not completed (status: {state})",
            "job_id": args.job_id,
        }))
        sys.exit(1)

    output_dir = Path(status_data.get("output_dir", str(job_dir)))
    anon_file = output_dir / "anonymized.txt"
    mapping_file = output_dir / "mapping.json"

    mapping_data = {}
    if mapping_file.exists():
        with open(mapping_file) as f:
            mapping_data = json.load(f)

    # Parse lda_cli JSON summary from log
    log_file = job_dir / "log.txt"
    lda_summary = {}
    if log_file.exists():
        log_text = log_file.read_text()
        try:
            start = log_text.index('{\n  "status"')
            depth = 0
            for i, ch in enumerate(log_text[start:], start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        lda_summary = json.loads(log_text[start:i+1])
                        break
        except (ValueError, json.JSONDecodeError):
            pass

    result = {
        "job_id": args.job_id,
        "status": "completed",
        "anonymized_file": str(anon_file),
        "mapping_file": str(mapping_file),
        "anonymized_size_kb": round(anon_file.stat().st_size / 1024, 1) if anon_file.exists() else 0,
        "entity_count": mapping_data.get("metadata", {}).get("entity_count", 0),
        "replacements_made": len(mapping_data.get("replacement_log", [])),
        "submitted_at": status_data.get("submitted_at", ""),
    }

    if status_data.get("batch_id"):
        result["batch_id"] = status_data["batch_id"]

    if lda_summary.get("document_type"):
        result["document_type"] = lda_summary["document_type"]

    print(json.dumps(result, indent=2))



def cmd_results_all(args):
    """Get results for ALL completed jobs in one call."""
    if not JOBS_DIR.exists():
        print(json.dumps({"results": [], "total": 0}))
        return

    results = []
    failed_jobs = []
    for job_dir in sorted(JOBS_DIR.iterdir()):
        if not job_dir.is_dir() or not job_dir.name.startswith("lda-"):
            continue

        data = read_status(job_dir)
        state = derive_state(job_dir, data)

        if state == "completed":
            output_dir = Path(data.get("output_dir", str(job_dir)))
            anon_file = output_dir / "anonymized.txt"
            mapping_file = output_dir / "mapping.json"

            mapping_data = {}
            if mapping_file.exists():
                with open(mapping_file) as f:
                    mapping_data = json.load(f)

            results.append({
                "job_id": data.get("job_id", job_dir.name),
                "filename": data.get("safe_filename", data.get("job_id", "unknown")),
                "anonymized_file": str(anon_file),
                "mapping_file": str(mapping_file),
                "entity_count": mapping_data.get("metadata", {}).get("entity_count", 0),
                "replacements_made": len(mapping_data.get("replacement_log", [])),
            })
        elif state == "failed":
            log_file = job_dir / "log.txt"
            reason = "unknown"
            if log_file.exists():
                log_text = log_file.read_text()
                if "No text extracted" in log_text:
                    reason = "scanned/image PDF — no text layer"
                elif "Traceback" in log_text:
                    lines = log_text.strip().split("\n")
                    reason = lines[-1][:200] if lines else "unknown error"
            failed_jobs.append({
                "job_id": data.get("job_id", job_dir.name),
                "filename": data.get("safe_filename", data.get("job_id", "unknown")),
                "reason": reason,
            })

    output = {
        "completed": len(results),
        "failed": len(failed_jobs),
        "results": results,
    }
    if failed_jobs:
        output["failed_jobs"] = failed_jobs

    print(json.dumps(output, indent=2))


def cmd_list(args):
    """List all jobs with queue positions."""
    if not JOBS_DIR.exists():
        print(json.dumps({"jobs": [], "worker_running": False}))
        return

    jobs = []
    queue_pos = 0
    for job_dir in sorted(JOBS_DIR.iterdir(), reverse=True):
        if not job_dir.is_dir() or not job_dir.name.startswith("lda-"):
            continue

        status_data = read_status(job_dir)
        state = derive_state(job_dir, status_data)

        entry = {
            "job_id": job_dir.name,
            "status": state,
            "submitted_at": status_data.get("submitted_at", ""),
            "filename": status_data.get("safe_filename", job_dir.name),
            "input_size_kb": round(status_data.get("input_size", 0) / 1024, 1),
        }
        if status_data.get("batch_id"):
            entry["batch_id"] = status_data["batch_id"]

        jobs.append(entry)
        if len(jobs) >= 30:
            break

    # Add queue positions (re-derive from oldest first)
    queued_ids = [j["job_id"] for j in sorted(
        [j for j in jobs if j["status"] == "queued"],
        key=lambda j: j["submitted_at"],
    )]
    for job in jobs:
        if job["status"] == "queued" and job["job_id"] in queued_ids:
            job["queue_position"] = queued_ids.index(job["job_id"]) + 1

    print(json.dumps({
        "jobs": jobs,
        "worker_running": is_worker_running(),
    }, indent=2))



def cmd_progress(args):
    """Show overall progress with ETA."""
    if not JOBS_DIR.exists():
        print(json.dumps({"error": "No jobs found"}))
        return

    jobs = []
    for job_dir in sorted(JOBS_DIR.iterdir()):
        if not job_dir.is_dir() or not job_dir.name.startswith("lda-"):
            continue
        data = read_status(job_dir)
        state = derive_state(job_dir, data)
        data["_state"] = state
        data["_job_dir"] = str(job_dir)
        jobs.append(data)

    if not jobs:
        print(json.dumps({"error": "No jobs found"}))
        return

    total = len(jobs)
    completed = [j for j in jobs if j["_state"] == "completed"]
    running = [j for j in jobs if j["_state"] == "running"]
    queued = [j for j in jobs if j["_state"] == "queued"]
    failed = [j for j in jobs if j["_state"] == "failed"]
    cancelled = [j for j in jobs if j["_state"] == "cancelled"]

    active_total = len(completed) + len(running) + len(queued)
    pct_completed = round(len(completed) / active_total * 100, 1) if active_total > 0 else 0
    pct_remaining = round((len(running) + len(queued)) / active_total * 100, 1) if active_total > 0 else 0

    # Estimate time from completed jobs
    durations = []
    for j in completed:
        started = j.get("started_at")
        finished = j.get("completed_at")
        if started and finished:
            try:
                t0 = datetime.fromisoformat(started)
                t1 = datetime.fromisoformat(finished)
                durations.append((t1 - t0).total_seconds())
            except ValueError:
                pass

    avg_seconds = sum(durations) / len(durations) if durations else None
    remaining_count = len(running) + len(queued)

    # Current job segment progress
    current_job_progress = None
    if running:
        rj = running[0]
        log_file = Path(rj["_job_dir"]) / "log.txt"
        if log_file.exists():
            log_text = log_file.read_text()
            # Find segment progress lines
            import re as _re
            segments = _re.findall(r"Processing segment (\d+)/(\d+)", log_text)
            if segments:
                cur_seg, total_seg = int(segments[-1][0]), int(segments[-1][1])
                current_job_progress = {
                    "job_id": rj.get("job_id"),
                    "filename": rj.get("safe_filename", rj.get("job_id", "unknown")),
                    "segment": f"{cur_seg}/{total_seg}",
                    "segment_pct": round(cur_seg / total_seg * 100) if total_seg > 0 else 0,
                }
                # Estimate time for current job from segment timestamps
                seg_times = _re.findall(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*Processing segment (\d+)/(\d+)", log_text)
                if len(seg_times) >= 2:
                    try:
                        t_first = datetime.strptime(seg_times[0][0], "%Y-%m-%d %H:%M:%S")
                        t_last = datetime.strptime(seg_times[-1][0], "%Y-%m-%d %H:%M:%S")
                        segs_done = int(seg_times[-1][1]) - int(seg_times[0][1])
                        if segs_done > 0:
                            per_seg = (t_last - t_first).total_seconds() / segs_done
                            segs_left = total_seg - cur_seg
                            current_job_progress["est_remaining_seconds"] = round(per_seg * segs_left)
                    except (ValueError, ZeroDivisionError):
                        pass

    # Build ETA
    eta_seconds = None
    if avg_seconds is not None and remaining_count > 0:
        # Time for queued jobs (full average each)
        eta_seconds = avg_seconds * len(queued)
        # Add estimate for current running job
        if current_job_progress and "est_remaining_seconds" in current_job_progress:
            eta_seconds += current_job_progress["est_remaining_seconds"]
        elif avg_seconds:
            eta_seconds += avg_seconds * 0.5  # rough guess: half done
        eta_seconds = round(eta_seconds)

    def fmt_duration(secs):
        if secs is None:
            return "unknown"
        if secs < 60:
            return f"{int(secs)}s"
        elif secs < 3600:
            return f"{int(secs // 60)}m {int(secs % 60)}s"
        else:
            return f"{int(secs // 3600)}h {int((secs % 3600) // 60)}m"

    result = {
        "total_jobs": active_total,
        "completed": len(completed),
        "running": len(running),
        "queued": len(queued),
        "failed": len(failed),
        "cancelled": len(cancelled),
        "pct_completed": pct_completed,
        "pct_remaining": pct_remaining,
        "avg_job_duration": fmt_duration(avg_seconds),
        "eta": fmt_duration(eta_seconds),
    }

    if current_job_progress:
        result["current_job"] = current_job_progress

    # Completed job summaries
    if completed:
        result["completed_files"] = [
            j.get("safe_filename", j.get("job_id", "unknown")) for j in completed
        ]

    print(json.dumps(result, indent=2))


def cmd_clean(args):
    """Remove old completed/failed/cancelled job directories."""
    if not JOBS_DIR.exists():
        print(json.dumps({"removed": 0}))
        return

    cutoff = datetime.now() - timedelta(days=args.days)
    removed = 0

    for job_dir in list(JOBS_DIR.iterdir()):
        if not job_dir.is_dir() or not job_dir.name.startswith("lda-"):
            continue

        status_data = read_status(job_dir)
        submitted_at = status_data.get("submitted_at", "")
        if not submitted_at:
            continue

        try:
            job_time = datetime.fromisoformat(submitted_at)
        except ValueError:
            continue

        if job_time < cutoff:
            state = derive_state(job_dir, status_data)
            if state in ("running", "queued"):
                continue
            shutil.rmtree(job_dir)
            removed += 1

    print(json.dumps({"removed": removed, "older_than_days": args.days}))


def cmd_batch_status(args):
    """Check status of all jobs in a batch."""
    batch_id = args.batch_id
    if not JOBS_DIR.exists():
        print(json.dumps({"error": "No jobs found"}))
        return

    total = 0
    completed = 0
    failed = 0
    running = 0
    queued = 0
    job_summaries = []

    for job_dir in sorted(JOBS_DIR.iterdir()):
        if not job_dir.is_dir() or not job_dir.name.startswith("lda-"):
            continue

        data = read_status(job_dir)
        if data.get("batch_id") != batch_id:
            continue

        state = derive_state(job_dir, data)
        total += 1

        if state == "completed":
            completed += 1
        elif state == "failed":
            failed += 1
        elif state == "running":
            running += 1
        elif state == "queued":
            queued += 1

        job_summaries.append({
            "job_id": data.get("job_id", job_dir.name),
            "status": state,
            "filename": data.get("safe_filename", "unknown"),
        })

    if total == 0:
        print(json.dumps({"error": f"No jobs found for batch: {batch_id}"}))
        return

    all_done = (running == 0 and queued == 0)

    print(json.dumps({
        "batch_id": batch_id,
        "total": total,
        "completed": completed,
        "failed": failed,
        "running": running,
        "queued": queued,
        "all_done": all_done,
        "jobs": job_summaries,
    }, indent=2))


# ── Worker ────────────────────────────────────────────────


def cmd_worker(args):
    """Background worker: process queued jobs sequentially, exit when empty."""
    WORKER_PID_FILE.write_text(str(os.getpid()))

    # Pre-warm Ollama to load the model before first job
    prewarm_ollama()

    try:
        while True:
            queued = get_queued_jobs()
            if not queued:
                break

            job_id, status_data = queued[0]
            job_dir = get_job_dir(job_id)

            # Find the input file (preserves original extension)
            input_copies = list(job_dir.glob("input.*"))
            input_copy = input_copies[0] if input_copies else job_dir / "input.txt"
            output_dir = Path(status_data.get("output_dir", str(job_dir)))

            # Check content cache before processing
            cached = check_cache(input_copy)
            if cached:
                # Cache hit — copy results and skip LDA
                shutil.copy2(cached / "anonymized.txt", output_dir / "anonymized.txt")
                shutil.copy2(cached / "mapping.json", output_dir / "mapping.json")
                status_data["status"] = "completed"
                status_data["completed_at"] = datetime.now().isoformat()
                status_data["cache_hit"] = True
                status_data["pid"] = None
                write_status(job_dir, status_data)
                log_file = job_dir / "log.txt"
                log_file.write_text(f"Cache hit: {cached}\n")
                continue

            status_data["status"] = "running"
            status_data["started_at"] = datetime.now().isoformat()
            write_status(job_dir, status_data)

            # Run lda_cli.py synchronously with timeout
            cmd = [
                str(VENV_PYTHON),
                str(LDA_CLI),
                "anonymize",
                "--input", str(input_copy),
                "--output-dir", str(output_dir),
            ]

            log_file = job_dir / "log.txt"
            with open(log_file, "w") as log_fh:
                try:
                    result = subprocess.run(
                        cmd,
                        stdout=log_fh,
                        stderr=subprocess.STDOUT,
                        timeout=JOB_TIMEOUT,
                    )
                    exit_code = result.returncode
                except subprocess.TimeoutExpired:
                    exit_code = -1
                    log_fh.write(f"\n[TIMEOUT] Job timed out after {JOB_TIMEOUT}s\n")

            # Update status based on exit code
            if exit_code == 0:
                status_data["status"] = "completed"
                status_data["completed_at"] = datetime.now().isoformat()
                # Store in content cache for future reuse
                try:
                    store_in_cache(input_copy, output_dir)
                except Exception:
                    pass  # Cache storage is best-effort
            elif exit_code == -1:
                # Timeout — check retry
                retry_count = status_data.get("retry_count", 0)
                if retry_count < MAX_RETRIES:
                    status_data["status"] = "queued"
                    status_data["retry_count"] = retry_count + 1
                    status_data["last_retry_at"] = datetime.now().isoformat()
                else:
                    status_data["status"] = "failed"
                    status_data["error"] = f"timed out after {MAX_RETRIES + 1} attempts"
                    status_data["failed_at"] = datetime.now().isoformat()
            else:
                # Non-zero exit — check retry
                retry_count = status_data.get("retry_count", 0)
                if retry_count < MAX_RETRIES:
                    status_data["status"] = "queued"
                    status_data["retry_count"] = retry_count + 1
                    status_data["last_retry_at"] = datetime.now().isoformat()
                else:
                    status_data["status"] = "failed"
                    status_data["failed_at"] = datetime.now().isoformat()

            # Store worker PID for state derivation (backwards compat)
            status_data["pid"] = None
            write_status(job_dir, status_data)

    finally:
        # Clean up PID file
        if WORKER_PID_FILE.exists():
            try:
                WORKER_PID_FILE.unlink()
            except OSError:
                pass


# ── Main ──────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="LDA Batch Processor — sequential job queue for long documents",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sub = subparsers.add_parser("submit", help="Submit a document for anonymization")
    sub.add_argument("input", help="Path to the input file")
    sub.add_argument("--output-dir", default=None, help="Output directory (default: job dir)")
    sub.add_argument("--batch-id", default=None, help="Assign to a batch (for grouped tracking)")

    sub = subparsers.add_parser("status", help="Check job status")
    sub.add_argument("job_id", help="Job ID")

    sub = subparsers.add_parser("result", help="Get completed job results")
    sub.add_argument("job_id", help="Job ID")

    sub = subparsers.add_parser("cancel", help="Cancel a queued job")
    sub.add_argument("job_id", help="Job ID to cancel")

    subparsers.add_parser("list", help="List all jobs")

    subparsers.add_parser("progress", help="Show overall progress with ETA")

    subparsers.add_parser("results-all", help="Get results for all completed jobs")

    sub = subparsers.add_parser("clean", help="Remove old job directories")
    sub.add_argument("--days", type=int, default=7, help="Remove jobs older than N days (default: 7)")

    sub = subparsers.add_parser("batch-status", help="Check status of all jobs in a batch")
    sub.add_argument("batch_id", help="Batch ID")

    subparsers.add_parser("worker", help="(internal) Background worker process")

    args = parser.parse_args()

    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    commands = {
        "submit": cmd_submit,
        "status": cmd_status,
        "result": cmd_result,
        "cancel": cmd_cancel,
        "list": cmd_list,
        "clean": cmd_clean,
        "progress": cmd_progress,
        "results-all": cmd_results_all,
        "batch-status": cmd_batch_status,
        "worker": cmd_worker,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
