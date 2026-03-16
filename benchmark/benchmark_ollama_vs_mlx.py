#!/usr/bin/env python3
"""
Benchmark: Ollama (qwen3.5-legal Q5) vs MLX (qwen3.5-legal 4-bit)
Tests: LDA anonymization, memo drafting, review-comments, review-redline, contract drafting
Measures: tokens/sec, latency, output quality (JSON validity, entity count, completeness)
"""

import json
import os
import re
import requests
import sys
import time
from pathlib import Path

# --- Config ---
OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3.5-legal"
MLX_BASE = "http://127.0.0.1:8801"
MLX_MODEL = "/Users/claptrap/finetune/mlx-legal"  # placeholder, mlx_lm.server uses loaded model

# Test documents
SHORT_DOC = "/tmp/lda-test-phase2-long/kintara-employment.txt"    # 10KB
LONG_DOC = "/tmp/lda-test-phase2-long/brightspring-employment.txt" # 40KB

SYSTEM_MSG = (
    "/no_think\n"
    "You are a precise legal document analysis assistant. "
    "Always return valid JSON as requested. "
    "Never include any text outside the JSON structure."
)

# --- LDA prompts (simplified from core/prompts.py) ---
LDA_PASS1_PROMPT = """Analyze this legal document and identify ALL personally identifiable information (PII).

For each entity found, provide:
- type: person, company, address, phone, email, ssn, date, amount, account, other
- value: the exact text as it appears
- aliases: list of alternative forms/references to the same entity

Return JSON format:
{
  "entities": [
    {"type": "person", "value": "John Smith", "aliases": ["Mr. Smith", "Smith"]},
    ...
  ]
}

DOCUMENT:
"""

REVIEW_COMMENTS_PROMPT = """Review this legal document and provide substantive comments on key provisions.
Focus on: risk allocation, indemnification, termination, non-compete, confidentiality.

Return JSON:
{
  "comments": [
    {"paragraph": 1, "text": "quoted text from doc", "comment": "your analysis"},
    ...
  ]
}

DOCUMENT:
"""

REVIEW_REDLINE_PROMPT = """Review this contract and suggest specific edits using tracked changes markup.
Use ~~deleted text~~ for deletions and **added text** for insertions.

Return the full revised text with markup inline. Focus on:
- Overly broad non-compete/non-solicit
- One-sided indemnification
- Unreasonable termination provisions

Return JSON:
{
  "revised_text": "full text with ~~deletions~~ and **insertions**",
  "change_summary": ["list of key changes"]
}

DOCUMENT (first 5000 chars):
"""

DRAFT_PROMPT = """Draft an employment agreement clause for a senior software engineer.
Include: base salary $250,000, equity vesting 4 years with 1-year cliff,
12-month non-compete limited to direct competitors, mutual termination with 30 days notice.

Return JSON:
{
  "clause_text": "the drafted clause text",
  "key_terms": ["list of key terms included"]
}
"""

MEMO_PROMPT = """Based on this anonymized legal document, draft a brief legal memo (2-3 paragraphs)
analyzing the key provisions and potential risks.

Return JSON:
{
  "memo": "the memo text",
  "key_risks": ["list of identified risks"]
}

DOCUMENT (first 5000 chars):
"""


def call_ollama(messages, temperature=0.1):
    """Call Ollama native API, return (content, tokens_per_sec, total_time)."""
    body = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_MSG}] + messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 16384,
            "num_predict": 4096,
        },
    }
    t0 = time.time()
    resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=body, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    elapsed = time.time() - t0
    content = data.get("message", {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    eval_count = data.get("eval_count", 0)
    eval_duration = data.get("eval_duration", 0)
    tps = eval_count / (eval_duration / 1e9) if eval_duration > 0 else 0

    return content, tps, elapsed


def call_mlx(messages, temperature=0.1):
    """Call MLX server (OpenAI-compatible), return (content, tokens_per_sec, total_time)."""
    body = {
        "model": "/Users/claptrap/finetune/mlx-legal",
        "messages": [{"role": "system", "content": SYSTEM_MSG}] + messages,
        "temperature": temperature,
        "max_tokens": 4096,
    }
    t0 = time.time()
    resp = requests.post(f"{MLX_BASE}/v1/chat/completions", json=body, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    elapsed = time.time() - t0
    content = data["choices"][0]["message"]["content"]
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    usage = data.get("usage", {})
    completion_tokens = usage.get("completion_tokens", 0)
    tps = completion_tokens / elapsed if elapsed > 0 and completion_tokens > 0 else 0

    return content, tps, elapsed


def validate_json(content):
    """Try to parse JSON from content. Returns (parsed, is_valid)."""
    # Try direct parse
    try:
        return json.loads(content), True
    except json.JSONDecodeError:
        pass
    # Try extracting JSON block
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1)), True
        except json.JSONDecodeError:
            pass
    # Try finding first { to last }
    start = content.find('{')
    end = content.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end+1]), True
        except json.JSONDecodeError:
            pass
    return None, False


