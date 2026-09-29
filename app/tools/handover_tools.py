from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from app.core.logging_config import safe_len
from app.services.backend_api import backend_api

logger = logging.getLogger(__name__)


@tool
async def create_human_handover(
    session_id: str,
    reason: str,
    customer_preferences: dict[str, Any],
    customer_contact: dict[str, Any] | None = None,
    conversation_summary: str | None = None,
    handover_reason: str | None = None,
) -> dict[str, Any]:
    """
    Create a human style-consultant handover ticket using backend API.
    """
    contact = customer_contact or {}
    logger.info(
        "create_human_handover start session_id=%s reason_length=%s preference_keys=%s "
        "contact_name_present=%s summary_length=%s handover_reason=%s",
        session_id,
        len(reason),
        list(customer_preferences.keys()),
        bool(contact.get("name")),
        safe_len(conversation_summary),
        handover_reason,
    )
    payload = {
        "session_id": session_id,
        "reason": reason,
        "customer_preferences": customer_preferences,
        "customer_contact": contact,
        "conversation_summary": conversation_summary or "",
        "handover_reason": handover_reason,
    }
    try:
        result = await backend_api.create_handover(payload)
        logger.info(
            "create_human_handover success session_id=%s ticket_id=%s status=%s",
            session_id,
            result.get("ticket_id"),
            result.get("status"),
        )
        return result
    except Exception:
        logger.exception("create_human_handover failed session_id=%s", session_id)
        raise
