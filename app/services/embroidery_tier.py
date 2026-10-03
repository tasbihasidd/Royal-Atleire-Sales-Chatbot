"""Infer embroidery tier for pricing from the customer chat via LLM."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)

EmbroideryTier = Literal["None", "Light", "Medium", "Heavy"]
VALID_TIERS: frozenset[str] = frozenset(("None", "Light", "Medium", "Heavy"))

_SYSTEM = """\
You are classifying embroidery amount for Turabees wedding menswear pricing.

Read the customer's messages and choose exactly one tier:
- None — they clearly want no embroidery (zero / without / plain / bina kaam).
- Light — minimal, minimalist, subtle, halka kaam, or light embroidery.
- Medium — moderate embroidery, or embroidery mentioned without a clear strength.
- Heavy — heavy embroidery, zardozi, full work, zyada kaam, ornate.

If embroidery is never discussed, choose Light (atelier default). Do not choose None
unless they explicitly refuse embroidery.

Reply with JSON only:
{"embroidery_tier":"None"|"Light"|"Medium"|"Heavy","reason":"one short sentence"}
"""


def _chat_context(state: dict[str, Any], *, max_turns: int = 16) -> str:
    chunks: list[str] = []
    for key in ("user_message", "custom_instructions"):
        value = state.get(key)
        if value:
            chunks.append(str(value))

    profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    for key in ("embroidery", "embroidery_tier", "notes", "preferences"):
        value = profile.get(key)
        if value:
            chunks.append(str(value))

    for message in (state.get("messages") or [])[-max_turns:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        if role not in ("user", "human"):
            continue
        text = message.get("content") or message.get("text")
        if text:
            chunks.append(str(text))

    return "\n".join(chunks).strip()


def parse_embroidery_response(raw: str) -> tuple[EmbroideryTier | None, str | None]:
    """Parse model JSON into (tier, reason)."""
    text = (raw or "").strip()
    if not text:
        return None, None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None, None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None, None
    if not isinstance(data, dict):
        return None, None

    tier_raw = str(data.get("embroidery_tier") or "").strip()
    tier: EmbroideryTier | None = None
    for valid in VALID_TIERS:
        if tier_raw.lower() == valid.lower():
            tier = valid  # type: ignore[assignment]
            break
    reason = data.get("reason")
    return tier, str(reason) if reason else None


async def infer_embroidery_tier(state: dict[str, Any]) -> dict[str, Any]:
    """Classify embroidery_tier from chat. Falls back to profile, then Light."""
    profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    cached = profile.get("embroidery_tier")
    context = _chat_context(state)

    if not context and cached in VALID_TIERS:
        return {"embroidery_tier": cached, "source": "profile"}

    if context:
        try:
            from app.services.llm import acall_llm

            raw = await acall_llm(
                _SYSTEM,
                f"Customer chat:\n{context[:4000]}\n\nClassify embroidery_tier.",
                json_mode=True,
                max_tokens=120,
                retries=1,
            )
            tier, reason = parse_embroidery_response(raw)
            if tier:
                out: dict[str, Any] = {"embroidery_tier": tier, "source": "llm"}
                if reason:
                    out["reason"] = reason
                return out
            logger.warning("embroidery_tier unparseable LLM output: %s", (raw or "")[:200])
        except Exception as exc:  # noqa: BLE001
            logger.info("embroidery_tier LLM unavailable: %s", exc)

    if cached in VALID_TIERS:
        return {"embroidery_tier": cached, "source": "profile"}

    return {"embroidery_tier": "Light", "source": "default"}
