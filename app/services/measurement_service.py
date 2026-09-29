from __future__ import annotations

import re
from typing import Any

from app.services.measurement_collection_service import (
    apply_standard_size,
    chart_for_category,
    find_chart_row,
    nearest_size,
    parse_inches,
    size_labels_from_chart,
)


# Deprecated: do NOT use for validation. Live charts come from GET /api/v2/size-charts.
# Kept only so older imports do not break; empty so we never invent Sherwani numbers.
SIZE_CHART: dict[str, dict[str, dict[str, float]]] = {}


def parse_height_to_inches(height: str | None) -> int | None:
    if not height:
        return None
    text = height.strip().lower()
    match = re.search(r"(\d)\s*['ft]+\s*(\d{1,2})?", text)
    if match:
        feet = int(match.group(1))
        inches = int(match.group(2) or 0)
        return feet * 12 + inches
    match = re.search(r"(\d{2,3})\s*(in|inch|inches)?", text)
    if match:
        value = int(match.group(1))
        if 50 <= value <= 90:
            return value
    return None


def validate_measurements(
    selected_size: str | None = None,
    height: str | None = None,
    chest: str | None = None,
    waist: str | None = None,
    product_type: str | None = None,
    size_chart: dict[str, Any] | None = None,
    body_measurements: dict[str, Any] | None = None,
    shoulder: str | None = None,
    sleeve: str | None = None,
    jacket_length: str | None = None,
    available_sizes: list[str] | None = None,
) -> dict:
    """
    Validate against a live size chart when provided.
    Without a chart, only check product available_sizes — never invent category numbers.
    """
    warnings: list[str] = []
    selected_size = str(selected_size).strip() if selected_size else None

    extra = body_measurements if isinstance(body_measurements, dict) else {}
    body = {
        "chest": chest or extra.get("chest"),
        "waist": waist or extra.get("waist"),
        "shoulder": shoulder or extra.get("shoulder"),
        "sleeve": sleeve or extra.get("sleeve"),
        "jacket_length": jacket_length or extra.get("jacket_length"),
    }

    if size_chart:
        if selected_size:
            applied = apply_standard_size(size_chart, selected_size)
            if applied.get("status") == "unknown":
                return {
                    "status": "unknown",
                    "requires_tailor_review": True,
                    "warnings": applied.get("warnings") or ["Selected size is not on the live size chart."],
                    "nearest_size": None,
                    "size_labels": size_labels_from_chart(size_chart),
                }
            # Optional body cross-check
            if chest or waist:
                match = nearest_size(size_chart, {**body, "size": selected_size})
                if match.get("nearest_size") and str(match["nearest_size"]).upper() != selected_size.upper():
                    warnings.append(
                        f"Body measurements are closer to size {match['nearest_size']} than {selected_size}."
                    )
                warnings.extend(match.get("warnings") or [])
            return {
                "status": "mismatch" if warnings else "ok",
                "requires_tailor_review": bool(warnings),
                "warnings": warnings,
                "nearest_size": selected_size,
                "size_labels": size_labels_from_chart(size_chart),
            }

        # Body-only path
        if any(body.values()):
            match = nearest_size(size_chart, body)
            return {
                "status": match.get("status", "unknown"),
                "requires_tailor_review": bool(match.get("requires_tailor_review")),
                "warnings": list(match.get("warnings") or []),
                "nearest_size": match.get("nearest_size"),
                "size_labels": size_labels_from_chart(size_chart),
            }

        return {
            "status": "unknown",
            "requires_tailor_review": False,
            "warnings": ["No selected size or body measurements provided."],
            "nearest_size": None,
            "size_labels": size_labels_from_chart(size_chart),
        }

    # No live chart for this category (e.g. Sherwani today) — do not invent rows.
    if selected_size and available_sizes:
        folded = {str(s).strip().upper() for s in available_sizes if str(s).strip()}
        if selected_size.upper() not in folded and not any(
            selected_size.upper() in s or s in selected_size.upper() for s in folded
        ):
            return {
                "status": "unknown",
                "requires_tailor_review": True,
                "warnings": [
                    f"Size {selected_size} is not in product available sizes. "
                    "A Style Consultant will confirm fit for this category."
                ],
                "nearest_size": None,
                "size_labels": [str(s) for s in available_sizes],
            }
        return {
            "status": "ok",
            "requires_tailor_review": False,
            "warnings": [
                "No published size chart for this category — using product available sizes only."
            ],
            "nearest_size": selected_size,
            "size_labels": [str(s) for s in available_sizes],
        }

    if selected_size:
        return {
            "status": "unknown",
            "requires_tailor_review": True,
            "warnings": [
                "No live size chart for this category. A Style Consultant will take remaining measurements."
            ],
            "nearest_size": selected_size,
            "size_labels": list(available_sizes or []),
        }

    return {
        "status": "unknown",
        "requires_tailor_review": False,
        "warnings": ["No selected size provided."],
        "nearest_size": None,
        "size_labels": list(available_sizes or []),
    }


def resolve_chart_for_product(
    charts: list[dict[str, Any]],
    *,
    product_type: str | None = None,
    product_details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    category_id = None
    category_name = product_type
    if isinstance(product_details, dict):
        category_id = product_details.get("category_id") or product_details.get("categoryId")
        category_name = (
            product_details.get("category")
            or product_details.get("product_type")
            or product_type
        )
    return chart_for_category(charts, category_name, category_id=category_id)
