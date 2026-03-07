# Qwen3.5-35B-A3B Model Benchmark Results

**Date:** 2026-03-07
**Hardware:** Mac Mini M4 Pro, 64GB RAM

Comparing fine-tuned (Unsloth 4-bit LoRA, RunPod RTX PRO 6000, 45 steps, loss 1.34) vs base Qwen3.5-35B-A3B across LDA, Legal Drafting, and Agentic reasoning tasks.

## Summary (v2 — Fixed Modelfiles)

The v1 benchmark showed fine-tuned models as broken. **Root cause: missing `RENDERER`/`PARSER` directives in the Ollama Modelfile.** After adding `RENDERER qwen3-vl-thinking` and `PARSER qwen3-vl-thinking`, the fine-tuned models work correctly.

| Model | LDA Simple | LDA Complex | Resolution | Memo | Planning | Tool Use | **Total** | Avg t/s |
|-------|-----------|-------------|------------|------|----------|----------|-----------|---------|
| Base Ollama | 5 | 1* | 5 | 5 | 5 | 5 | 26/30 | 17.0 |
| Fine-tuned Q4 | 5 | 1* | 5 | 5 | 5 | 5 | 26/30 | 17.4 |
| **Fine-tuned Q5** | **5** | **5** | **5** | **5** | **5** | **5** | **30/30** | **15.8** |
| Base MLX | 5 | 1* | 5 | 5 | 5 | 5 | 26/30 | 48.0 |

*\* = Hit 4000 token limit during thinking on complex input. Fine-tuned Q5 completed within budget.*

### Winner: Fine-tuned Q5 (Ollama)

The fine-tuned Q5_K_M model is the only one to score perfect 30/30. It successfully handled the complex multi-entity LDA task that every other model ran out of thinking budget on, suggesting the fine-tuning made the model more **efficient** at LDA tasks (less thinking overhead, more direct output).

## Key Findings

1. **Fine-tuned models ARE working** — the v1 failures were caused by missing `RENDERER qwen3-vl-thinking` / `PARSER qwen3-vl-thinking` in the Ollama Modelfile, not corrupted weights.

2. **Fine-tuned Q5 is the best model** — perfect 5/5 on all tests. The Q5 quantization preserves more weight precision than Q4, and the fine-tuning made the model more efficient at LDA (less thinking overhead).

3. **Token budget matters** — Qwen3.5 uses ~1000-3000 tokens for thinking before producing content. With `num_predict: 4000`, most models run out of budget on complex inputs. The fine-tuned Q5 is more efficient with its thinking budget.

4. **MLX is 3x faster** (48 vs 16-17 t/s) but serves the base model only (no fine-tune). MLX also leaks thinking into the content field.

## Detailed Results (v2)

### LDA: Simple PII Replacement

| Model | Score | Latency | t/s | Notes |
|-------|-------|---------|-----|-------|
| Base Ollama | 5/5 | 221.1s | 8.2 | All entities with mapping |
| Fine-tuned Q4 | 5/5 | 124.9s | 15.9 | All entities with mapping |
| **Fine-tuned Q5** | **5/5** | **146.6s** | **13.3** | **All entities with mapping** |
| Base MLX | 5/5 | 27.1s | 46.7 | All entities with mapping |

### LDA: Multi-entity Employment Clause

| Model | Score | Latency | t/s | Notes |
|-------|-------|---------|-----|-------|
| Base Ollama | 1/5 | 230.7s | 17.3 | Hit token limit, empty content |
| Fine-tuned Q4 | 1/5 | 230.1s | 17.4 | Hit token limit, empty content |
| **Fine-tuned Q5** | **5/5** | **148.0s** | **15.7** | **6 persons, 2 orgs, 4 addresses, 2 emails** |
| Base MLX | 1/5 | 41.4s | 48.3 | Hit token limit, empty content |

### Drafting: Board Resolution

