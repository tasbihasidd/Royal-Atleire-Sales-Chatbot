from __future__ import annotations

"""
Chat recommendations do NOT use hardcoded styling scores.
Flow: user preferences → backend product search → return full filtered set.
Frontend decides how many cards to render (3, 6, or N).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# No agent-side hard cap — return all filtered candidates. Frontend truncates.
MAX_PRODUCTS_TO_SHOW: int | None = None


def select_products_for_display(
    products: list[dict[str, Any]],
    limit: int | None = MAX_PRODUCTS_TO_SHOW,
    event_text: str | None = None,
) -> list[dict[str, Any]]:
    """Take API results as-is; optionally slice only if an explicit limit is passed."""
    from app.services.mock_data_service import filter_products_by_stated_event

    filtered = filter_products_by_stated_event(products, event_text)
    selected = list(filtered or [])
    if limit is not None and limit > 0:
        selected = selected[:limit]
    logger.info(
        "select_products_for_display raw=%s event_filtered=%s shown=%s limit=%s",
        len(products or []),
        len(filtered),
        len(selected),
        limit,
    )
    return selected
