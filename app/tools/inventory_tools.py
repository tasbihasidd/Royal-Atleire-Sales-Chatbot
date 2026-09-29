from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from app.services.backend_api import backend_api

logger = logging.getLogger(__name__)


@tool
async def check_inventory(
    product_id: str,
    size: str | None = None,
    color: str | None = None,
    quantity: int = 1,
) -> dict[str, Any]:
    """
    Check real-time stock from backend API. Never invent availability.
    """
    logger.info(
        "check_inventory start product_id=%s size=%s color=%s quantity=%s",
        product_id,
        size,
        color,
        quantity,
    )
    try:
        result = await backend_api.check_inventory(product_id, size, color, quantity)
        logger.info(
            "check_inventory success product_id=%s available=%s",
            product_id,
            result.get("available"),
        )
        return result
    except Exception:
        logger.exception("check_inventory failed product_id=%s", product_id)
        raise
