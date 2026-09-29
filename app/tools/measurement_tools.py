from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from app.services.measurement_service import validate_measurements as validate_measurements_service

logger = logging.getLogger(__name__)


@tool
def validate_measurements(
    selected_size: str | None = None,
    height: str | None = None,
    chest: str | None = None,
    waist: str | None = None,
    product_type: str | None = None,
    size_chart: dict[str, Any] | None = None,
    body_measurements: dict[str, Any] | None = None,
    available_sizes: list[str] | None = None,
    shoulder: str | None = None,
    sleeve: str | None = None,
    jacket_length: str | None = None,
) -> dict:
    """
    Validate selected size / body measurements against the live size chart.
    If the category has no chart, do not invent Suits numbers.
    """
    logger.info(
        "validate_measurements start selected_size=%s product_type=%s has_chart=%s",
        selected_size,
        product_type,
        bool(size_chart),
    )
    try:
        result = validate_measurements_service(
            selected_size=selected_size,
            height=height,
            chest=chest,
            waist=waist,
            product_type=product_type,
            size_chart=size_chart,
            body_measurements=body_measurements,
            available_sizes=available_sizes,
            shoulder=shoulder,
            sleeve=sleeve,
            jacket_length=jacket_length,
        )
        logger.info(
            "validate_measurements success status=%s tailor_review=%s",
            result.get("status"),
            result.get("requires_tailor_review"),
        )
        return result
    except Exception:
        logger.exception("validate_measurements failed")
        raise
