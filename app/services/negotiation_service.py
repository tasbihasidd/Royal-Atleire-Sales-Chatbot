from __future__ import annotations

from decimal import Decimal
from typing import Any

DEFAULT_FLOOR_PRICE_RATIO = Decimal("0.92")


def resolve_floor_price(price: float | int | str, floor_price: float | int | str | None = None) -> float:
    """Use backend floor when present; otherwise fall back to configurable ratio (legacy)."""
    if floor_price is not None:
        return float(floor_price)
    return round(float(price) * float(DEFAULT_FLOOR_PRICE_RATIO), 2)


def calculate_offer(
    cart_items: list[dict[str, Any]],
    user_budget: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    DEPRECATED for chat negotiation.

    The sales agent uses NegotiationEngine (4-round ladder) via calculate_negotiation_offer_node.
    This helper remains only as a thin compatibility shim and must not invent discount codes.
    """
    if not cart_items:
        return {"approved": False, "offer_type": "none", "message": "Cart is empty."}

    item = cart_items[0]
    list_price = float(item["price"])
    floor = resolve_floor_price(list_price, item.get("floor_price"))
    qty = int(item.get("quantity") or 1)
    total = list_price * qty

    if user_budget is not None and float(user_budget) < floor * qty:
        return {
            "approved": False,
            "offer_type": "pivot_required",
            "message": "Requested budget is below the approved floor. Pivot to an in-budget alternative.",
            "total_before": total,
            "floor_total": floor * qty,
        }

    # Soft courtesy path without inventing codes
    courtesy = total * 0.95
    if courtesy >= floor * qty:
        return {
            "approved": True,
            "offer_type": "discount",
            "discount_percent": 5,
            "discount_code": None,
            "requires_backend_discount": True,
            "total_before": float(total),
            "total_after": float(courtesy),
            "message": "5% courtesy discount eligible pending checkout confirmation. Do not invent a code.",
        }

    return {
        "approved": True,
        "offer_type": "free_accessory",
        "item": "complimentary styling accessory",
        "message": "Cash discount would breach floor; complimentary accessory approved instead.",
    }
