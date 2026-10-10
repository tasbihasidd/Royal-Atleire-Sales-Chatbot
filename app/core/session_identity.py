"""Resolve chatbot session id from cookie (prod) or request body (dev)."""

from __future__ import annotations

import logging
import re

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


def normalize_session_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    sid = str(raw).strip()
    if not sid:
        return None
    if len(sid) > 255 or not _SESSION_ID_RE.match(sid):
        raise HTTPException(status_code=422, detail="Invalid session_id format.")
    return sid


def resolve_session_id(request: Request, body_session_id: str | None = None) -> str:
    """
    Prod: prefer HttpOnly cookie (SESSION_COOKIE_NAME), optional body fallback.
    Dev: cookie if present, else body session_id (localStorage flow).
    """
    cookie_name = settings.SESSION_COOKIE_NAME
    cookie_sid = normalize_session_id(request.cookies.get(cookie_name))
    body_sid = normalize_session_id(body_session_id)

    if cookie_sid:
        if body_sid and body_sid != cookie_sid:
            logger.info(
                "Session cookie wins over body cookie=%s body=%s",
                cookie_sid[:16],
                body_sid[:16],
            )
        return cookie_sid

    if settings.is_prod and settings.SESSION_REQUIRE_COOKIE_IN_PROD:
        raise HTTPException(
            status_code=401,
            detail="Session cookie required. Sign in / refresh the page and try again.",
        )

    if body_sid:
        if settings.is_prod:
            logger.warning(
                "Prod chat using body session_id (cookie missing) session_id=%s",
                body_sid[:24],
            )
        return body_sid

    raise HTTPException(
        status_code=422,
        detail="session_id is required (cookie or body).",
    )


def is_unsigned_cart_checkout_url(url: str | None) -> bool:
    """True when URL uses insecure ?cart= base64 payload instead of ?s= session code."""
    if not url:
        return False
    lower = str(url).lower()
    return "?cart=" in lower or "&cart=" in lower