| Model | Score | Latency | t/s | Notes |
|-------|-------|---------|-----|-------|
| Base Ollama | 5/5 | 128.2s | 17.3 | WHEREAS + RESOLVED + dollar cap |
| Fine-tuned Q4 | 5/5 | 148.4s | 17.4 | WHEREAS + RESOLVED + dollar cap |
| **Fine-tuned Q5** | **5/5** | **135.0s** | **15.8** | **WHEREAS + RESOLVED + dollar cap** |
| Base MLX | 5/5 | 40.8s | 49.0 | WHEREAS + RESOLVED + dollar cap |

### Drafting: Legal Memo Outline

| Model | Score | Latency | t/s | Notes |
|-------|-------|---------|-----|-------|
| Base Ollama | 5/5 | 195.5s | 17.3 | Well-structured, 7 key factors |
| Fine-tuned Q4 | 5/5 | 230.5s | 17.4 | Well-structured, 6 key factors |
| **Fine-tuned Q5** | **5/5** | **209.5s** | **15.7** | **Well-structured, 6 key factors** |
| Base MLX | 5/5 | 40.9s | 48.9 | Well-structured, 3 key factors |

### Agentic: Multi-step Task Planning

| Model | Score | Latency | t/s | Notes |
|-------|-------|---------|-----|-------|
| Base Ollama | 5/5 | 139.7s | 17.1 | Clear steps, 6 key items |
| Fine-tuned Q4 | 5/5 | 145.6s | 17.4 | Clear steps, 6 key items |
| **Fine-tuned Q5** | **5/5** | **159.0s** | **15.8** | **Clear steps, 7 key items** |
| Base MLX | 5/5 | 41.0s | 48.7 | Clear steps, 7 key items |

### Agentic: Tool Use / Function Calling

| Model | Score | Latency | t/s | Notes |
|-------|-------|---------|-----|-------|
| Base Ollama | 5/5 | 42.4s | 17.0 | check_calendar, draft_email, create_task |
| Fine-tuned Q4 | 5/5 | 30.2s | 17.1 | draft_email, create_task |
| **Fine-tuned Q5** | **5/5** | **44.4s** | **15.6** | **check_calendar, draft_email, create_task, search_documents** |
| Base MLX | 5/5 | 10.5s | 46.9 | draft_email, create_task |

## What Was Wrong (v1 → v2 Fix)

### The Bug

The original Modelfile was:
```
FROM ./qwen3.5-legal-q5_k_m.gguf
PARAMETER temperature 0.3
PARAMETER num_ctx 8192
```

### The Fix

The corrected Modelfile adds the critical RENDERER/PARSER directives that handle Qwen3.5's thinking tokens:
```
FROM ./qwen3.5-legal-q5_k_m.gguf
TEMPLATE {{ .Prompt }}
RENDERER qwen3-vl-thinking
PARSER qwen3-vl-thinking
PARAMETER temperature 1
PARAMETER top_k 20
PARAMETER top_p 0.95
PARAMETER num_ctx 8192
```

**Without RENDERER/PARSER:** Ollama treats thinking tokens as regular output, causing them to be mixed into the content field. This produces garbled/repetitive output.

**Without proper temperature/sampling:** `temperature 0.3` is too deterministic for this architecture. The base Ollama model uses `temperature 1, top_k 20, top_p 0.95`.

## Deployment

**Winner deployed as:** `qwen3.5-legal` (Q5_K_M, 24GB) on Ollama
- Associate agent: LDA document processing
- Spark agent: Hobbyist/creative tasks
- OpenClaw config: `ollama/qwen3.5-legal`

## Test Setup

- **Hardware:** Mac Mini M4 Pro, 64GB RAM
- **Ollama:** v0.17.7 (brew, upgraded from 0.17.1-rc1)
- **MLX:** v0.31.0, mlx-lm v0.31.0 (Python 3.14 venv)
- **Fine-tune:** Unsloth 4-bit LoRA, RunPod RTX PRO 6000 96GB, 45 steps (3 epochs)
- **Training data:** 117 examples (59 LDA + 23 drafting + 26 reasoning + 9 instruction)
- **Scoring:** 0=error, 1=degenerate/too short, 2=missed criteria, 3=partial, 4=good, 5=excellent
- **Token budget:** num_predict=4000 (Ollama native API), max_tokens=2000 (MLX OpenAI-compat)
