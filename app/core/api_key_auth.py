"""Shared X-Api-Key gate for chatbot AI / public write APIs."""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

# Paths that stay open without X-Api-Key (health probes / static assets).
_PUBLIC_EXACT = frozenset({"/", "/health"})
_PUBLIC_PREFIXES = ("/static/",)


def api_keys_match(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    try:
        return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except Exception:
        return False


def extract_api_key(request: Request) -> str:
    # Prefer canonical header; accept common aliases.
    for name in ("x-api-key", "X-Api-Key", "X-API-KEY"):
        val = request.headers.get(name)
        if val and str(val).strip():
            return str(val).strip()
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def require_chatbot_api_key(request: Request) -> None:
    """Raise 401/503 if the request is not authorized (for Depends() use)."""
    expected = (settings.CHATBOT_API_KEY or "").strip()
    if not expected:
        if settings.is_prod:
            raise HTTPException(
                status_code=503,
                detail="CHATBOT_API_KEY is not configured on the server.",
            )
        return
    provided = extract_api_key(request)
    if not api_keys_match(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Api-Key.")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path or "/"
        if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        expected = (settings.CHATBOT_API_KEY or "").strip()
        if not expected:
            if settings.is_prod:
                logger.error("CHATBOT_API_KEY missing in production — rejecting %s", path)
                return JSONResponse(
                    status_code=503,
                    content={"detail": "CHATBOT_API_KEY is not configured on the server."},
                )
            # Dev without key: open (local testing). Set CHATBOT_API_KEY to exercise the gate.
            return await call_next(request)

        provided = extract_api_key(request)
        if not api_keys_match(provided, expected):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing X-Api-Key."},
            )
        return await call_next(request)
