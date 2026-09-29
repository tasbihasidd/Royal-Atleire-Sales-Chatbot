from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from app.services.negotiation_service import calculate_offer

logger = logging.getLogger(__name__)


@tool
def calculate_negotiation_offer(
    cart_items: list[dict[str, Any]],
    user_budget: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Calculate an approved discount or complimentary accessory.
    This enforces floor price rules and Groom's Party discount logic.
    """
    logger.info(
        "calculate_negotiation_offer start cart_item_count=%s user_budget=%s reason_length=%s",
        len(cart_items),
        user_budget,
        len(reason) if reason else 0,
    )
    try:
        result = calculate_offer(cart_items=cart_items, user_budget=user_budget, reason=reason)
        logger.info(
            "calculate_negotiation_offer success approved=%s",
            result.get("approved"),
        )
        return result
    except Exception:
        logger.exception("calculate_negotiation_offer failed")
        raise
