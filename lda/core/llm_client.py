"""
LLM API client — calls local Ollama via native /api/chat endpoint.
Hardcoded for local deployment: no .env, no external API keys.

Uses Qwen3.5 35B (A3B) for high-quality entity extraction.
Uses Ollama's format=json for guaranteed valid JSON output.
"""

import os
import re
import json
import time
import logging
import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
LLM_MODEL = os.environ.get("LDA_MODEL", "qwen3.5:35b-a3b")

SYSTEM_MSG = (
    "You are a precise legal document analysis assistant. "
    "Always return valid JSON as requested. "
    "Never include any text outside the JSON structure."
)

# Retry config for transient Ollama errors (model swapping, etc.)
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds


def call_llm(messages: list[dict], temperature: float = 0.1) -> str:
    """
    Call the local Ollama LLM via native API and return the text response.

    Uses format=json to guarantee structured output and prevent
    thinking mode from consuming the generation budget.
    Retries up to 3 times with exponential backoff for transient errors.

    Args:
        messages: Chat messages list [{"role": "...", "content": "..."}, ...]
        temperature: Generation temperature (default 0.1 for deterministic extraction)

    Returns:
        LLM text response (guaranteed valid JSON string)
    """
    processed = [{"role": "system", "content": SYSTEM_MSG}]
    for msg in messages:
        processed.append(msg)

    body = {
        "model": LLM_MODEL,
        "messages": processed,
        "stream": False,
        "format": "json",
        "think": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 32768,
            "num_predict": 4096,
        },
    }

    logger.debug("Calling LLM (%s) with %d messages", LLM_MODEL, len(messages))

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                f"{OLLAMA_BASE}/api/chat",
                json=body,
                timeout=300,
            )
            response.raise_for_status()

            result = response.json()
            content = result.get("message", {}).get("content", "")

            duration_s = result.get("total_duration", 0) / 1e9
            logger.info("LLM response in %.1fs (%d chars)", duration_s, len(content))

            if not content:
                logger.warning("LLM returned empty content. Raw response keys: %s", list(result.keys()))

            return content

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


def parse_json_response(text: str) -> dict | list:
    """
    Extract JSON data from an LLM response.

    With format=json enabled, the response should already be valid JSON.
    Fallback parsing is kept for robustness.

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
