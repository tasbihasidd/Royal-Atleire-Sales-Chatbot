"""Parse fal any-llm responses for text + token usage (or estimate)."""

from __future__ import annotations

from typing import Any

from app.services.cost_tracking import estimate_tokens


def output_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("output") or result.get("text") or "")
    return str(getattr(result, "output", "") or getattr(result, "text", "") or "")


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_usage(result: Any) -> dict[str, int] | None:
    """Return {prompt_tokens, completion_tokens, total_tokens} if present on fal payload."""
    if result is None:
        return None
    data = result if isinstance(result, dict) else getattr(result, "__dict__", None)
    if not isinstance(data, dict):
        return None

    usage = data.get("usage") or data.get("token_usage") or data.get("tokens")
    if isinstance(usage, dict):
        prompt = (
            _as_int(usage.get("prompt_tokens"))
            or _as_int(usage.get("input_tokens"))
            or _as_int(usage.get("prompt"))
        )
        completion = (
            _as_int(usage.get("completion_tokens"))
            or _as_int(usage.get("output_tokens"))
            or _as_int(usage.get("completion"))
        )
        total = _as_int(usage.get("total_tokens")) or _as_int(usage.get("total"))
        if prompt is not None or completion is not None:
            p = prompt or 0
            c = completion or 0
            return {
                "prompt_tokens": p,
                "completion_tokens": c,
                "total_tokens": total if total is not None else p + c,
            }

    prompt = _as_int(data.get("prompt_tokens")) or _as_int(data.get("input_tokens"))
    completion = _as_int(data.get("completion_tokens")) or _as_int(data.get("output_tokens"))
    if prompt is not None or completion is not None:
        p = prompt or 0
        c = completion or 0
        return {
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": p + c,
        }
    return None


def parse_fal_llm_result(
    result: Any,
    *,
    system: str = "",
    user: str = "",
) -> tuple[str, int, int, bool]:
    """
    Returns (text, prompt_tokens, completion_tokens, estimated).
    Falls back to char/4 estimates when fal omits usage.
    """
    text = output_text(result)
    usage = extract_usage(result)
    if usage:
        return (
            text,
            int(usage["prompt_tokens"]),
            int(usage["completion_tokens"]),
            False,
        )
    prompt_tokens = estimate_tokens(f"{system}\n{user}".strip())
    completion_tokens = estimate_tokens(text)
    return text, prompt_tokens, completion_tokens, True
