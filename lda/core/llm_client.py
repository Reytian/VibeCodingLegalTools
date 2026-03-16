"""
LLM API client — calls local MLX server (primary) with Ollama fallback.
Hardcoded for local deployment: no .env, no external API keys.

Uses fine-tuned Qwen3.5-legal for high-quality entity extraction.
MLX backend: ~30 t/s via OpenAI-compatible API on port 8801.
Ollama fallback: ~16 t/s via native API on port 11434.
"""

import os
import re
import json
import time
import logging
import requests

logger = logging.getLogger(__name__)

# MLX server (primary) — OpenAI-compatible API
MLX_BASE = os.environ.get("LDA_MLX_BASE", "http://127.0.0.1:8801")
MLX_MODEL = os.environ.get("LDA_MLX_MODEL", "/Users/claptrap/finetune/mlx-legal")

# Ollama (fallback)
OLLAMA_BASE = os.environ.get("LDA_OLLAMA_BASE", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("LDA_MODEL", "qwen3.5-legal")

# Backend selection: "mlx" (default), "ollama", or "auto" (mlx with ollama fallback)
LLM_BACKEND = os.environ.get("LDA_BACKEND", "auto")

SYSTEM_MSG = (
    "/no_think\n"
    "You are a precise legal document analysis assistant. "
    "Always return valid JSON as requested. "
    "Never include any text outside the JSON structure."
)

# Retry config
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds


def _call_mlx(messages, temperature=0.1):
    """Call MLX server via OpenAI-compatible API."""
    body = {
        "model": MLX_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_MSG}] + messages,
        "temperature": temperature,
        "max_tokens": 4096,
    }

    response = requests.post(
        f"{MLX_BASE}/v1/chat/completions",
        json=body,
        timeout=300,
    )
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]

    usage = data.get("usage", {})
    completion_tokens = usage.get("completion_tokens", 0)

    # Strip thinking tags (MLX returns them in content)
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    logger.info("MLX response: %d completion tokens, %d chars", completion_tokens, len(content))
    return content


def _call_ollama(messages, temperature=0.1):
    """Call Ollama via native API (fallback)."""
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

    response = requests.post(
        f"{OLLAMA_BASE}/api/chat",
        json=body,
        timeout=300,
    )
    response.raise_for_status()

    result = response.json()
    content = result.get("message", {}).get("content", "")

    duration_s = result.get("total_duration", 0) / 1e9
    logger.info("Ollama response in %.1fs (%d chars)", duration_s, len(content))

    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content


def call_llm(messages, temperature=0.1):
    """
    Call the local LLM and return the text response.

    Backend selection (LDA_BACKEND env var):
    - "mlx": MLX only (fast, 30+ t/s)
    - "ollama": Ollama only (slower, 16 t/s)
    - "auto" (default): Try MLX first, fall back to Ollama

    Args:
        messages: Chat messages list [{"role": "...", "content": "..."}, ...]
        temperature: Generation temperature (default 0.1 for deterministic extraction)

    Returns:
        LLM text response
    """
    logger.debug("Calling LLM (backend=%s) with %d messages", LLM_BACKEND, len(messages))

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if LLM_BACKEND == "ollama":
                return _call_ollama(messages, temperature)
            elif LLM_BACKEND == "mlx":
                return _call_mlx(messages, temperature)
            else:  # auto
                try:
                    return _call_mlx(messages, temperature)
                except Exception as mlx_err:
                    logger.warning("MLX failed (%s), falling back to Ollama", mlx_err)
                    return _call_ollama(messages, temperature)

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "LLM call attempt %d/%d failed (%s), retrying in %ds...",
                    attempt, MAX_RETRIES, type(e).__name__, delay,
                )
                time.sleep(delay)
            else:
                logger.error("LLM call failed after %d attempts: %s", MAX_RETRIES, e)

    raise last_error


def parse_json_response(text: str):
    """
    Extract JSON data from an LLM response.

    Args:
        text: Raw LLM response text

    Returns:
        Parsed dict or list
    """
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Fallback: strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fallback: extract JSON substring
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = text.find(start_char)
        if start_idx == -1:
            continue
        end_idx = text.rfind(end_char)
        if end_idx == -1 or end_idx <= start_idx:
            continue
        json_str = text[start_idx : end_idx + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Failed to parse JSON from LLM response:\n{text[:500]}")
