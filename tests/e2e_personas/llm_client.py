"""
OpenRouter LLM client for testers and judge (DeepSeek v4.1 Flash).
"""
from __future__ import annotations

import json
import logging
import httpx

from tests.e2e_personas.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    TESTER_MODEL,
    JUDGE_MODEL,
    LLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_COMPLETIONS_URL = f"{OPENROUTER_BASE_URL}/chat/completions"
_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "http://localhost:8015",
    "X-Title": "Royal Atelier E2E Tester",
}


async def _call(model: str, messages: list[dict], temperature: float = 0.7) -> str:
    """Call OpenRouter chat completions and return the assistant content string."""
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1024,
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
        r = await client.post(_COMPLETIONS_URL, headers=_HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"].get("content") or ""
        return content.strip()


async def generate_user_message(system_prompt: str, conversation_history: list[dict]) -> str:
    """
    Ask the tester LLM to produce the next user message for the persona.
    Returns the raw text the persona would say.
    """
    messages = [{"role": "system", "content": system_prompt}] + conversation_history
    return await _call(TESTER_MODEL, messages, temperature=0.8)


async def evaluate_turn(judge_prompt: str, turn_data: str) -> dict:
    """
    Ask the judge LLM to evaluate a single conversation turn.
    Returns parsed JSON verdict dict.
    """
    messages = [
        {"role": "system", "content": judge_prompt},
        {"role": "user", "content": turn_data},
    ]
    raw = await _call(JUDGE_MODEL, messages, temperature=0.1)

    # Strip markdown fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n")
        last_fence = cleaned.rfind("```")
        cleaned = cleaned[first_newline + 1 : last_fence].strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Judge returned non-JSON, wrapping raw:\n%s", raw[:300])
        return {
            "overall_pass": None,
            "score": 0,
            "raw_judge_output": raw,
            "parse_error": True,
            "dimensions": {},
            "defects": [f"Judge output was not valid JSON: {raw[:200]}"],
            "suggestions": [],
        }
