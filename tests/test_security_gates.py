"""Security gates: CORS, session identity, checkout URL, LRU cache, API key."""

from __future__ import annotations

from app.config import settings
from app.core.api_key_auth import api_keys_match
from app.core.bounded_cache import LRUDict
from app.core.session_identity import is_unsigned_cart_checkout_url, normalize_session_id
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest


def test_cors_origins_never_star():
    assert "*" not in (settings.CORS_ORIGINS or [])


def test_unsigned_cart_url_rejected():
    assert is_unsigned_cart_checkout_url("https://turabees.com/cart?cart=eyJpZCI6MX0")
    assert is_unsigned_cart_checkout_url("https://turabees.com/checkout?foo=1&cart=abc")
    assert not is_unsigned_cart_checkout_url("https://turabees.com/cart?s=281dc7e0")
    assert not is_unsigned_cart_checkout_url(None)


def test_normalize_session_id():
    assert normalize_session_id("user-session-123") == "user-session-123"
    assert normalize_session_id("  ") is None
    with pytest.raises(HTTPException):
        normalize_session_id("bad id with spaces!!!")


def test_lru_dict_evicts():
    cache: LRUDict[str, int] = LRUDict(maxsize=2)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    assert "a" not in cache
    assert cache["b"] == 2
    assert cache["c"] == 3


def test_api_keys_match():
    assert api_keys_match("abc", "abc")
    assert not api_keys_match("abc", "abcd")
    assert not api_keys_match("", "abc")


def test_chat_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "CHATBOT_API_KEY", "test-secret-key-xyz")
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    from app.main import app

    client = TestClient(app)
    r = client.post("/chat", json={"session_id": "s1", "message": "hi"})
    assert r.status_code == 401
    assert "X-Api-Key" in (r.json().get("detail") or "")
    # Wrong key still 401
    r_bad = client.post(
        "/chat",
        json={"session_id": "s1", "message": "hi"},
        headers={"X-Api-Key": "wrong"},
    )
    assert r_bad.status_code == 401
    # Health stays public
    assert client.get("/health").status_code == 200
    # Root stays public
    assert client.get("/").status_code == 200
