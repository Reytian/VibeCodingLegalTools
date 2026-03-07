#!/bin/bash
# Benchmark v2: Fixed fine-tuned models with proper RENDERER/PARSER
set -e
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

cat > /tmp/bench_v2.py << 'PYEOF'
import json, sys, time, subprocess, re

TESTS = {
    "lda_simple": {
        "category": "LDA", "name": "Simple PII Replacement",
        "system": "You are a legal document anonymizer. Identify all personally identifiable information (PII) in the document and replace each instance with a typed placeholder. Use these placeholder types: {PERSON_N} for personal names, {ORG_N} for company/organization names, {ADDRESS_N} for street addresses, {DATE_N} for specific dates, {AMOUNT_N} for monetary amounts, {PHONE_N} for phone numbers, {EMAIL_N} for email addresses, {TITLE_N} for job titles. Maintain consistent numbering. After the anonymized text, include a mapping section.",
        "user": "John Smith, CEO of Acme Corp, located at 123 Main Street, New York, NY 10001, will receive an annual salary of $500,000 effective January 1, 2025. His emergency contact is Jane Smith at (212) 555-0199.",
    },
    "lda_complex": {
        "category": "LDA", "name": "Multi-entity Employment Clause",
        "system": "You are a legal document anonymizer. Replace all PII with typed placeholders: {PERSON_N}, {ORG_N}, {ADDRESS_N}, {DATE_N}, {AMOUNT_N}, {PHONE_N}, {EMAIL_N}, {TITLE_N}. Include a mapping section.",
        "user": "EMPLOYMENT AGREEMENT between GlobalTech Industries, Inc., a Delaware corporation with principal offices at 500 Park Avenue, Suite 2100, New York, NY 10022 (Company), and Dr. Sarah Chen, residing at 45 Riverside Drive, Apt 12B, New York, NY 10024 (Executive). The Company hereby employs Executive as Chief Technology Officer, reporting to CEO Michael Rodriguez, effective March 15, 2025, at a base salary of $450,000 per annum. Executive shall also receive a signing bonus of $75,000. For questions, contact HR Director Lisa Park at lisa.park@globaltech.com or (646) 555-0234.",
    },
    "drafting_resolution": {
        "category": "Drafting", "name": "Board Resolution",
        "system": "You are a legal drafting assistant for a New York-based cross-border law firm.",
        "user": "Draft a brief board resolution authorizing the CEO to execute a consulting agreement with an outside technology firm, not to exceed $50,000.",
    },
    "drafting_memo": {
        "category": "Drafting", "name": "Legal Memo Outline",
        "system": "You are a legal drafting assistant for a New York-based cross-border law firm.",
        "user": "Draft an outline for a legal memorandum analyzing whether a non-compete clause in an employment agreement is enforceable under New York law. Include the key factors courts consider.",
    },
    "agentic_planning": {
        "category": "Agentic", "name": "Multi-step Task Planning",
        "system": "You are an AI assistant that helps with complex multi-step tasks. When given a task, break it down into clear, numbered steps and explain your reasoning.",
        "user": "I need to onboard a new client for a cross-border M&A deal between a US company and a Chinese target. What are the key steps I should take in the first week?",
    },
    "agentic_tool_use": {
        "category": "Agentic", "name": "Tool Use / Function Calling",
        "system": 'You are an AI assistant with access to tools. When you need to use a tool, output a JSON function call in this format: {"tool": "tool_name", "args": {...}}. Available tools: search_documents(query), draft_email(to, subject, body), check_calendar(date), create_task(title, due_date, assignee).',
        "user": "Schedule a meeting with the legal team for next Monday to discuss the merger agreement, then draft an email to partner Sarah Chen summarizing the key issues we need to address.",
    }
}

