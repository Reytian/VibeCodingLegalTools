#!/usr/bin/env python3
"""Save batch meta instructions for the LDA auto-pipeline.

Usage:
    save_batch_meta.py <batch_id> <instructions> [--related <batch_id> ...] [--task-type memo|review-comments|review-redline|draft]

Examples:
    save_batch_meta.py batch-20260305-002704 "Analyze contracts for expiration"
    save_batch_meta.py batch-20260305-002704 "Review this NDA" --task-type review-comments
    save_batch_meta.py batch-20260305-002704 "Revise this contract" --task-type review-redline
    save_batch_meta.py batch-20260305-002704 "Draft NDA: Parties are X and Y, 2-year term" --task-type draft
    save_batch_meta.py batch-20260305-002704 "Write a unified memo" --related batch-20260305-002657 batch-20260305-002703
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

JOBS_DIR = Path.home() / ".openclaw/tools/lda/jobs"
META_DIR = JOBS_DIR / ".batch_meta"

VALID_TASK_TYPES = ["memo", "review-comments", "review-redline", "draft"]


def count_batch_files(batch_id):
    """Count jobs belonging to a batch."""
    count = 0
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir() or job_dir.name.startswith("."):
            continue
        status_file = job_dir / "status.json"
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text())
                if data.get("batch_id") == batch_id:
                    count += 1
            except Exception:
                pass
    return count


def main():
    parser = argparse.ArgumentParser(description="Save batch meta instructions for auto-pipeline")
    parser.add_argument("batch_id", help="Primary batch ID (e.g., batch-20260305-002704)")
    parser.add_argument("instructions", help="Processing instructions for CC")
    parser.add_argument("--related", nargs="+", default=[], help="Related batch IDs to group together")
    parser.add_argument(
        "--task-type",
        choices=VALID_TASK_TYPES,
        default="memo",
        help="Task type: memo (default), review-comments, review-redline, or draft",
    )
    args = parser.parse_args()

    META_DIR.mkdir(parents=True, exist_ok=True)

    # Validate primary batch exists
    file_count = count_batch_files(args.batch_id)
    if file_count == 0:
        print(json.dumps({"error": "No jobs found for batch " + args.batch_id}))
        sys.exit(1)

    # Validate related batches
    all_batches = [args.batch_id] + args.related
    total_files = file_count
    for rid in args.related:
        rc = count_batch_files(rid)
        if rc == 0:
            print(json.dumps({"error": "No jobs found for related batch " + rid}))
            sys.exit(1)
        total_files += rc

    # Build meta
    meta = {
        "batch_id": args.batch_id,
        "instructions": args.instructions,
        "task_type": args.task_type,
        "submitted_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "file_count": total_files,
    }
    if args.related:
        meta["related_batches"] = args.related

    # Save with correct filename
    meta_file = META_DIR / (args.batch_id + ".json")
    meta_file.write_text(json.dumps(meta, indent=2))

    # Also save a pointer in each related batch's meta
    for rid in args.related:
        related_meta = {
            "batch_id": rid,
            "instructions": args.instructions,
            "task_type": args.task_type,
            "submitted_at": meta["submitted_at"],
            "file_count": count_batch_files(rid),
            "primary_batch": args.batch_id,
        }
        related_file = META_DIR / (rid + ".json")
        related_file.write_text(json.dumps(related_meta, indent=2))

    result = {
        "status": "saved",
        "meta_file": str(meta_file),
        "batch_id": args.batch_id,
        "task_type": args.task_type,
        "related_batches": args.related,
        "total_files": total_files,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
