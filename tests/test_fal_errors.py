"""FAL provider quota / rate-limit error classification."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.services.fal_errors import (
    MSG_QUOTA,
    MSG_RATE,
    FalProviderError,
    classify_fal_failure,
)


@dataclass
class _FakeFalHTTP(Exception):
    message: str
    status_code: int
    response_headers: dict

    def __str__(self) -> str:
        return self.message


def test_classify_402_credits_as_quota():
    err = classify_fal_failure(
        _FakeFalHTTP("Insufficient credits", 402, {}),
    )
    assert err.code == "FAL_QUOTA_EXCEEDED"
    assert err.http_status == 429
    assert MSG_QUOTA in err.message
    detail = err.to_detail()
    assert detail["code"] == "FAL_QUOTA_EXCEEDED"


def test_classify_429_as_rate_limit():
    err = classify_fal_failure(
        Exception("Too Many Requests"),
        status_code=429,
        message="Too Many Requests",
        response_headers={"Retry-After": "12"},
    )
    assert err.code == "FAL_RATE_LIMITED"
    assert err.http_status == 429
    assert err.retry_after == "12"
    assert MSG_RATE in err.message


def test_classify_quota_text_without_status():
    err = classify_fal_failure(RuntimeError("User has no remaining balance / credits"))
    assert err.code == "FAL_QUOTA_EXCEEDED"
    assert err.http_status == 429


def test_classify_401_auth():
    err = classify_fal_failure(
        Exception("Unauthorized"),
        status_code=401,
        message="Unauthorized",
    )
    assert err.code == "FAL_AUTH_ERROR"
    assert err.http_status == 503


def test_classify_500_upstream():
    err = classify_fal_failure(
        Exception("Internal"),
        status_code=503,
        message="Service Unavailable",
    )
    assert err.code == "FAL_UPSTREAM_ERROR"
    assert err.http_status == 502


def test_fal_provider_error_to_detail():
    exc = FalProviderError(
        code="FAL_QUOTA_EXCEEDED",
        message=MSG_QUOTA,
        http_status=429,
        fal_status=402,
        provider_message="no credits",
    )
    d = exc.to_detail()
    assert d["code"] == "FAL_QUOTA_EXCEEDED"
    assert d["fal_status"] == 402
    assert d["provider_message"] == "no credits"


def test_subscribe_maps_fal_http_error(monkeypatch):
    from app.services import fal_runtime

    def boom(*_a, **_k):
        raise _FakeFalHTTP("User out of credits", 402, {})

    monkeypatch.setattr(fal_runtime.settings, "FAL_KEY", "test-key")
    monkeypatch.setitem(
        __import__("sys").modules,
        "fal_client",
        SimpleNamespace(subscribe=boom),
    )

    with pytest.raises(FalProviderError) as ei:
        fal_runtime.subscribe("fal-ai/test", {"prompt": "x"}, label="test")
    assert ei.value.code == "FAL_QUOTA_EXCEEDED"
