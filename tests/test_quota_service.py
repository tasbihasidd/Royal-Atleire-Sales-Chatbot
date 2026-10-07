"""Daily quota service — RA /limits + Option A today counts."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.quota_service import (
    clear_limits_cache,
    fetch_limits,
    hardcoded_limits,
    karachi_today_bounds_utc,
    require_quota,
)


def setup_function():
    clear_limits_cache()


def teardown_function():
    clear_limits_cache()


def test_hardcoded_limits_defaults():
    limits = hardcoded_limits()
    assert limits["no_of_ai_messages_perday"] >= 1
    assert limits["no_of_custom_images_perday"] >= 1


def test_karachi_today_bounds_are_utc_and_one_day():
    start, end = karachi_today_bounds_utc()
    assert start.tzinfo is not None
    assert end.tzinfo is not None
    assert (end - start) == timedelta(days=1)


def test_fetch_limits_from_backend_daily_fields(monkeypatch):
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_QUOTA_USE_BACKEND", True)
    mock_api = MagicMock()
    mock_api.get_chatbot_quotas = AsyncMock(
        return_value={
            "success": True,
            "limits": {
                "no_of_ai_messages_perday": 50,
                "no_of_custom_images_perday": 10,
            },
        }
    )
    with patch("app.services.backend_api.backend_api", mock_api):
        limits, source = asyncio.run(fetch_limits(force=True))
    assert source == "royal_attire"
    assert limits["no_of_ai_messages_perday"] == 50
    assert limits["no_of_custom_images_perday"] == 10


def test_require_quota_blocks_ai_when_at_limit(monkeypatch):
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_QUOTA_ENFORCE", True)
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_AI_MESSAGES_PER_DAY", 2)
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_CUSTOM_IMAGES_PER_DAY", 2)
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_QUOTA_USE_BACKEND", False)

    with patch(
        "app.services.quota_service.get_used_today",
        new=AsyncMock(return_value={"ai_messages_today": 2, "custom_images_today": 0}),
    ):
        decision = asyncio.run(require_quota("s-quota", "ai_message"))
    assert decision.allowed is False
    assert decision.kind == "ai_message"
    assert decision.message


def test_require_quota_allows_under_limit(monkeypatch):
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_QUOTA_ENFORCE", True)
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_AI_MESSAGES_PER_DAY", 10)
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_QUOTA_USE_BACKEND", False)

    with patch(
        "app.services.quota_service.get_used_today",
        new=AsyncMock(return_value={"ai_messages_today": 3, "custom_images_today": 0}),
    ):
        decision = asyncio.run(require_quota("s-quota", "ai_message"))
    assert decision.allowed is True
    assert decision.remaining["ai_messages"] == 7


def test_require_quota_image_block(monkeypatch):
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_QUOTA_ENFORCE", True)
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_CUSTOM_IMAGES_PER_DAY", 2)
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_QUOTA_USE_BACKEND", False)

    with patch(
        "app.services.quota_service.get_used_today",
        new=AsyncMock(return_value={"ai_messages_today": 0, "custom_images_today": 2}),
    ):
        decision = asyncio.run(require_quota("s-quota", "custom_image"))
    assert decision.allowed is False


def test_enforce_off_always_allows(monkeypatch):
    monkeypatch.setattr("app.services.quota_service.settings.CHATBOT_QUOTA_ENFORCE", False)
    with patch(
        "app.services.quota_service.get_used_today",
        new=AsyncMock(return_value={"ai_messages_today": 999, "custom_images_today": 999}),
    ):
        decision = asyncio.run(require_quota("s-quota", "ai_message"))
    assert decision.allowed is True


def test_reset_quota_today_calls_waive(monkeypatch):
    from app.services.quota_service import reset_quota_today

    waive_img = AsyncMock(return_value=2)
    waive_msg = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "app.services.image_store.image_store.waive_images_between",
        waive_img,
    )
    monkeypatch.setattr(
        "app.services.chat_store.chat_store.waive_user_messages_between",
        waive_msg,
    )
    with patch(
        "app.services.quota_service.get_quota_status",
        new=AsyncMock(
            return_value={
                "session_id": "s1",
                "used": {"ai_messages_today": 0, "custom_images_today": 0},
                "remaining": {"ai_messages": 10, "custom_images": 2},
            }
        ),
    ):
        result = asyncio.run(reset_quota_today("s1", kind="custom_image"))
    assert result["status"] == "ok"
    assert result["waived"]["custom_images"] == 2
    waive_img.assert_awaited_once()
    waive_msg.assert_not_awaited()
