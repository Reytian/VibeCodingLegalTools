#!/usr/bin/env python3
"""
LDA File Watcher — auto-intercepts inbound files and submits them to LDA.

Polls ~/.openclaw/media/inbound/ for new files every 5 seconds.
Supported files are submitted to lda_batch.py, then moved to .processed/.
Files found in the same scan cycle share a batch_id for grouped tracking.

Also monitors the LDA worker and restarts it if it dies with queued jobs.

Designed to run as a launchd service (com.openclaw.lda-watcher).
No external dependencies — uses only stdlib + lda_batch.py CLI.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

INBOUND_DIR = Path(os.path.expanduser("~/.openclaw/media/inbound"))
PROCESSED_DIR = INBOUND_DIR / ".processed"
MANIFEST_FILE = Path(os.path.expanduser("~/.openclaw/workspace-legal/lda-manifest.json"))
LOG_FILE = Path(os.path.expanduser("~/.openclaw/logs/lda-watcher.log"))
POLL_INTERVAL = 5  # seconds
WORKER_CHECK_INTERVAL = 30  # seconds — check worker health every N seconds

SUPPORTED_EXTENSIONS = {".txt", ".doc", ".docx", ".pdf", ".zip"}
VENV_PYTHON = Path(__file__).parent / ".venv" / "bin" / "python"
BATCH_SCRIPT = Path(__file__).parent / "lda_batch.py"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def ensure_dirs():
    """Create required directories if they don't exist."""
    INBOUND_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_manifest() -> list[dict]:
    """Load the manifest file, or return empty list if it doesn't exist."""
    if not MANIFEST_FILE.exists():
        return []
    try:
        with open(MANIFEST_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_manifest(entries: list[dict]):
    """Save manifest entries to disk."""
    with open(MANIFEST_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def generate_batch_id() -> str:
    """Generate a batch ID for grouping files from one scan cycle."""
    return f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def submit_file(file_path: Path, batch_id: str = "") -> dict | None:
    """Submit a file to lda_batch.py and return the parsed result."""
    cmd = [str(VENV_PYTHON), str(BATCH_SCRIPT), "submit", str(file_path)]
    if batch_id:
        cmd.extend(["--batch-id", batch_id])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("Submit failed for %s: %s", file_path.name, result.stderr[:200])
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        logger.error("Submit timed out for %s", file_path.name)
        return None
    except json.JSONDecodeError:
        logger.error("Invalid JSON from submit for %s", file_path.name)
        return None


def move_to_processed(file_path: Path) -> Path:
    """Move a file to .processed/, handling name collisions."""
    dest = PROCESSED_DIR / file_path.name
    if dest.exists():
        stem = file_path.stem
        suffix = file_path.suffix
        ts = datetime.now().strftime("%H%M%S")
        dest = PROCESSED_DIR / f"{stem}-{ts}{suffix}"
    shutil.move(str(file_path), str(dest))
    return dest


def collect_inbound_files() -> list[Path]:
    """Collect all supported files currently in the inbound directory."""
    files = []
    try:
        with os.scandir(INBOUND_DIR) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if entry.name.startswith("."):
                    continue
                path = Path(entry.path)
                if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(path)
    except FileNotFoundError:
        pass
    return sorted(files, key=lambda p: p.name)


def process_batch(files: list[Path]):
    """Submit all files in one scan cycle under a single batch_id."""
    if not files:
        return

    # Generate a shared batch_id if multiple files (or even one — consistent tracking)
    batch_id = generate_batch_id()
    logger.info("Processing %d files as %s", len(files), batch_id)

    manifest = load_manifest()

    for file_path in files:
        logger.info("New file detected: %s", file_path.name)

        # Zips generate their own batch_id internally, so don't pass ours
        if file_path.suffix.lower() == ".zip":
            result = submit_file(file_path)
        else:
            result = submit_file(file_path, batch_id=batch_id)

        if result is None:
            logger.error("Failed to submit %s, leaving in inbound/", file_path.name)
            continue

        # Extract job IDs from result
        job_ids = []
        if "job_id" in result:
            job_ids.append(result["job_id"])
        elif "jobs" in result:
            job_ids = [j["job_id"] for j in result.get("jobs", [])]

        # Use the batch_id from the result (zip creates its own)
        effective_batch_id = result.get("batch_id", batch_id)

        # Move to processed
        dest = move_to_processed(file_path)
        logger.info("Moved %s to .processed/", file_path.name)

        # Write manifest entry
        manifest.append({
            "original_filename": file_path.name,
            "job_ids": job_ids,
            "batch_id": effective_batch_id,
            "submitted_at": datetime.now().isoformat(),
            "processed_path": str(dest),
        })
        logger.info("Submitted %s → batch=%s, jobs=%s", file_path.name, effective_batch_id, job_ids)

    save_manifest(manifest)


def check_worker_health():
    """Check if worker is alive; restart it if there are queued jobs."""
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(BATCH_SCRIPT), "list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return
        data = json.loads(result.stdout)
        worker_running = data.get("worker_running", False)
        queued_count = sum(1 for j in data.get("jobs", []) if j.get("status") == "queued")

        if queued_count > 0 and not worker_running:
            logger.warning("Worker dead with %d queued jobs — restarting", queued_count)
            # Submit a no-op to trigger ensure_worker() — create a tiny temp file
            # Actually, just call ensure_worker via a dummy submit won't work.
            # Instead, start the worker directly.
            jobs_dir = Path(os.path.expanduser("~/.openclaw/tools/lda/jobs"))
            worker_log = jobs_dir / ".worker.log"
            with open(worker_log, "a") as log_fh:
                proc = subprocess.Popen(
                    [str(VENV_PYTHON), str(BATCH_SCRIPT), "worker"],
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            pid_file = jobs_dir / ".worker.pid"
            pid_file.write_text(str(proc.pid))
            logger.info("Restarted worker (PID %d) for %d queued jobs", proc.pid, queued_count)
    except Exception as e:
        logger.error("Worker health check failed: %s", e)


def main():
    """Main polling loop."""
    logger.info("LDA File Watcher starting (polling every %ds)", POLL_INTERVAL)
    logger.info("Watching: %s", INBOUND_DIR)

    ensure_dirs()

    last_worker_check = 0

    while True:
        try:
            # Collect all files in one pass, submit as a batch
            files = collect_inbound_files()
            if files:
                process_batch(files)

            # Periodically check worker health
            now = time.monotonic()
            if now - last_worker_check >= WORKER_CHECK_INTERVAL:
                check_worker_health()
                last_worker_check = now

        except Exception as e:
            logger.error("Scan error: %s", e, exc_info=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
