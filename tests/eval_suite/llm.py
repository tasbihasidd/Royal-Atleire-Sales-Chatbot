"""
OpenRouter client for the evaluation suite.

Three things the old harness lacked and which this module provides:

1. **Strict schema output.** Judges are called with `response_format:
   json_schema`, so a verdict either arrives as valid JSON or the request fails
   loudly. The previous harness lost 95% of its verdicts to truncated JSON.
2. **Retries with backoff** on transport errors, 429 and 5xx, plus a retry when
   a model returns empty content (some reasoning models spend their whole token
   budget on hidden reasoning and return "").
3. **Cost and latency accounting** on every call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx

from tests.eval_suite import config
from tests.eval_suite.models import LLMCall

logger = logging.getLogger(__name__)

_HEADERS = {
    "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "http://localhost:8015",
    "X-Title": "Royal Atelier Eval Suite",
}

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


class LLMError(RuntimeError):
    pass


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = config.PRICING.get(model)
    if not price:
        return 0.0
    return (prompt_tokens * price["prompt"] + completion_tokens * price["completion"]) / 1_000_000


async def call(
    model: str,
    messages: list[dict[str, str]],
    *,
    role: str,
    temperature: float,
    max_tokens: int,
    json_schema: dict[str, Any] | None = None,
) -> tuple[str, LLMCall]:
    """Call OpenRouter once (with internal retries) and return (text, telemetry)."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "verdict", "strict": True, "schema": json_schema},
        }

    telemetry = LLMCall(model=model, role=role)
    started = time.perf_counter()
    last_error: str = "unknown"

    for attempt in range(1, config.LLM_MAX_ATTEMPTS + 1):
        telemetry.attempts = attempt
        try:
            async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_S) as client:
                resp = await client.post(config.COMPLETIONS_URL, headers=_HEADERS, json=body)

            if resp.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                raise LLMError(last_error)
            if resp.status_code != 200:
                # Non-retryable (e.g. 400 bad schema) — fail fast, it will not fix itself.
                telemetry.error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                telemetry.latency_ms = (time.perf_counter() - started) * 1000
                raise LLMError(telemetry.error)

            payload = resp.json()
            choice = (payload.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""

            usage = payload.get("usage") or {}
            telemetry.prompt_tokens = int(usage.get("prompt_tokens") or 0)
            telemetry.completion_tokens = int(usage.get("completion_tokens") or 0)
            telemetry.cost_usd = float(
                usage.get("cost")
                or _estimate_cost(model, telemetry.prompt_tokens, telemetry.completion_tokens)
            )

            if not text.strip():
                # Empty content with a finish_reason of "length" means the model
                # spent everything on hidden reasoning. Raising max_tokens is the
                # only useful response.
                last_error = f"empty content (finish_reason={choice.get('finish_reason')})"
                body["max_tokens"] = min(int(body["max_tokens"] * 2), 1600)
                raise LLMError(last_error)

            telemetry.latency_ms = (time.perf_counter() - started) * 1000
            return text.strip(), telemetry

        except LLMError:
            if telemetry.error:  # non-retryable
                raise
            if attempt == config.LLM_MAX_ATTEMPTS:
                break
            await asyncio.sleep(config.RETRY_BACKOFF_S * attempt + random.uniform(0, 0.75))
        except Exception as exc:  # transport / JSON decode
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == config.LLM_MAX_ATTEMPTS:
                break
            await asyncio.sleep(config.RETRY_BACKOFF_S * attempt + random.uniform(0, 0.75))

    telemetry.latency_ms = (time.perf_counter() - started) * 1000
    telemetry.error = last_error
    raise LLMError(f"{model} failed after {config.LLM_MAX_ATTEMPTS} attempts: {last_error}")


async def call_json(
    model: str,
    messages: list[dict[str, str]],
    *,
    role: str,
    schema: dict[str, Any],
    temperature: float = 0.0,
    max_tokens: int = config.JUDGE_MAX_TOKENS,
) -> tuple[dict[str, Any], LLMCall]:
    """Strict-JSON variant. Falls back to brace extraction if a provider ignores
    the schema, so one sloppy provider cannot zero out a whole run."""
    text, telemetry = await call(
        model,
        messages,
        role=role,
        temperature=temperature,
        max_tokens=max_tokens,
        json_schema=schema,
    )
    try:
        return json.loads(text), telemetry
    except json.JSONDecodeError:
        pass

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    start, end = cleaned.find("{"), cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start:end]), telemetry
        except json.JSONDecodeError as exc:
            # Must not escape as JSONDecodeError: callers handle LLMError only,
            # and an unhandled decode error takes down the whole session.
            raise LLMError(f"{model} emitted malformed JSON ({exc}): {text[:300]}") from exc
    raise LLMError(f"{model} returned no JSON object: {text[:300]}")
