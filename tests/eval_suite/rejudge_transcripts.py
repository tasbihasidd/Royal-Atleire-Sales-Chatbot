"""
Re-judge salvaged transcripts without re-calling the chatbot.

Use when a run completed conversations but crashed before writing reports
(e.g. judge JSONDecodeError). Input: JSON from salvaged_run1.json shape, or
any {persona: {session_id, messages: [{role, content, metadata}]}} map.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.eval_suite import assertions, config, judges, personas, reporting  # noqa: E402
from tests.eval_suite.models import Persona, Session, Turn  # noqa: E402

logger = logging.getLogger("rejudge")


def _turns_from_messages(messages: list[dict], persona: Persona) -> list[Turn]:
    """Pair user/assistant messages into Turns, carrying metadata as state."""
    turns: list[Turn] = []
    pending_user: str | None = None
    index = 0
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "user":
            pending_user = content
            continue
        if role == "assistant" and pending_user is not None:
            index += 1
            meta = msg.get("metadata") or {}
            state = {
                k: meta.get(k)
                for k in (
                    "intent", "sales_stage", "discovery_next_slot", "required_steps",
                    "selected_product_id", "event_type", "product_type", "color", "size",
                    "budget", "wedding_date", "inventory_result", "negotiation_result",
                    "negotiation_state", "measurement_result", "handover_result",
                    "handover_pending", "customer_contact", "custom_image_url",
                    "catalog_search_note", "checkout_hand_off_note", "product_interest_note",
                    "product_variation_note", "available_colors_summary",
                    "selected_variation_name", "selected_product_variation_name",
                    "shown_product_ids", "products", "product_details", "product_variations",
                    "category_variations", "fabrics",
                )
                if meta.get(k) is not None
            }
            turn = Turn(
                index=index,
                user_message=pending_user,
                bot_reply=content,
                http_status=200,
                latency_ms=0.0,
                image_url=meta.get("imageurl") or "",
                executed_nodes=list(meta.get("executed_nodes") or []),
                state=state,
            )
            history = list(turns)
            turn.assertions = assertions.check_turn(turn, persona, history)
            turns.append(turn)
            pending_user = None
    return turns


async def rejudge_persona(name: str, blob: dict, panel: tuple[str, ...]) -> Session:
    persona = personas.PERSONAS_BY_NAME[name]
    session = Session(
        persona=persona,
        session_id=blob["session_id"],
        started_at=datetime.now(timezone.utc).isoformat(),
        end_reason="rejudged from salvaged transcript",
    )
    session.turns = _turns_from_messages(blob.get("messages") or [], persona)
    logger.info("[%s] %s turns to rejudge", name, len(session.turns))

    for i, turn in enumerate(session.turns):
        history = session.turns[:i]
        await judges.judge_turn(turn, persona, history, panel=panel)
        logger.info("[%s] turn %s score=%s", name, turn.index, turn.consensus_score)

    session.ended_at = datetime.now(timezone.utc).isoformat()
    session.session_assertions = assertions.check_session(session)
    return session


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="salvaged JSON path")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--single-judge", action="store_true")
    parser.add_argument("--stamp", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    data = json.loads(args.input.read_text())
    panel = (config.FAST_JUDGE,) if args.single_judge else config.JUDGE_PANEL
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(name: str, blob: dict) -> Session:
        async with sem:
            return await rejudge_persona(name, blob, panel)

    sessions = await asyncio.gather(*[guarded(n, b) for n, b in data.items()])
    stamp = args.stamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_salvaged"
    meta = {
        "source": "rejudged_salvaged_transcripts",
        "input": str(args.input),
        "judge_panel": ", ".join(panel),
        "personas_run": len(sessions),
    }
    artifacts = reporting.write_reports(list(sessions), meta, stamp=stamp)
    print(f"Wrote {artifacts['markdown']}")
    print(f"Wrote {artifacts['json']}")
    print(f"mean={artifacts['summary'].get('mean_score')} turns={artifacts['summary'].get('turns')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
