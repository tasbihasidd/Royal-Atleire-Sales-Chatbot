"""
Infrastructure and persistence probes.

These answer questions the chat API cannot: did the turn actually land in
Postgres, is Redis holding what it claims to hold, do the two agree, and is the
upstream catalogue API healthy. Because the chatbot silently degrades when Redis
or the backend is down, a green conversation score means nothing without these.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from tests.eval_suite import config
from tests.eval_suite.models import StateProbe

logger = logging.getLogger(__name__)


async def preflight() -> dict[str, Any]:
    """Check every dependency before burning money on LLM calls."""
    report: dict[str, Any] = {"ok": True, "checks": {}}

    def record(name: str, ok: bool, detail: Any, fatal: bool = True) -> None:
        report["checks"][name] = {"ok": ok, "detail": detail}
        if not ok and fatal:
            report["ok"] = False

    # Chatbot
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(config.HEALTH_URL)
        record("chatbot_health", resp.status_code == 200, f"HTTP {resp.status_code} {resp.text[:120]}")
    except Exception as exc:
        record("chatbot_health", False, f"{type(exc).__name__}: {exc}")

    # Postgres
    try:
        import asyncpg

        dsn = config.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn, timeout=10)
        tables = [r[0] for r in await conn.fetch(
            "select tablename from pg_tables where schemaname='public' order by 1"
        )]
        await conn.close()
        record("postgres", True, {"tables": tables})
    except Exception as exc:
        record("postgres", False, f"{type(exc).__name__}: {exc}")

    # Redis — non-fatal, because the app is designed to degrade without it. That
    # degradation is itself a finding, so we record it either way.
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(config.REDIS_URL, decode_responses=True, socket_connect_timeout=3)
        pong = await client.ping()
        size = await client.dbsize()
        await client.aclose()
        record("redis", bool(pong), {"dbsize": size}, fatal=False)
    except Exception as exc:
        record("redis", False, f"{type(exc).__name__}: {exc}", fatal=False)

    # Upstream catalogue API
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{config.BACKEND_API_BASE_URL.rstrip('/')}/products/search",
                json={"limit": 1},
            )
        products = (resp.json() or {}).get("products") or []
        record(
            "backend_catalog",
            resp.status_code == 200 and bool(products),
            {"status": resp.status_code, "products": len(products)},
            fatal=False,
        )
    except Exception as exc:
        record("backend_catalog", False, f"{type(exc).__name__}: {exc}", fatal=False)

    # LLM provider
    try:
        from tests.eval_suite.llm import call

        text, telemetry = await call(
            config.FAST_JUDGE,
            [{"role": "user", "content": "Reply with the single word: ready"}],
            role="judge",
            temperature=0,
            max_tokens=20,
        )
        record("llm_provider", "ready" in text.lower(), {"model": config.FAST_JUDGE, "reply": text[:60]})
    except Exception as exc:
        record("llm_provider", False, f"{type(exc).__name__}: {exc}")

    return report


async def clear_session(session_id: str) -> bool:
    """Guarantee session isolation before a run."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(config.CLEAR_SESSION_URL.format(session_id=session_id))
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Could not clear session %s: %s", session_id, exc)
        return False


async def probe_session_state(session_id: str) -> StateProbe:
    """Inspect what actually persisted for this session."""
    probe = StateProbe()

    try:
        import asyncpg

        dsn = config.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn, timeout=10)
        try:
            probe.db_messages = await conn.fetchval(
                "select count(*) from chat_messages where session_id=$1", session_id
            ) or 0

            profile_row = await conn.fetchrow(
                "select profile_json, negotiation_json from user_profiles where session_id=$1",
                session_id,
            )
            if profile_row:
                probe.db_profile_present = True
                raw = profile_row["profile_json"]
                profile = json.loads(raw) if isinstance(raw, str) else (raw or {})
                probe.db_profile = {
                    k: v for k, v in profile.items() if v not in (None, [], {}, "")
                }

            session_row = await conn.fetchrow(
                "select metadata_json from chat_sessions where session_id=$1", session_id
            )
            if session_row and session_row["metadata_json"]:
                raw = session_row["metadata_json"]
                meta = json.loads(raw) if isinstance(raw, str) else raw
                probe.db_session_context_keys = sorted((meta.get("session_context") or {}).keys())
        finally:
            await conn.close()
    except Exception as exc:
        probe.notes.append(f"postgres probe failed: {type(exc).__name__}: {exc}")

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(config.REDIS_URL, decode_responses=True, socket_connect_timeout=3)
        cached = await client.get(f"session_profile:{session_id}")
        await client.aclose()
        probe.redis_key_present = cached is not None
        if cached and probe.db_profile_present:
            payload = json.loads(cached).get("profile") or {}
            interesting = ("event_type", "budget", "sales_stage", "jacket_size", "product_type")
            probe.redis_profile_matches_db = all(
                payload.get(k) == probe.db_profile.get(k) for k in interesting
            )
            if probe.redis_profile_matches_db is False:
                probe.notes.append(
                    "Redis cache and Postgres profile disagree on "
                    + ", ".join(
                        f"{k}(redis={payload.get(k)!r} db={probe.db_profile.get(k)!r})"
                        for k in interesting
                        if payload.get(k) != probe.db_profile.get(k)
                    )
                )
    except Exception as exc:
        probe.notes.append(f"redis probe failed: {type(exc).__name__}: {exc}")

    return probe
