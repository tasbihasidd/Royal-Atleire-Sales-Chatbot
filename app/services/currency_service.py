from __future__ import annotations

from typing import Any


def detect_market_currency(
    *,
    user_message: str | None = None,
    language_hint: str | None = None,
    session_currency: str | None = None,
) -> str:
    """Deprecated: market FX detection removed. Prefer backend product currency."""
    del user_message, language_hint
    if session_currency:
        return str(session_currency).strip().upper()
    return "GBP"


def apply_display_currency(
    product: dict[str, Any],
    market_currency: str | None,
) -> dict[str, Any]:
    """
    Annotate a product for display using catalogue (backend) currency only.
    ``market_currency`` is kept for call-site compatibility but never triggers FX conversion.
    """
    out = dict(product)
    product_currency = str(out.get("currency") or "GBP").strip().upper()
    if market_currency:
        out["market_currency"] = str(market_currency).strip().upper()
    else:
        out["market_currency"] = product_currency

    if out.get("price_unavailable") or not out.get("_has_db_price"):
        out["currency_needs_consultant"] = True
        return out

    out["display_price"] = out.get("price")
    out["display_currency"] = product_currency
    out["currency_needs_consultant"] = False
    return out


def apply_display_currency_list(
    products: list[dict[str, Any]],
    market_currency: str | None,
) -> list[dict[str, Any]]:
    return [apply_display_currency(p, market_currency) for p in (products or [])]
