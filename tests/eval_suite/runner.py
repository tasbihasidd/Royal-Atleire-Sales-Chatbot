"""
Session orchestration.

One session = one persona having a full conversation with the live chatbot, every
turn judged by the panel and asserted deterministically, then the persisted state
probed. Sessions run concurrently up to ``config.SESSION_CONCURRENCY``.

An HTTP failure does not abort the session — it is recorded as a critical defect
and the conversation continues, so one 500 on turn 3 no longer costs us the other
nine turns of signal.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx

from tests.eval_suite import assertions, config, judges, probes, simulated_user
from tests.eval_suite.models import Persona, Session, Turn

logger = logging.getLogger(__name__)


async def _post_chat(session_id: str, message: str) -> tuple[dict, int, str | None, float]:
    """POST /chat with retries. Returns (payload, status, error, latency_ms)."""
    last_error: str | None = None
    status = 0
    started = time.perf_counter()

    for attempt in range(1, config.CHAT_MAX_ATTEMPTS + 1):
        attempt_started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=config.CHAT_TIMEOUT_S) as client:
                resp = await client.post(
                    config.CHAT_URL,
                    json={"session_id": session_id, "message": message},
                )
            status = resp.status_code
            latency = (time.perf_counter() - attempt_started) * 1000
            if status == 200:
                return resp.json(), status, None, latency
            last_error = f"HTTP {status}: {resp.text[:300]}"
            # A 500 is a real product defect. Retrying tells us whether it is
            # deterministic, which is worth knowing, but we keep the first error.
            if status < 500:
                return {}, status, last_error, latency
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < config.CHAT_MAX_ATTEMPTS:
            await asyncio.sleep(config.RETRY_BACKOFF_S * attempt)

    return {}, status, last_error, (time.perf_counter() - started) * 1000


async def run_session(persona: Persona, *, panel: tuple[str, ...] | None = None, run_tag: str = "") -> Session:
    session_id = f"eval_{run_tag or 'x'}_{persona.name.lower()}_{uuid.uuid4().hex[:6]}"
    session = Session(
        persona=persona,
        session_id=session_id,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Isolation: never inherit state from a previous run.
    await probes.clear_session(session_id)

    max_turns = persona.max_turns or config.MAX_TURNS
    consecutive_errors = 0
    logger.info("[%s] start session_id=%s", persona.name, session_id)

    for index in range(1, max_turns + 1):
        message, should_end, sim_call = await simulated_user.next_message(persona, session.turns)
        if sim_call.error and not message:
            session.end_reason = f"user simulator failed on turn {index}: {sim_call.error}"
            break

        payload, status, error, latency = await _post_chat(session_id, message)

        turn = Turn(
            index=index,
            user_message=message,
            bot_reply=(payload.get("reply") or "") if not error else "",
            http_status=status,
            error=error,
            latency_ms=latency,
            image_url=payload.get("imageurl") or "",
            executed_nodes=list(payload.get("executed_nodes") or []),
            state=payload.get("state") or {},
        )
        turn.llm_calls.append(sim_call)

        history = list(session.turns)
        turn.assertions = assertions.check_turn(turn, persona, history)

        if error:
            consecutive_errors += 1
            logger.warning("[%s] turn %s failed: %s", persona.name, index, error)
            session.turns.append(turn)
            if consecutive_errors >= config.ABORT_SESSION_AFTER_CONSECUTIVE_ERRORS:
                session.end_reason = f"aborted after {consecutive_errors} consecutive chat errors"
                break
            continue

        consecutive_errors = 0
        await judges.judge_turn(turn, persona, history, panel=panel)
        session.turns.append(turn)

        logger.info(
            "[%s] turn %s score=%s nodes=%s latency=%.0fms",
            persona.name, index, turn.consensus_score, turn.executed_nodes, latency,
        )

        if should_end:
            session.end_reason = "persona ended the conversation"
            break
    else:
        session.end_reason = f"hit max_turns ({max_turns})"

    session.ended_at = datetime.now(timezone.utc).isoformat()
    session.probe = await probes.probe_session_state(session_id)
    session.session_assertions = assertions.check_session(session)
    logger.info(
        "[%s] done turns=%s mean_score=%s reason=%s",
        persona.name, len(session.turns), session.mean_score, session.end_reason,
    )
    return session


async def run_suite(
    personas: list[Persona],
    *,
    concurrency: int | None = None,
    panel: tuple[str, ...] | None = None,
    run_tag: str = "",
) -> list[Session]:
    semaphore = asyncio.Semaphore(concurrency or config.SESSION_CONCURRENCY)

    async def guarded(persona: Persona) -> Session:
        async with semaphore:
            try:
                return await run_session(persona, panel=panel, run_tag=run_tag)
            except Exception as exc:  # never let one persona kill the run
                logger.exception("Session crashed for %s", persona.name)
                failed = Session(
                    persona=persona,
                    session_id=f"eval_crashed_{persona.name.lower()}",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    end_reason=f"harness crash: {type(exc).__name__}: {exc}",
                )
                failed.session_assertions = assertions.check_session(failed)
                return failed

    return list(await asyncio.gather(*[guarded(p) for p in personas]))
