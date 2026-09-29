from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from app.services.backend_api import backend_api

logger = logging.getLogger(__name__)


@tool
async def search_fabrics(
    fabric_type: str | None = None,
    color: str | None = None,
    category: str | None = None,
    season: str | None = None,
    pattern: str | None = None,
    embroidery: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Search fabrics for custom / bespoke orders.
    Returns catalog_code, name, colours, season, embroidery, etc.
    Never invent fabric price, meters, or weight — those are not in the fabric schema.
    """
    logger.info(
        "search_fabrics start fabric_type=%s color=%s category=%s season=%s",
        fabric_type,
        color,
        category,
        season,
    )
    try:
        fabrics = await backend_api.search_fabrics(
            {
                "fabric_type": fabric_type,
                "color": color,
                "category": category,
                "season": season,
                "pattern": pattern,
                "embroidery": embroidery,
                "limit": limit,
                "offset": offset,
            }
        )
        logger.info("search_fabrics success result_count=%s", len(fabrics))
        return fabrics
    except Exception:
        logger.exception("search_fabrics failed")
        raise


@tool
async def get_fabric_details(catalog_code: str) -> dict[str, Any]:
    """
    Get fabric details by catalog_code.
    product.fabric_id maps to fabric.catalog_code.
    Never invent price, meters, or weight.
    """
    logger.info("get_fabric_details start catalog_code=%s", catalog_code)
    try:
        fabric = await backend_api.get_fabric_details(catalog_code)
        found = fabric is not None
        logger.info(
            "get_fabric_details success catalog_code=%s found=%s",
            catalog_code,
            found,
        )
        return fabric or {"error": "Fabric not found", "catalog_code": catalog_code}
    except Exception:
        logger.exception("get_fabric_details failed catalog_code=%s", catalog_code)
        raise
