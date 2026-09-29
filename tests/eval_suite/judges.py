"""
The multi-model judge panel.

Each turn is scored independently by every model in ``config.JUDGE_PANEL`` and
the results are reconciled by majority vote in ``models.Turn``. Using three
different model families means a single model's quirk (over-generosity,
self-preference toward its own family's prose, or a systematic blind spot about
Roman Urdu) cannot silently set the score for the whole run. Where the panel
disagrees widely, ``Turn.score_spread`` flags the turn for human review rather
than pretending the average is meaningful.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from tests.eval_suite import config
from tests.eval_suite.llm import LLMError, call_json
from tests.eval_suite.models import DimensionScore, JudgeVerdict, LLMCall, Persona, Turn
from tests.eval_suite.rubric import VERDICT_SCHEMA, build_judge_prompt

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = build_judge_prompt()

# State keys worth showing the judge. The full state object is huge and mostly
# noise; a judge distracted by 50 nulls scores worse.
_STATE_KEYS = (
    "intent", "sales_stage", "discovery_next_slot", "required_steps",
    "selected_product_id", "event_type", "product_type", "color", "size", "budget",
    "wedding_date", "inventory_result", "negotiation_result", "negotiation_state",
    "measurement_result", "handover_result", "handover_pending", "customer_contact",
    "custom_image_url", "catalog_search_note", "checkout_hand_off_note",
    "product_interest_note", "product_variation_note", "available_colors_summary",
    "selected_variation_name", "selected_product_variation_name",
    "shown_product_ids",
)


def _trim_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        k: product.get(k)
        for k in (
            "product_id", "name", "category", "price", "currency", "available_colors",
            "available_sizes", "stock_quantity", "lead_time_days", "fabric",
            "is_bespoke_available",
        )
        if product.get(k) not in (None, [], "")
    }


def build_turn_payload(turn: Turn, persona: Persona, history: list[Turn]) -> str:
    """The evidence pack handed to each judge."""
    state = turn.state or {}

    transcript = []
    for past in history[-5:]:
        transcript.append(f"CUSTOMER: {past.user_message}")
        transcript.append(f"BOT: {past.bot_reply}")

    context: dict[str, Any] = {
        k: state[k] for k in _STATE_KEYS if state.get(k) not in (None, [], {}, "", False)
    }
    products = state.get("products") or []
    if products:
        context["products_returned_by_tools"] = [_trim_product(p) for p in products[:4]]
    details = state.get("product_details")
    if isinstance(details, dict) and details:
        context["product_details"] = _trim_product(details)
    variations = state.get("product_variations") or state.get("category_variations") or []
    if variations:
        context["variations_available"] = [
            {k: v.get(k) for k in ("id", "name", "price", "stock_quantity") if v.get(k) is not None}
            for v in variations[:8]
            if isinstance(v, dict)
        ]

    return f"""## Persona being simulated
{persona.name} — {persona.headline} (expected language register: {persona.language})

## Conversation so far
{chr(10).join(transcript) if transcript else "(this is the first turn)"}

## THE TURN UNDER REVIEW
CUSTOMER (turn {turn.index}): {turn.user_message}

BOT REPLY: {turn.bot_reply}

## Machine-readable evidence
executed_nodes: {turn.executed_nodes}
image_returned: {bool(turn.image_url)}
latency_ms: {turn.latency_ms:.0f}

tool_context_available_to_the_bot:
{json.dumps(context, indent=2, default=str, ensure_ascii=False)[:6000]}

Judge the BOT REPLY only. Any fact in the reply that is absent from
tool_context_available_to_the_bot is ungrounded."""


def _clamp(score: Any) -> float | None:
    """Keep scores on the 1-10 scale.

    Some models answer a "1-10" rubric with 0, or normalise to 0-1. Clamping
    rather than discarding keeps their (correct) judgement that a turn was
    terrible, without letting a stray 0.1 drag a mean below the floor.
    """
    if not isinstance(score, (int, float)):
        return None
    return float(min(10.0, max(1.0, float(score))))


def _parse(model: str, data: dict[str, Any], raw: str = "") -> JudgeVerdict:
    dims: dict[str, DimensionScore] = {}
    for name, payload in (data.get("dimensions") or {}).items():
        if not isinstance(payload, dict):
            continue
        verdict = payload.get("verdict") or "na"
        dims[name] = DimensionScore(
            score=None if verdict == "na" else _clamp(payload.get("score")),
            verdict=verdict,
            note=(payload.get("note") or "")[:600],
        )
    return JudgeVerdict(
        model=model,
        overall_score=_clamp(data.get("overall_score")),
        overall_pass=data.get("overall_pass"),
        dimensions=dims,
        defects=[
            {
                "severity": (d.get("severity") or "minor"),
                "dimension": (d.get("dimension") or "general"),
                "description": (d.get("description") or "")[:600],
            }
            for d in (data.get("defects") or [])
            if isinstance(d, dict)
        ],
        coaching=(data.get("coaching") or "")[:600],
        parse_ok=True,
        raw=raw[:400],
    )


async def _judge_once(model: str, payload: str) -> tuple[JudgeVerdict, LLMCall]:
    try:
        data, telemetry = await call_json(
            model,
            [
                {"role": "system", "content": _JUDGE_PROMPT},
                {"role": "user", "content": payload},
            ],
            role="judge",
            schema=VERDICT_SCHEMA,
            temperature=config.JUDGE_TEMPERATURE,
            max_tokens=config.JUDGE_MAX_TOKENS,
        )
        return _parse(model, data), telemetry
    except Exception as exc:
        # One judge failing must never end a session; the panel carries on and
        # the failure is reported in judge_health.
        logger.warning("Judge %s failed: %s", model, exc)
        return (
            JudgeVerdict(model=model, parse_ok=False, error=str(exc)[:400]),
            LLMCall(model=model, role="judge", error=str(exc)[:400]),
        )


async def judge_turn(
    turn: Turn,
    persona: Persona,
    history: list[Turn],
    panel: tuple[str, ...] | None = None,
) -> None:
    """Score `turn` in place with the whole panel, in parallel."""
    panel = panel or config.JUDGE_PANEL
    payload = build_turn_payload(turn, persona, history)
    results = await asyncio.gather(*[_judge_once(m, payload) for m in panel])
    for verdict, telemetry in results:
        turn.verdicts.append(verdict)
        turn.llm_calls.append(telemetry)