def query_ollama_native(model, system_prompt, user_prompt, num_predict=4000, timeout=300):
    """Query via Ollama native API (handles thinking properly)."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False,
        "options": {"num_predict": num_predict}
    })
    start = time.time()
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout),
             "http://127.0.0.1:11434/api/chat",
             "-d", payload],
            capture_output=True, text=True, timeout=timeout+10
        )
        elapsed = time.time() - start
        if result.returncode != 0:
            return {"error": f"curl failed: {result.stderr}", "latency": elapsed}
        data = json.loads(result.stdout)
        if "error" in data:
            return {"error": str(data["error"]), "latency": elapsed}
        msg = data.get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("reasoning", "")
        eval_count = data.get("eval_count", 0)
        tps = eval_count / elapsed if elapsed > 0 else 0
        return {
            "content": content,
            "reasoning_len": len(reasoning),
            "latency": round(elapsed, 1),
            "tps": round(tps, 1),
            "tokens": eval_count
        }
    except Exception as e:
        return {"error": str(e), "latency": round(time.time() - start, 1)}

def query_mlx(model, system_prompt, user_prompt, port=8801, timeout=180):
    """Query MLX server via OpenAI-compat API."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 2000,
        "temperature": 0.7,
        "stream": False
    })
    start = time.time()
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout),
             f"http://127.0.0.1:{port}/v1/chat/completions",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=timeout+10
        )
        elapsed = time.time() - start
        if result.returncode != 0:
            return {"error": f"curl failed: {result.stderr}", "latency": elapsed}
        data = json.loads(result.stdout)
        if "error" in data:
            return {"error": str(data["error"]), "latency": elapsed}
        content = data["choices"][0]["message"]["content"]
        # Strip thinking from MLX output
        content_clean = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        # Also strip "Thinking Process:" blocks
        content_clean = re.sub(r'^Thinking Process:.*?(?=\n(?:##|#|\*\*|[A-Z]{2,}|\{))', '', content_clean, flags=re.DOTALL).strip()
        usage = data.get("usage", {})
        comp_tokens = usage.get("completion_tokens", len(content.split()))
        tps = comp_tokens / elapsed if elapsed > 0 else 0
        return {
            "content": content_clean,
            "latency": round(elapsed, 1),
            "tps": round(tps, 1),
            "tokens": comp_tokens
        }
    except Exception as e:
        return {"error": str(e), "latency": round(time.time() - start, 1)}

def score(test_id, result):
    if "error" in result:
        return 0, result["error"]
    content = result["content"]
    if len(content) < 30:
        return 1, f"Response too short ({len(content)} chars)"
    words = content.split()
    if len(words) > 20:
        unique = len(set(words)) / len(words)
        if unique < 0.3:
            return 1, "Degenerate repetition"

    test = TESTS[test_id]
    cat = test["category"]

    if cat == "LDA":
        has_placeholders = "{PERSON_" in content or "{ORG_" in content
        has_mapping = "mapping" in content.lower() or "|" in content or ":" in content
        if test_id == "lda_complex":
            person_count = content.count("{PERSON_")
            org_count = content.count("{ORG_")
            addr_count = content.count("{ADDRESS_")
            email_count = content.count("{EMAIL_")
            total = person_count + org_count + addr_count + email_count
            if has_placeholders and person_count >= 3 and total >= 8:
                return 5, f"Excellent: {person_count}P/{org_count}O/{addr_count}A/{email_count}E"
            elif has_placeholders and person_count >= 2:
                return 4, f"Good: {person_count}P/{org_count}O/{addr_count}A"
            elif has_placeholders:
                return 3, f"Partial: {total} entities"
        else:
            person_count = content.count("{PERSON_")
            if has_placeholders and has_mapping and person_count >= 2:
                return 5, "All entities with mapping"
            elif has_placeholders and has_mapping:
                return 4, "Correct placeholders with mapping"
            elif has_placeholders:
                return 3, "Placeholders but no mapping"
        return 2, "No placeholders found"

    elif cat == "Drafting":
        if test_id == "drafting_resolution":
            has_resolved = "RESOLVED" in content.upper() or "resolved" in content.lower()
            has_whereas = "WHEREAS" in content.upper()
            has_amount = "$50,000" in content or "50,000" in content
            if has_resolved and has_amount and has_whereas:
                return 5, "Full resolution with WHEREAS + RESOLVED + cap"
            elif has_resolved and has_amount:
                return 5, "Proper resolution with dollar cap"
            elif has_resolved or has_whereas:
                return 4, "Resolution format present"
            return 2, "No RESOLVED clause"
        else:
            has_structure = any(x in content.lower() for x in ["i.", "1.", "question presented", "issue", "memorandum"])
            factors = ["reasonable", "geographic", "duration", "legitimate", "hardship", "scope", "time"]
            has_factors = sum(1 for x in factors if x in content.lower())
            if has_structure and has_factors >= 3:
                return 5, f"Well-structured, {has_factors} key factors"
            elif has_structure and has_factors >= 1:
                return 4, f"Structured, {has_factors} factors"
            elif has_factors >= 1:
                return 3, "Some factors but weak structure"
            return 2, "Missing key analysis"

    elif cat == "Agentic":
        if test_id == "agentic_planning":
            has_steps = any(f"{i}." in content or f"{i})" in content for i in range(1, 6))
            keywords = ["conflict", "engagement", "kyc", "aml", "team", "jurisdiction", "due diligence", "regulatory", "cfius", "antitrust"]
            key_items = sum(1 for x in keywords if x in content.lower())
            if has_steps and key_items >= 4:
                return 5, f"Clear steps, {key_items} key items"
            elif has_steps and key_items >= 2:
                return 4, f"Steps present, {key_items} key items"
            elif has_steps:
                return 3, "Steps but few key items"
            return 2, "No structured steps"
        else:
            tools_found = [x for x in ["check_calendar", "draft_email", "create_task", "search_documents"] if x in content.lower()]
            has_json = "{" in content and ("tool" in content.lower() or any(t in content for t in tools_found))
            if len(tools_found) >= 2 and has_json:
                return 5, f"Valid tool calls: {', '.join(tools_found)}"
            elif len(tools_found) >= 1 and has_json:
                return 4, f"Tool call: {', '.join(tools_found)}"
            elif tools_found:
                return 3, f"Tool refs but no JSON: {', '.join(tools_found)}"
            return 2, "No tool usage"

    return 2, "Unknown"

