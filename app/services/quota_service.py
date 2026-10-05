"""Daily chatbot quotas — limits from RA (or hardcoded); used from today's history."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.config import settings

logger = logging.getLogger(__name__)

KARACHI = ZoneInfo("Asia/Karachi")
QuotaKind = Literal["ai_message", "custom_image"]

_limits_cache: dict[str, Any] | None = None
_limits_cache_at: float = 0.0
_LIMITS_TTL_S = 60.0


def clear_limits_cache() -> None:
    global _limits_cache, _limits_cache_at
    _limits_cache = None
    _limits_cache_at = 0.0

MSG_AI_EXCEEDED = (
    "Aaj ki AI chat limit poori ho chuki hai. "
    "Kal phir try karein, ya Style Consultant se WhatsApp par rabta karein."
)
MSG_IMAGE_EXCEEDED = (
    "Aaj ki custom design image limit poori ho chuki hai. "
    "Kal naya mockup generate kar sakte hain."
)


@dataclass
class QuotaDecision:
    allowed: bool
    kind: str
    limits: dict[str, int]
    used: dict[str, int]
    remaining: dict[str, int]
    resets_at: str
    message: str | None = None
    source: str = "hardcoded"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def karachi_today_bounds_utc() -> tuple[datetime, datetime]:
    """Return [start, end) of today in Asia/Karachi as UTC-aware datetimes."""
    now = datetime.now(KARACHI)
    start_local = datetime.combine(now.date(), dt_time.min, tzinfo=KARACHI)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def next_reset_iso() -> str:
    now = datetime.now(KARACHI)
    start_local = datetime.combine(now.date(), dt_time.min, tzinfo=KARACHI)
    return (start_local + timedelta(days=1)).isoformat()


def hardcoded_limits() -> dict[str, int]:
    return {
        "no_of_ai_messages_perday": int(settings.CHATBOT_AI_MESSAGES_PER_DAY),
        "no_of_custom_images_perday": int(settings.CHATBOT_CUSTOM_IMAGES_PER_DAY),
    }


async def fetch_limits(*, session_id: str | None = None, force: bool = False) -> tuple[dict[str, int], str]:
    """
    Return (limits, source).
    While Royal Attire GET is pending, defaults to hardcoded env values unless
    CHATBOT_QUOTA_USE_BACKEND=true.
    """
    global _limits_cache, _limits_cache_at
    now = time.monotonic()
    if (
        not force
        and _limits_cache is not None
        and (now - _limits_cache_at) < _LIMITS_TTL_S
    ):
        return dict(_limits_cache["limits"]), str(_limits_cache["source"])

    hardcoded = hardcoded_limits()
    source = "hardcoded"

    if settings.CHATBOT_QUOTA_USE_BACKEND:
        try:
            from app.services.backend_api import backend_api

            remote = await backend_api.get_chatbot_quotas(session_id=session_id)
            limits = remote.get("limits") if isinstance(remote, dict) else None
            if isinstance(limits, dict):
                msg_lim = int(
                    limits.get("no_of_ai_messages_perday")
                    or limits.get("ai_messages")
                    or hardcoded["no_of_ai_messages_perday"]
                )
                img_lim = int(
                    limits.get("no_of_custom_images_perday")
                    or limits.get("custom_images")
                    or hardcoded["no_of_custom_images_perday"]
                )
                hardcoded = {
                    "no_of_ai_messages_perday": max(0, msg_lim),
                    "no_of_custom_images_perday": max(0, img_lim),
                }
                source = "royal_attire"
        except Exception:
            logger.warning(
                "Chatbot quotas backend fetch failed — using hardcoded limits",
                exc_info=True,
            )
            source = "hardcoded_fallback"

    _limits_cache = {"limits": hardcoded, "source": source}
    _limits_cache_at = now
    return dict(hardcoded), source


async def get_used_today(session_id: str) -> dict[str, int]:
    from app.services.chat_store import chat_store
    from app.services.image_store import image_store

    start, end = karachi_today_bounds_utc()
    messages = await chat_store.count_user_messages_between(session_id, start, end)
    images = await image_store.count_images_between(session_id, start, end)
    return {"ai_messages_today": messages, "custom_images_today": images}


async def get_quota_status(session_id: str) -> dict[str, Any]:
    limits, source = await fetch_limits(session_id=session_id)
    used = await get_used_today(session_id)
    remaining = {
        "ai_messages": max(0, limits["no_of_ai_messages_perday"] - used["ai_messages_today"]),
        "custom_images": max(
            0, limits["no_of_custom_images_perday"] - used["custom_images_today"]
        ),
    }
    return {
        "session_id": session_id,
        "timezone": "Asia/Karachi",
        "limits": limits,
        "used": used,
        "remaining": remaining,
        "resets_at": next_reset_iso(),
        "source": source,
        "enforce": bool(settings.CHATBOT_QUOTA_ENFORCE),
    }


async def require_quota(session_id: str, kind: QuotaKind) -> QuotaDecision:
    """Allow if today's used for kind is still under limit (check before consuming)."""
    limits, source = await fetch_limits(session_id=session_id)
    used = await get_used_today(session_id)
    remaining = {
        "ai_messages": max(0, limits["no_of_ai_messages_perday"] - used["ai_messages_today"]),
        "custom_images": max(
            0, limits["no_of_custom_images_perday"] - used["custom_images_today"]
        ),
    }
    resets_at = next_reset_iso()

    if not settings.CHATBOT_QUOTA_ENFORCE:
        return QuotaDecision(
            allowed=True,
            kind=kind,
            limits=limits,
            used=used,
            remaining=remaining,
            resets_at=resets_at,
            source=source,
        )

    if kind == "ai_message":
        ok = used["ai_messages_today"] < limits["no_of_ai_messages_perday"]
        msg = None if ok else MSG_AI_EXCEEDED
    else:
        ok = used["custom_images_today"] < limits["no_of_custom_images_perday"]
        msg = None if ok else MSG_IMAGE_EXCEEDED

    return QuotaDecision(
        allowed=ok,
        kind=kind,
        limits=limits,
        used=used,
        remaining=remaining,
        resets_at=resets_at,
        message=msg,
        source=source,
    )