def score_lda(content, doc_size):
    """Score LDA output: JSON valid, entity count, type diversity."""
    parsed, valid = validate_json(content)
    score = 0
    details = {"json_valid": valid, "entity_count": 0, "types": [], "has_aliases": False}
    if not valid:
        return score, details

    score += 3  # valid JSON
    entities = parsed.get("entities", [])
    details["entity_count"] = len(entities)

    if len(entities) >= 3:
        score += 2
    elif len(entities) >= 1:
        score += 1

    types = set(e.get("type", "") for e in entities)
    details["types"] = sorted(types)
    if len(types) >= 3:
        score += 2
    elif len(types) >= 2:
        score += 1

    has_aliases = any(len(e.get("aliases", [])) > 0 for e in entities)
    details["has_aliases"] = has_aliases
    if has_aliases:
        score += 1

    # Check entity values are non-empty strings
    valid_vals = all(isinstance(e.get("value"), str) and len(e["value"]) > 0 for e in entities)
    if valid_vals and entities:
        score += 2

    return min(score, 10), details


def score_review(content, task):
    """Score review output (comments or redline)."""
    parsed, valid = validate_json(content)
    score = 0
    details = {"json_valid": valid}
    if not valid:
        return score, details

    score += 3

    if task == "comments":
        comments = parsed.get("comments", [])
        details["comment_count"] = len(comments)
        if len(comments) >= 3:
            score += 3
        elif len(comments) >= 1:
            score += 1
        # Check comment quality
        has_analysis = any(len(c.get("comment", "")) > 20 for c in comments)
        if has_analysis:
            score += 2
        has_quotes = any(len(c.get("text", "")) > 5 for c in comments)
        if has_quotes:
            score += 2
    elif task == "redline":
        revised = parsed.get("revised_text", "")
        changes = parsed.get("change_summary", [])
        details["has_revised_text"] = len(revised) > 100
        details["change_count"] = len(changes)
        if len(revised) > 100:
            score += 3
        if len(changes) >= 2:
            score += 2
        has_markup = "~~" in revised or "**" in revised
        details["has_markup"] = has_markup
        if has_markup:
            score += 2

    return min(score, 10), details


def score_draft(content):
    """Score contract drafting output."""
    parsed, valid = validate_json(content)
    score = 0
    details = {"json_valid": valid}
    if not valid:
        return score, details

    score += 3
    clause = parsed.get("clause_text", "")
    terms = parsed.get("key_terms", [])
    details["clause_length"] = len(clause)
    details["term_count"] = len(terms)

    if len(clause) > 200:
        score += 3
    elif len(clause) > 50:
        score += 1

    if len(terms) >= 3:
        score += 2
    elif len(terms) >= 1:
        score += 1

    # Check for key content
    clause_lower = clause.lower()
    has_salary = "250,000" in clause or "250000" in clause
    has_equity = "vest" in clause_lower
    has_noncompete = "non-compete" in clause_lower or "noncompete" in clause_lower or "non compete" in clause_lower
    details["has_salary"] = has_salary
    details["has_equity"] = has_equity
    details["has_noncompete"] = has_noncompete
    score += sum([has_salary, has_equity, has_noncompete])

    return min(score, 10), details


def score_memo(content):
    """Score memo drafting output."""
    parsed, valid = validate_json(content)
    score = 0
    details = {"json_valid": valid}
    if not valid:
        return score, details

    score += 3
    memo = parsed.get("memo", "")
    risks = parsed.get("key_risks", [])
    details["memo_length"] = len(memo)
    details["risk_count"] = len(risks)

    if len(memo) > 300:
        score += 3
    elif len(memo) > 100:
        score += 1

    if len(risks) >= 3:
        score += 2
    elif len(risks) >= 1:
        score += 1

    # Check memo substance
    memo_lower = memo.lower()
    legal_terms = ["indemnif", "terminat", "non-compete", "confidential", "govern", "liabil"]
    term_hits = sum(1 for t in legal_terms if t in memo_lower)
    if term_hits >= 3:
        score += 2
    elif term_hits >= 1:
        score += 1

    return min(score, 10), details


def run_test(backend_fn, backend_name, test_name, messages, score_fn):
    """Run a single test and return result dict."""
    print(f"  [{backend_name}] {test_name}...", end=" ", flush=True)
    try:
        content, tps, elapsed = backend_fn(messages)
        score, details = score_fn(content)
        print(f"{score}/10 | {tps:.1f} t/s | {elapsed:.1f}s")
        return {
            "test": test_name,
            "backend": backend_name,
            "score": score,
            "tps": round(tps, 1),
            "latency": round(elapsed, 1),
            "details": details,
            "output_len": len(content),
        }
    except Exception as e:
        print(f"FAILED: {e}")
        return {
            "test": test_name,
            "backend": backend_name,
            "score": 0,
            "tps": 0,
            "latency": 0,
            "details": {"error": str(e)},
            "output_len": 0,
        }


