"""Resolve USD cost from fal Platform APIs (pricing + billing), not static .env rates."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.services.cost_tracking import llm_cost_usd

logger = logging.getLogger(__name__)

FAL_PLATFORM_API = "https://api.fal.ai/v1"
_CACHE_TTL_S = 3600.0
_BILLING_RETRIES = 3
_BILLING_RETRY_DELAY_S = 0.45

_lock = threading.Lock()
_pricing_cache: dict[str, tuple[float, "EndpointPrice"]] = {}


@dataclass(frozen=True)
class EndpointPrice:
    endpoint_id: str
    unit_price: float
    unit: str
    currency: str = "USD"


def _auth_headers() -> dict[str, str]:
    key = (settings.FAL_KEY or "").strip()
    if not key:
        return {}
    return {"Authorization": f"Key {key}"}


def extract_cost_usd_from_result(result: Any) -> float | None:
    """Some fal payloads include charged USD; prefer this when present."""
    if not isinstance(result, dict):
        return None
    for key in ("cost_total", "cost_usd", "cost"):
        raw = result.get(key)
        if isinstance(raw, (int, float)) and raw >= 0:
            return float(raw)
    billing = result.get("billing")
    if isinstance(billing, dict):
        for key in ("cost_total", "cost_usd", "cost"):
            raw = billing.get(key)
            if isinstance(raw, (int, float)) and raw >= 0:
                return float(raw)
    return None


def count_output_units(result: Any, unit: str) -> float:
    u = (unit or "").strip().lower()
    if u in ("request", "requests"):
        return 1.0
    if u == "image":
        if isinstance(result, dict):
            images = result.get("images")
            if isinstance(images, list) and images:
                return float(max(1, len(images)))
            if result.get("image") or result.get("image_url") or result.get("url"):
                return 1.0
        return 1.0
    return 1.0


def get_endpoint_pricing(endpoint_id: str, *, force_refresh: bool = False) -> EndpointPrice | None:
    endpoint_id = (endpoint_id or "").strip()
    if not endpoint_id:
        return None
    now = time.monotonic()
    with _lock:
        if not force_refresh:
            hit = _pricing_cache.get(endpoint_id)
            if hit is not None and now - hit[0] < _CACHE_TTL_S:
                return hit[1]

    headers = _auth_headers()
    if not headers:
        return None

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(
                f"{FAL_PLATFORM_API}/models/pricing",
                params={"endpoint_id": endpoint_id},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("fal pricing fetch failed endpoint=%s err=%s", endpoint_id, exc)
        return None

    prices = data.get("prices") if isinstance(data, dict) else None
    if not isinstance(prices, list) or not prices:
        return None
    row = prices[0] if isinstance(prices[0], dict) else None
    if not row:
        return None
    try:
        ep = EndpointPrice(
            endpoint_id=str(row.get("endpoint_id") or endpoint_id),
            unit_price=float(row["unit_price"]),
            unit=str(row.get("unit") or "request"),
            currency=str(row.get("currency") or "USD"),
        )
    except (KeyError, TypeError, ValueError):
        return None

    with _lock:
        _pricing_cache[endpoint_id] = (now, ep)
    return ep


def fetch_billing_cost_usd(request_id: str, *, endpoint_id: str = "") -> float | None:
    """Per-request charge from fal billing-events (includes account discounts)."""
    request_id = (request_id or "").strip()
    if not request_id:
        return None
    headers = _auth_headers()
    if not headers:
        return None

    params: dict[str, str] = {"request_id": request_id, "limit": "1"}
    if endpoint_id:
        params["endpoint_id"] = endpoint_id

    for attempt in range(_BILLING_RETRIES):
        try:
            with httpx.Client(timeout=12.0) as client:
                resp = client.get(
                    f"{FAL_PLATFORM_API}/models/billing-events",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "fal billing-events failed request_id=%s attempt=%s err=%s",
                request_id,
                attempt + 1,
                exc,
            )
            time.sleep(_BILLING_RETRY_DELAY_S)
            continue

        events = data.get("billing_events") if isinstance(data, dict) else None
        if events is None:
            events = data.get("events")
        if not isinstance(events, list) or not events:
            if attempt + 1 < _BILLING_RETRIES:
                time.sleep(_BILLING_RETRY_DELAY_S)
            continue
        ev = events[0] if isinstance(events[0], dict) else None
        if not ev:
            continue
        for key in ("cost_total", "cost"):
            raw = ev.get(key)
            if isinstance(raw, (int, float)) and raw >= 0:
                return float(raw)
        nano = ev.get("cost_estimate_nano_usd")
        if isinstance(nano, (int, float)) and nano >= 0:
            return float(nano) / 1_000_000_000.0
    return None


def cost_from_platform_pricing(endpoint_id: str, result: Any) -> float | None:
    ep = get_endpoint_pricing(endpoint_id)
    if ep is None or ep.unit_price < 0:
        return None
    units = count_output_units(result, ep.unit)
    return float(ep.unit_price) * units


def resolve_fal_call_usd(
    *,
    endpoint_id: str,
    result: Any,
    request_id: str | None = None,
    label: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    image_like: bool = False,
) -> tuple[float, str]:
    """
    Returns (usd, source) where source is response | billing | pricing | fallback_tokens | fallback_flat.
    """
    from_result = extract_cost_usd_from_result(result)
    if from_result is not None:
        return from_result, "response"

    if request_id:
        billed = fetch_billing_cost_usd(request_id, endpoint_id=endpoint_id)
        if billed is not None:
            return billed, "billing"

    priced = cost_from_platform_pricing(endpoint_id, result)
    if priced is not None:
        return priced, "pricing"

    if image_like:
        return float(settings.FAL_IMAGE_COST_USD), "fallback_flat"

    blob = f"{label} {endpoint_id}".lower()
    if "any-llm" in blob:
        # any-llm is billed per request on fal, not per token — avoid Gemini env rates.
        ep = get_endpoint_pricing(endpoint_id)
        if ep is not None:
            return float(ep.unit_price), "pricing"
        return 0.001, "fallback_flat"

    return (
        llm_cost_usd(prompt_tokens, completion_tokens),
        "fallback_tokens",
    )


def clear_pricing_cache_for_tests() -> None:
    with _lock:
        _pricing_cache.clear()
