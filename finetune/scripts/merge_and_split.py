#!/usr/bin/env python3
"""
Merge LDA and drafting JSONL datasets, shuffle, and split into train/val (90/10).

Input:
  ../lda_train.jsonl
  ../drafting_train.jsonl

Output:
  ../train.jsonl   (90%)
  ../valid.jsonl   (10%)
"""

import json
import logging
import random
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
LDA_FILE = BASE_DIR / "lda_train.jsonl"
DRAFTING_FILE = BASE_DIR / "drafting_train.jsonl"
TRAIN_OUTPUT = BASE_DIR / "train.jsonl"
VALID_OUTPUT = BASE_DIR / "valid.jsonl"

SPLIT_RATIO = 0.9
RANDOM_SEED = 42


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file, skipping malformed lines."""
    examples = []
    if not path.exists():
        log.warning("File not found, skipping: %s", path)
        return examples

    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                examples.append(obj)
            except json.JSONDecodeError as e:
                log.warning("Skipping malformed line %d in %s: %s", i, path.name, e)

    return examples


def write_jsonl(path: Path, examples: list[dict]):
    """Write examples to a JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")


def main():
    # Load both datasets
    lda_examples = load_jsonl(LDA_FILE)
    drafting_examples = load_jsonl(DRAFTING_FILE)

    log.info("Loaded %d LDA examples from %s", len(lda_examples), LDA_FILE.name)
    log.info("Loaded %d drafting examples from %s", len(drafting_examples), DRAFTING_FILE.name)

    all_examples = lda_examples + drafting_examples

    if not all_examples:
        log.error("No examples found. Run prepare_lda_dataset.py and "
                   "prepare_drafting_dataset.py first.")
        sys.exit(1)

    # Tag source for stats (temporary, not written to output)
    for ex in lda_examples:
        ex["_source"] = "lda"
    for ex in drafting_examples:
        ex["_source"] = "drafting"

    # Shuffle
    random.seed(RANDOM_SEED)
    random.shuffle(all_examples)

    # Split
    split_idx = int(len(all_examples) * SPLIT_RATIO)
    train_set = all_examples[:split_idx]
    valid_set = all_examples[split_idx:]

    # Remove temporary tags before writing
    for ex in all_examples:
        ex.pop("_source", None)

    write_jsonl(TRAIN_OUTPUT, train_set)
    write_jsonl(VALID_OUTPUT, valid_set)

    # Compute stats
    total_tokens_train = 0
    total_tokens_valid = 0

    for ex in train_set:
        total_tokens_train += sum(estimate_tokens(m["content"]) for m in ex["messages"])
    for ex in valid_set:
        total_tokens_valid += sum(estimate_tokens(m["content"]) for m in ex["messages"])

    # Stats
    log.info("=" * 50)
    log.info("Merge & Split Summary")
    log.info("=" * 50)
    log.info("Source breakdown:")
    log.info("  LDA (anonymization): %d examples", len(lda_examples))
    log.info("  Drafting:            %d examples", len(drafting_examples))
    log.info("  Total:               %d examples", len(all_examples))
    log.info("")
    log.info("Split (%.0f/%.0f):", SPLIT_RATIO * 100, (1 - SPLIT_RATIO) * 100)
    log.info("  Train: %d examples (~%d tokens)", len(train_set), total_tokens_train)
    log.info("  Valid: %d examples (~%d tokens)", len(valid_set), total_tokens_valid)
    log.info("")
    log.info("Output files:")
    log.info("  %s", TRAIN_OUTPUT)
    log.info("  %s", VALID_OUTPUT)
    log.info("=" * 50)


if __name__ == "__main__":
    main()
