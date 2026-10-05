"""Fal platform pricing resolution (no live API)."""

from __future__ import annotations

from app.services.fal_pricing import (
    EndpointPrice,
    clear_pricing_cache_for_tests,
    count_output_units,
    extract_cost_usd_from_result,
    resolve_fal_call_usd,
)


def test_extract_cost_from_result():
    assert extract_cost_usd_from_result({"cost_total": 0.0025}) == 0.0025
    assert extract_cost_usd_from_result({"billing": {"cost_total": 0.01}}) == 0.01
    assert extract_cost_usd_from_result({"output": "x"}) is None


def test_count_output_units():
    assert count_output_units({}, "request") == 1.0
    assert count_output_units({"images": [{}, {}]}, "image") == 2.0


def test_resolve_prefers_response_cost():
    usd, source = resolve_fal_call_usd(
        endpoint_id="fal-ai/any-llm",
        result={"output": "hi", "cost_total": 0.003},
        request_id="req-1",
        label="any-llm",
    )
    assert usd == 0.003 and source == "response"


def test_resolve_uses_cached_pricing(monkeypatch):
    clear_pricing_cache_for_tests()

    def fake_pricing(endpoint_id: str, *, force_refresh: bool = False):
        return EndpointPrice(endpoint_id=endpoint_id, unit_price=0.025, unit="image")

    monkeypatch.setattr("app.services.fal_pricing.get_endpoint_pricing", fake_pricing)
    monkeypatch.setattr("app.services.fal_pricing.fetch_billing_cost_usd", lambda *a, **k: None)

    usd, source = resolve_fal_call_usd(
        endpoint_id="fal-ai/flux/dev",
        result={"images": [{}]},
        request_id=None,
        label="seedream",
        image_like=True,
    )
    assert usd == 0.025 and source == "pricing"


def test_resolve_any_llm_not_token_fallback(monkeypatch):
    monkeypatch.setattr("app.services.fal_pricing.get_endpoint_pricing", lambda *a, **k: None)
    monkeypatch.setattr("app.services.fal_pricing.fetch_billing_cost_usd", lambda *a, **k: None)

    usd, source = resolve_fal_call_usd(
        endpoint_id="fal-ai/any-llm",
        result={"output": "x"},
        label="any-llm",
        prompt_tokens=5000,
        completion_tokens=5000,
    )
    assert usd == 0.001 and source == "fallback_flat"
