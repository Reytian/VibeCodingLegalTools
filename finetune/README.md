# Fine-Tuning Qwen3.5-35B-A3B for Legal Document Processing

This directory contains the training pipeline used to fine-tune [Qwen3.5-35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B) (a 35B-parameter MoE model with 3B active params per forward pass) for legal document anonymization (LDA), legal drafting, and agentic reasoning tasks.

## Model

**Base model:** `Qwen/Qwen3.5-35B-A3B` (Mixture of Experts, 256 experts, Mamba/SSM hybrid)

**Fine-tuned model:** `Reytian/qwen3.5-legal-q5_k_m-gguf` (Q5_K_M quantization, ~24GB)

**Method:** 4-bit LoRA via [Unsloth](https://github.com/unslothai/unsloth) on RunPod (RTX PRO 6000, 96GB VRAM)

**Training:** 45 steps (3 epochs), final loss 1.34, 117 training examples

## Training Data

| Category | Examples | Source |
|----------|----------|--------|
| LDA (anonymization) | 59 | SEC EDGAR employment agreements, compensation exhibits |
| Legal Drafting | 23 | Board resolutions, legal memos, opinions |
| Agentic Reasoning | 26 | Multi-step planning, tool use, function calling |
| Instruction Following | 9 | System prompt adherence, format compliance |
| **Total** | **117** | |

Raw documents sourced from SEC EDGAR (public filings). Training examples generated using Claude to create input-output pairs for each task type.

### Data Preparation Scripts

- `scripts/prepare_lda_dataset.py` — Generates LDA anonymization training pairs from raw legal documents
- `scripts/prepare_drafting_dataset.py` — Generates legal drafting examples
- `scripts/build_drafting_v2.py` — Improved drafting dataset with more variety
- `scripts/generate_training_v2.py` — V2 training data with better LDA examples
- `scripts/merge_and_split.py` — Merges all JSONL files into `train.jsonl` + `valid.jsonl` (90/10 split)

## Pipeline

### 1. Data Collection
```bash
# Raw EDGAR docs go in raw/
ls raw/  # employment_agreement_01.txt, merger_agreement_01.txt, etc.
```

### 2. Dataset Preparation
```bash
python3 scripts/prepare_lda_dataset.py       # → lda_train.jsonl
python3 scripts/prepare_drafting_dataset.py   # → drafting_train.jsonl
python3 scripts/merge_and_split.py            # → train.jsonl + valid.jsonl
```

### 3. Fine-Tuning (RunPod)

Upload `unsloth_finetune.ipynb` to RunPod with an RTX PRO 6000 (96GB VRAM). The notebook:
- Loads Qwen3.5-35B-A3B in 4-bit quantization via Unsloth
- Applies LoRA (rank 16) to attention layers
- Trains for 3 epochs on the 117-example dataset
- Exports to GGUF Q5_K_M format

### 4. Deployment

There are two inference backends. Both use the same fine-tuned model weights.

#### Option A: MLX (Recommended for Mac)

MLX is Apple's native ML framework. On Apple Silicon, it is ~2x faster than Ollama with better JSON reliability — recommended for all Mac users.

```bash
# Create a Python venv with mlx-lm
python3 -m venv mlx-env
source mlx-env/bin/activate
pip install mlx-lm

# Convert fine-tuned model to MLX 4-bit format (~16GB on disk)
python3 -m mlx_lm.convert \
  --hf-path Reytian/qwen3.5-legal-q5_k_m-gguf \
  --mlx-path ./mlx-legal \
  --quantize --q-bits 4

# Start the MLX server (OpenAI-compatible API)
python3 -m mlx_lm.server --model ./mlx-legal --port 8801
```

The LDA pipeline auto-detects MLX at `http://127.0.0.1:8801`. To use it:
```bash
# Auto mode (MLX first, Ollama fallback) — this is the default
export LDA_BACKEND=auto

# Force MLX only
export LDA_BACKEND=mlx

# Force Ollama only
export LDA_BACKEND=ollama
```

**Note:** MLX returns thinking tokens in the content field (no separate reasoning field). The LDA client automatically strips `<think>...</think>` tags from responses.

**Note on quantization:** Qwen3.5's MoE architecture (128 experts) means expert FFN layers are always quantized at 4-bit by `mlx_lm`, regardless of the `--q-bits` flag. The effective bits/weight is ~4.5 even with `--q-bits 6`. This is a limitation of the current MLX quantizer for MoE models, not a bug. On 32GB RAM, 4-bit is the practical ceiling.

#### Option B: Ollama (Cross-Platform)

Ollama works on macOS, Linux, and Windows. Use this if you're not on Apple Silicon or prefer Ollama's ecosystem.

```bash
# Download GGUF to your machine
# Create Modelfile (CRITICAL: must include RENDERER/PARSER for thinking tokens)
cat > Modelfile << 'EOF'
FROM ./qwen3.5-legal-q5_k_m.gguf
TEMPLATE {{ .Prompt }}
RENDERER qwen3-vl-thinking
PARSER qwen3-vl-thinking
PARAMETER temperature 1
PARAMETER top_k 20
PARAMETER top_p 0.95
PARAMETER num_ctx 8192
EOF

# Register with Ollama
ollama create qwen3.5-legal -f Modelfile
```

**Important:** Without `RENDERER qwen3-vl-thinking` and `PARSER qwen3-vl-thinking`, Ollama treats thinking tokens as regular output, producing garbled/repetitive text. This was the root cause of initial deployment failures.

## Benchmark Results

Tested on Mac Mini M4 Pro (32GB RAM). The fine-tuned Q5 model achieved **perfect 30/30** across all tests.

| Model | LDA Simple | LDA Complex | Resolution | Memo | Planning | Tool Use | **Total** | Avg t/s |
|-------|-----------|-------------|------------|------|----------|----------|-----------|---------:|
| Base Ollama | 5 | 1* | 5 | 5 | 5 | 5 | 26/30 | 17.0 |
| Fine-tuned Q4 | 5 | 1* | 5 | 5 | 5 | 5 | 26/30 | 17.4 |
| **Fine-tuned Q5** | **5** | **5** | **5** | **5** | **5** | **5** | **30/30** | **15.8** |
| Base MLX | 5 | 1* | 5 | 5 | 5 | 5 | 26/30 | 48.0 |

\* = Hit 4000 token limit during thinking on complex input. Fine-tuned Q5 completed within budget.

The fine-tuning made the model more **efficient** at LDA tasks — less thinking overhead, more direct output — allowing it to handle complex multi-entity documents that base models ran out of token budget on.

### Performance

#### Backend Comparison (Mac Mini M4 Pro, 32GB RAM)

| Backend | Avg Speed | Avg Latency (10KB) | JSON Reliability | RAM Usage |
|---------|-----------|---------------------|-------------------|-----------|
| **MLX 4-bit** | **30.5 t/s** | **~16s** | **97% (68/70)** | ~16GB |
| Ollama Q5_K_M | 15.8 t/s | ~68s | 56% (39/70) | ~26GB |

MLX is 2x faster with 4.8x lower latency. The JSON reliability difference comes from MLX's cleaner handling of the model's thinking token architecture. Both backends use the same model weights — the performance gap is purely from the inference runtime.

**Important:** MLX and Ollama cannot run simultaneously on 32GB RAM (16GB + 26GB = 42GB). Choose one as your primary backend. With `LDA_BACKEND=auto` (default), the pipeline tries MLX first and falls back to Ollama only if MLX is unavailable.

Full benchmark script: [`benchmark/benchmark_ollama_vs_mlx.py`](../benchmark/benchmark_ollama_vs_mlx.py)

## Hardware Requirements

| Use Case | Hardware | Notes |
|----------|----------|-------|
| **Inference (MLX 4-bit)** | Apple Silicon Mac, 32GB+ RAM | 16GB for model, best performance |
| **Inference (Ollama Q5_K_M)** | Apple Silicon Mac, 32GB+ RAM | 26GB for model, cross-platform |
| **Inference (Ollama Q4_K_M)** | Apple Silicon Mac, 32GB RAM | 20GB for model |
| **Training** | GPU with 48GB+ VRAM | RTX PRO 6000, A100, etc. |

## Key Lessons

1. **Ollama Modelfile**: `RENDERER` and `PARSER` directives are essential for Qwen3.5's thinking token architecture. Without them, output is broken.
2. **Temperature**: Use `temperature 1` with `top_k 20, top_p 0.95` (matching base model). `temperature 0.3` is too deterministic for this architecture.
3. **Token budget**: Qwen3.5 uses 1000-3000 tokens for thinking. Set `num_predict` to 4000+ for complex tasks. Use `reasoning_effort: "low"` in production to reduce thinking overhead.
4. **Q5 > Q4 for fine-tuned models**: Q5_K_M preserves more weight precision, which matters more for fine-tuned weights than base weights.
5. **Training quant vs export quant are independent**: Train in 4-bit (fits in VRAM) → export as Q5_K_M (best quality that fits in RAM).
