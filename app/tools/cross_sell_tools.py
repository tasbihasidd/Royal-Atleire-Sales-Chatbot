from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from app.services.accessories_service import (
    filter_accessories_for_product,
    summarize_accessories_for_prompt,
)
from app.services.backend_api import backend_api

logger = logging.getLogger(__name__)


@tool
async def suggest_cross_sell(
    product_type: str | None = None,
    color: str | None = None,
    occasion: str | None = None,
    budget_max: float | None = None,
) -> list[dict[str, Any]]:
    """
    Suggest complementary accessories from the accessories API for the selected garment.
    Uses pairs_with_categories / suitable_events — never invents mismatched items.
    """
    logger.info(
        "suggest_cross_sell start product_type=%s color=%s occasion=%s budget_max=%s",
        product_type,
        color,
        occasion,
        budget_max,
    )
    try:
        if not product_type:
            # Accessories-only / no garment yet — search accessories API directly.
            searched = await backend_api.search_accessories(
                {
                    "event_type": occasion,
                    "color": color,
                    "limit": 8,
                }
            )
            results = summarize_accessories_for_prompt(searched)
            logger.info("suggest_cross_sell accessories-only result_count=%s", len(results))
            return results

        recommended = await backend_api.recommend_accessories(
            category=str(product_type),
            event_type=str(occasion) if occasion else None,
            main_product_price=float(budget_max) if budget_max is not None else None,
            limit=6,
        )
        raw = list(recommended.get("accessories") or [])
        filtered = filter_accessories_for_product(
            raw,
            product_category=product_type,
            event_type=str(occasion) if occasion else None,
            product_color=color,
            limit=1,
        )
        if not filtered:
            searched = await backend_api.search_accessories(
                {
                    "category": product_type,
                    "event_type": occasion,
                    "color": color,
                    "limit": 8,
                }
            )
            filtered = filter_accessories_for_product(
                searched,
                product_category=product_type,
                event_type=str(occasion) if occasion else None,
                product_color=color,
                limit=1,
            )

        results = summarize_accessories_for_prompt(filtered)
        logger.info("suggest_cross_sell success result_count=%s", len(results))
        return results
    except Exception:
        logger.exception("suggest_cross_sell failed")
        raise
