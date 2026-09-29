from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from app.services.backend_api import backend_api

logger = logging.getLogger(__name__)


@tool
async def search_products(
    query: str,
    product_type: str | None = None,
    color: str | None = None,
    occasion: str | None = None,
    event_type: str | None = None,
    budget_max: float | None = None,
    budget: float | None = None,
    size: str | None = None,
    fabric: str | None = None,
    season: str | None = None,
    in_stock: bool | None = True,
    limit: int = 10,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Search products from the backend catalog using locked search filters.
    Never invent stock or price. Budget is hard-capped — over-budget items are not returned.
    """
    resolved_occasion = occasion or event_type
    resolved_budget = budget_max if budget_max is not None else budget
    logger.info(
        "search_products start product_type=%s color=%s occasion=%s budget_max=%s "
        "size=%s fabric=%s season=%s in_stock=%s query_length=%s",
        product_type,
        color,
        resolved_occasion,
        resolved_budget,
        size,
        fabric,
        season,
        in_stock,
        len(query or ""),
    )
    try:
        filters = {
            "query": query,
            "product_type": product_type,
            "color": color,
            "occasion": resolved_occasion,
            "budget_max": resolved_budget,
            "size": size,
            "fabric": fabric,
            "season": season,
            "in_stock": in_stock,
            "limit": limit,
            "offset": offset,
        }
        products = await backend_api.search_products(filters)
        logger.info("search_products success result_count=%s", len(products))
        return products
    except Exception:
        logger.exception("search_products failed")
        raise


@tool
async def get_product_details(product_id: str) -> dict[str, Any]:
    """
    Get fresh product details from backend API by product_id.
    Use this when the user asks details about a selected product.
    """
    logger.info("get_product_details start product_id=%s", product_id)
    try:
        product = await backend_api.get_product_details(product_id)
        found = product is not None
        logger.info("get_product_details success product_id=%s found=%s", product_id, found)
        return product or {"error": "Product not found", "product_id": product_id}
    except Exception:
        logger.exception("get_product_details failed product_id=%s", product_id)
        raise
