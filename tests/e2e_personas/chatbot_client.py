"""
Async HTTP client for the live Royal Atelier chatbot.
"""
from __future__ import annotations

import logging
import httpx

from tests.e2e_personas.config import (
    CHATBOT_CHAT_URL,
    CHATBOT_HEALTH_URL,
    HTTP_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


async def health_check() -> bool:
    """Return True if the chatbot server responds to /health."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(CHATBOT_HEALTH_URL)
            return r.status_code == 200
    except Exception:
        return False


async def send_message(session_id: str, message: str) -> dict:
    """
    POST /chat and return the full parsed JSON response.
    Returns dict with keys: reply, imageurl, state, executed_nodes
    """
    payload = {"session_id": session_id, "message": message}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        logger.debug("→ POST /chat  session=%s  msg=%s", session_id, message[:80])
        r = await client.post(CHATBOT_CHAT_URL, json=payload)
        r.raise_for_status()
        data = r.json()
        logger.debug(
            "← reply_len=%s  nodes=%s",
            len(data.get("reply", "")),
            data.get("executed_nodes"),
        )
        return data
