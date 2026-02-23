"""
LLM API client — calls local Ollama via native /api/chat endpoint.
Hardcoded for local deployment: no .env, no external API keys.

Uses native Ollama API for better control over context window,
generation parameters, and thinking mode.
"""

import re
import json
import logging
import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
LLM_MODEL = "qwen3:30b-a3b"

SYSTEM_MSG = (
    "You are a precise legal document analysis assistant. "
    "Always return valid JSON as requested. "
    "Never wrap JSON in markdown code fences. "
    "Do not include any text outside the JSON structure."
)


def call_llm(messages: list[dict], temperature: float = 0.1) -> str:
    """
    Call the local Ollama LLM via native API and return the text response.

    Appends /no_think to user messages to disable Qwen3's thinking mode.

    Args:
        messages: Chat messages list [{"role": "...", "content": "..."}, ...]
        temperature: Generation temperature (default 0.1 for deterministic extraction)

    Returns:
        LLM text response (with <think> blocks stripped)
    """
    # Add system message and append /no_think to user messages
    processed = [{"role": "system", "content": SYSTEM_MSG}]
    for msg in messages:
        if msg["role"] == "user":
            processed.append({
                "role": "user",
                "content": msg["content"] + "\n/no_think",
            })
        else:
            processed.append(msg)

    body = {
        "model": LLM_MODEL,
        "messages": processed,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 16384,
            "num_predict": 4096,
        },
    }

    logger.debug("Calling LLM with %d messages", len(messages))

    response = requests.post(
        f"{OLLAMA_BASE}/api/chat",
        json=body,
        timeout=600,
    )
    response.raise_for_status()

    result = response.json()
    content = result.get("message", {}).get("content", "")

    duration_s = result.get("total_duration", 0) / 1e9
    logger.info("LLM response in %.1fs (%d chars)", duration_s, len(content))

    # Strip Qwen3 <think>...</think> blocks (in case they still appear)
    content = _strip_think_blocks(content)

    if not content:
        logger.warning("LLM returned empty content. Raw response keys: %s", list(result.keys()))

    return content


def _strip_think_blocks(text: str) -> str:
    """Remove Qwen3 thinking mode <think>...</think> blocks from response."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def parse_json_response(text: str) -> dict | list:
    """
    Extract JSON data from an LLM response.

    Handles various formats: raw JSON, markdown-wrapped JSON,
    JSON embedded in explanatory text, and Qwen3 think blocks.

    Args:
        text: Raw LLM response text

    Returns:
        Parsed dict or list
    """
    # Strip think blocks first
    text = _strip_think_blocks(text)

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

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