def main():
    short_doc = Path(SHORT_DOC).read_text(encoding="utf-8") if Path(SHORT_DOC).exists() else "No test doc"
    long_doc = Path(LONG_DOC).read_text(encoding="utf-8") if Path(LONG_DOC).exists() else "No test doc"

    # Check backends
    backends = []
    try:
        requests.get(f"{OLLAMA_BASE}/api/version", timeout=5)
        backends.append(("Ollama", call_ollama))
        print("✓ Ollama available")
    except Exception:
        print("✗ Ollama not available")

    try:
        requests.get(f"{MLX_BASE}/v1/models", timeout=5)
        backends.append(("MLX", call_mlx))
        print("✓ MLX available")
    except Exception:
        print("✗ MLX not available")

    if not backends:
        print("No backends available!")
        sys.exit(1)

    # Define tests
    tests = [
        # (name, messages, score_fn)
        ("LDA-short (10KB)",
         [{"role": "user", "content": LDA_PASS1_PROMPT + short_doc[:10000]}],
         lambda c: score_lda(c, 10000)),

        ("LDA-long (40KB)",
         [{"role": "user", "content": LDA_PASS1_PROMPT + long_doc[:20000]}],
         lambda c: score_lda(c, 40000)),

        ("Memo-short",
         [{"role": "user", "content": MEMO_PROMPT + short_doc[:5000]}],
         score_memo),

        ("Memo-long",
         [{"role": "user", "content": MEMO_PROMPT + long_doc[:5000]}],
         score_memo),

        ("Review-comments",
         [{"role": "user", "content": REVIEW_COMMENTS_PROMPT + short_doc[:8000]}],
         lambda c: score_review(c, "comments")),

        ("Review-redline",
         [{"role": "user", "content": REVIEW_REDLINE_PROMPT + short_doc[:5000]}],
         lambda c: score_review(c, "redline")),

        ("Contract-draft",
         [{"role": "user", "content": DRAFT_PROMPT}],
         score_draft),
    ]

    # Run all tests
    all_results = []
    for backend_name, backend_fn in backends:
        print(f"\n{'='*60}")
        print(f"Backend: {backend_name}")
        print(f"{'='*60}")
        for test_name, messages, score_fn in tests:
            result = run_test(backend_fn, backend_name, test_name, messages, score_fn)
            all_results.append(result)

    # Summary table
    print(f"\n{'='*80}")
    print("BENCHMARK RESULTS SUMMARY")
    print(f"{'='*80}")
    print(f"{'Test':<22} {'Backend':<8} {'Score':<7} {'t/s':<8} {'Latency':<10} {'OutLen':<8}")
    print("-" * 80)

    for r in all_results:
        print(f"{r['test']:<22} {r['backend']:<8} {r['score']}/10   {r['tps']:<8.1f} {r['latency']:<10.1f} {r['output_len']:<8}")

    # Aggregate per backend
    print(f"\n{'='*60}")
    print("AGGREGATE")
    print(f"{'='*60}")
    for backend_name, _ in backends:
        br = [r for r in all_results if r["backend"] == backend_name]
        total_score = sum(r["score"] for r in br)
        avg_tps = sum(r["tps"] for r in br) / len(br) if br else 0
        avg_lat = sum(r["latency"] for r in br) / len(br) if br else 0
        max_score = len(br) * 10
        print(f"{backend_name}: {total_score}/{max_score} | avg {avg_tps:.1f} t/s | avg {avg_lat:.1f}s latency")

    # Save results
    output_path = Path("/tmp/benchmark_results.json")
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nDetailed results saved to {output_path}")

    # Recommendation
    print(f"\n{'='*60}")
    print("RECOMMENDATION")
    print(f"{'='*60}")
    if len(backends) == 2:
        ollama_r = [r for r in all_results if r["backend"] == "Ollama"]
        mlx_r = [r for r in all_results if r["backend"] == "MLX"]
        o_score = sum(r["score"] for r in ollama_r)
        m_score = sum(r["score"] for r in mlx_r)
        o_tps = sum(r["tps"] for r in ollama_r) / len(ollama_r)
        m_tps = sum(r["tps"] for r in mlx_r) / len(mlx_r)

        if m_score >= o_score and m_tps > o_tps * 1.5:
            print(f"→ MLX is the clear winner: equal/better quality ({m_score} vs {o_score}) and {m_tps/o_tps:.1f}x faster")
        elif o_score > m_score + 5:
            print(f"→ Ollama (GGUF Q5) wins on quality ({o_score} vs {m_score}), despite being slower ({o_tps:.0f} vs {m_tps:.0f} t/s)")
        elif m_tps > o_tps * 2:
            print(f"→ MLX recommended: comparable quality ({m_score} vs {o_score}) but {m_tps/o_tps:.1f}x faster")
        else:
            print(f"→ Close call. Ollama: {o_score} score, {o_tps:.0f} t/s | MLX: {m_score} score, {m_tps:.0f} t/s")
            print("  Consider MLX if speed matters more, Ollama if quality edge is critical")


if __name__ == "__main__":
    main()
