"""Turabees Excel-parity price calculator (pure math — no I/O)."""

from __future__ import annotations

from typing import Any

from app.services.pricing_config import AddonPricing, PricingConfig


class PriceCalculationError(ValueError):
    """Raised when style/fabric grade/embroidery cannot be resolved from config."""


def _lookup_ci(mapping: dict[str, Any], key: str) -> Any | None:
    if key in mapping:
        return mapping[key]
    lower = {str(k).lower(): v for k, v in mapping.items()}
    return lower.get(str(key).lower())


def calculate_turabees_price(
    payload: dict[str, Any],
    config: PricingConfig,
) -> dict[str, Any]:
    """
    Complete Turabees calculator.

    payload:
      style, fabric (grade name), embroidery, addons[], quantity, delivery_fee
    """
    style_key = str(payload.get("style") or "").strip()
    fabric_grade = str(payload.get("fabric") or "").strip()
    embroidery_key = str(payload.get("embroidery") or "None").strip() or "None"
    selected_addons = payload.get("addons") or []
    if not isinstance(selected_addons, list):
        selected_addons = [selected_addons]
    try:
        quantity = int(payload.get("quantity") or 1)
    except (TypeError, ValueError):
        quantity = 1
    if quantity < 1:
        quantity = 1
    try:
        delivery_fee = float(payload.get("delivery_fee") or 0)
    except (TypeError, ValueError):
        delivery_fee = 0.0

    style = _lookup_ci(config.styles, style_key)
    if style is None:
        raise PriceCalculationError(f"Unknown style {style_key!r} in pricing-config.")

    rate = _lookup_ci(config.fabric_grades, fabric_grade)
    if rate is None:
        raise PriceCalculationError(
            f"Unknown fabric grade {fabric_grade!r} in pricing-config.fabric_grades."
        )
    fabric_rate = float(rate)

    embroidery_cost = _lookup_ci(config.embroidery, embroidery_key)
    if embroidery_cost is None:
        # try common aliases
        aliases = {"none": "None", "no": "None", "light": "Light", "medium": "Medium", "heavy": "Heavy"}
        alt = aliases.get(embroidery_key.lower())
        embroidery_cost = _lookup_ci(config.embroidery, alt) if alt else None
    if embroidery_cost is None:
        raise PriceCalculationError(f"Unknown embroidery tier {embroidery_key!r} in pricing-config.")
    embroidery_cost = float(embroidery_cost)

    markup = float(config.markup or 0.0)
    base_price = float(style.base)
    fabric_length = float(style.fabric_length)

    fabric_cost = fabric_length * fabric_rate
    subtotal_no_markup = base_price + fabric_cost + embroidery_cost
    markup_amount_per_outfit = subtotal_no_markup * markup
    price_per_outfit_marked_up = subtotal_no_markup + markup_amount_per_outfit

    total_outfits_price = price_per_outfit_marked_up * quantity
    total_outfits_floor = subtotal_no_markup * quantity
    total_markup_fee = markup_amount_per_outfit * quantity

    addons_total = 0.0
    addons_breakdown: dict[str, float] = {}
    for addon in selected_addons:
        addon_key = str(addon).strip()
        if not addon_key:
            continue
        info = _lookup_ci(config.addons, addon_key)
        if info is None:
            # try lowercased raw
            continue
        if not isinstance(info, AddonPricing):
            continue
        cost = (info.price * quantity) if info.charge_per == "outfit" else info.price
        addons_total += cost
        label = info.name or addon_key
        addons_breakdown[str(label)] = round(cost, 2)

    grand_total = total_outfits_price + addons_total + delivery_fee
    floor_price = total_outfits_floor + addons_total + delivery_fee

    return {
        "inputs": {
            "style": style_key,
            "fabric": fabric_grade,
            "embroidery": embroidery_key,
            "addons": [str(a) for a in selected_addons],
            "quantity": quantity,
            "delivery_fee": round(delivery_fee, 2),
            "markup_rate": markup,
            "style_base": round(base_price, 2),
            "fabric_length": fabric_length,
            "fabric_rate_per_metre": fabric_rate,
        },
        "calculations": {
            "fabric_cost_per_outfit": round(fabric_cost, 2),
            "embroidery_cost_per_outfit": round(embroidery_cost, 2),
            "subtotal_no_markup_per_outfit": round(subtotal_no_markup, 2),
            "total_markup_fee": round(total_markup_fee, 2),
            "price_per_outfit_with_markup": round(price_per_outfit_marked_up, 2),
            "addons_total": round(addons_total, 2),
            "addons_breakdown": addons_breakdown,
            "delivery_fee": round(delivery_fee, 2),
            "floor_price": round(floor_price, 2),
            "grand_total": round(grand_total, 2),
            "list_price": round(grand_total, 2),
            "unit_price": round(price_per_outfit_marked_up, 2),
        },
    }