# Main
model_label = sys.argv[1]
backend = sys.argv[2]  # "ollama" or "mlx"
model_name = sys.argv[3]
port = int(sys.argv[4]) if len(sys.argv) > 4 else 11434

print(f"\nTesting: {model_label} ({backend})", flush=True)
print("=" * 60, flush=True)

model_results = {}
for test_id, test in TESTS.items():
    print(f"  [{test['category']}] {test['name']}...", end=" ", flush=True)

    if backend == "ollama":
        result = query_ollama_native(model_name, test["system"], test["user"])
    else:
        result = query_mlx(model_name, test["system"], test["user"], port=port)

    s, note = score(test_id, result)
    result["score"] = s
    result["note"] = note
    if "content" in result:
        result["preview"] = result["content"][:500]
    model_results[test_id] = result

    lat = result.get("latency", "?")
    tps = result.get("tps", "?")
    print(f"Score: {s}/5 | {lat}s | {tps} t/s | {note}", flush=True)

# Merge results
try:
    with open("/tmp/benchmark_v2.json") as f:
        all_results = json.load(f)
except:
    all_results = {}

all_results[model_label] = model_results
with open("/tmp/benchmark_v2.json", "w") as f:
    json.dump(all_results, f, indent=2, default=str)

print(f"\nResults saved for {model_label}", flush=True)
PYEOF

echo '{}' > /tmp/benchmark_v2.json

# Phase 1: Base Ollama
echo "Phase 1: Base Ollama"
ollama stop qwen3.5-legal-q4 2>/dev/null || true
ollama stop qwen3.5-legal 2>/dev/null || true
pkill -f mlx_lm 2>/dev/null || true
sleep 3
python3 /tmp/bench_v2.py "Base Ollama (Q4)" ollama "qwen3.5:35b-a3b"

# Phase 2: Fine-tuned Q4
echo ""
echo "Phase 2: Fine-tuned Q4"
ollama stop "qwen3.5:35b-a3b" 2>/dev/null || true
sleep 5
python3 /tmp/bench_v2.py "Fine-tuned Q4 (Ollama)" ollama "qwen3.5-legal-q4"

# Phase 3: Fine-tuned Q5
echo ""
echo "Phase 3: Fine-tuned Q5"
ollama stop qwen3.5-legal-q4 2>/dev/null || true
sleep 5
python3 /tmp/bench_v2.py "Fine-tuned Q5 (Ollama)" ollama "qwen3.5-legal"

# Phase 4: Base MLX
echo ""
echo "Phase 4: Base MLX"
ollama stop qwen3.5-legal 2>/dev/null || true
sleep 5
source ~/finetune/mlx-env/bin/activate
nohup python3 -m mlx_lm.server --model mlx-community/Qwen3.5-35B-A3B-4bit --port 8801 > /tmp/mlx-bench.log 2>&1 &
MLX_PID=$!
sleep 20
if curl -s http://127.0.0.1:8801/v1/models > /dev/null 2>&1; then
    python3 /tmp/bench_v2.py "Base MLX (Q4)" mlx "mlx-community/Qwen3.5-35B-A3B-4bit" 8801
else
    echo "MLX server failed"
    cat /tmp/mlx-bench.log
fi
kill $MLX_PID 2>/dev/null || true

echo ""
echo "=== ALL BENCHMARKS COMPLETE ==="
