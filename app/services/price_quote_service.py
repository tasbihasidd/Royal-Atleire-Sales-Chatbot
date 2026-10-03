"""Resolve sellable list/floor prices: catalogue vs Turabees calculator (Path A/B)."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from app.services.checkout_service import is_custom_checkout
from app.services.embroidery_tier import infer_embroidery_tier
from app.services.negotiation_service import resolve_floor_price
from app.services.price_calculator import PriceCalculationError, calculate_turabees_price
from app.services.pricing_config import (
    PricingConfig,
    resolve_delivery_fee,
)

logger = logging.getLogger(__name__)

CustomizeMode = Literal["direct", "product"]


def map_style_key(
    *,
    product_type: str | None = None,
    category: str | None = None,
    cut_style: str | None = None,
    variation_name: str | None = None,
    config: PricingConfig | None = None,
) -> str:
    """Map chat/product category → pricing-config style key."""
    blob = " ".join(
        str(x) for x in (cut_style, variation_name, product_type, category) if x
    ).lower()
    if not blob.strip():
        return "Sherwani"

    # Prefer exact config keys when present
    if config:
        for key in config.styles:
            if key.lower() in blob or blob in key.lower():
                return key

    if "double" in blob and "breast" in blob:
        return "Double-Breasted Suit"
    if "3-piece" in blob or "3 piece" in blob or "three piece" in blob:
        return "3-Piece Suit"
    if "2-piece" in blob or "2 piece" in blob or "two piece" in blob:
        return "2-Piece Suit"
    if "dinner" in blob:
        return "Dinner Suit"
    if "tuxedo" in blob or "tux" in blob:
        return "Tuxedo"
    if any(
        w in blob
        for w in ("suit", "blazer", "morning coat", "waistcoat set")
    ) and "sherwani" not in blob:
        return "2-Piece Suit"
    if any(
        w in blob
        for w in ("sherwani", "achkan", "bandhgala", "prince coat", "jodhpuri", "indo", "kurta")
    ):
        return "Sherwani"
    return "Sherwani"


def _coerce_grade_value(value: Any) -> str | None:
    """API may return fabric_grade as str or list[str] (e.g. ['Standard blend'])."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return None
    text = str(value).strip()
    return text or None


def fabric_grade_from_row(fabric: dict[str, Any] | None) -> str | None:
    if not isinstance(fabric, dict):
        return None
    for key in ("fabric_grade", "grade", "fabricGrade", "grade_name"):
        grade = _coerce_grade_value(fabric.get(key))
        if grade:
            return grade
    return None


def _soft_match_grade_key(hints: str, config: PricingConfig) -> str | None:
    """Longest pricing-config grade key that matches free-text hints."""
    if not hints.strip() or not config.fabric_grades:
        return None
    hint_tokens = [t for t in re.split(r"[^\w]+", hints.lower()) if len(t) >= 4]
    ranked = sorted(config.fabric_grades.keys(), key=lambda k: len(str(k)), reverse=True)
    for key in ranked:
        key_l = str(key).lower()
        key_tokens = [t for t in re.split(r"[^\w]+", key_l) if len(t) >= 4]
        if key_l in hints:
            return str(key)
        # Partial either way: "Jamawar" ↔ "Jamawar / Velvet"
        if key_tokens and any(t in hints for t in key_tokens):
            return str(key)
        if hint_tokens and any(t in key_l for t in hint_tokens):
            return str(key)
    return None


def resolve_fabric_grade(
    fabric: dict[str, Any] | None,
    config: PricingConfig,
) -> str | None:
    """
    Prefer explicit fabric.grade. If missing or not an exact config key, soft-match
    against pricing-config fabric_grades (e.g. 'Jamawar' → 'Jamawar / Velvet').
    Never invent a grade that is not in config.
    """
    explicit = fabric_grade_from_row(fabric)
    if explicit:
        if _lookup_grade_rate(config, explicit) is not None:
            return _canonical_grade_key(config, explicit) or explicit
        # API often sends short labels ("Jamawar") vs config ("Jamawar / Velvet")
        soft = _soft_match_grade_key(explicit.lower(), config)
        if soft:
            return soft

    if not isinstance(fabric, dict) or not config.fabric_grades:
        return None

    hints = " ".join(
        str(fabric.get(k) or "")
        for k in (
            "fabric_grade",
            "grade",
            "fabric_type",
            "name",
            "title",
            "description",
            "fabric",
            "pattern",
        )
    ).lower()
    return _soft_match_grade_key(hints, config)


def _canonical_grade_key(config: PricingConfig, grade: str) -> str | None:
    if grade in config.fabric_grades:
        return grade
    lower = {str(k).lower(): k for k in config.fabric_grades}
    return lower.get(str(grade).lower())


def _lookup_grade_rate(config: PricingConfig, grade: str) -> float | None:
    key = _canonical_grade_key(config, grade)
    if key is None:
        return None
    try:
        return float(config.fabric_grades[key])
    except (TypeError, ValueError):
        return None


def _selected_addon_keys(state: dict[str, Any], config: PricingConfig) -> list[str]:
    keys: list[str] = []
    profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    for raw in profile.get("selected_addons") or state.get("selected_addons") or []:
        if raw:
            keys.append(str(raw).strip())

    # Map accepted cross-sell / negotiation accessories by name → addon catalog
    accessory_rows: list[dict[str, Any]] = []
    for bucket in (
        state.get("cross_sell_items"),
        (state.get("negotiation_result") or {}).get("accessories_full")
        if isinstance(state.get("negotiation_result"), dict)
        else None,
    ):
        if isinstance(bucket, list):
            accessory_rows.extend(a for a in bucket if isinstance(a, dict))

    addon_name_index = {
        str(a.name or k).strip().lower(): k for k, a in config.addons.items() if a.name or k
    }
    for acc in accessory_rows:
        name = str(acc.get("name") or acc.get("title") or acc.get("accessory_type") or "").strip().lower()
        if not name:
            continue
        if name in addon_name_index:
            keys.append(addon_name_index[name])
            continue
        for aname, key in addon_name_index.items():
            if name in aname or aname in name:
                keys.append(key)
                break
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        lk = k.lower()
        if lk in seen:
            continue
        seen.add(lk)
        out.append(k)
    return out


def _quantity(state: dict[str, Any]) -> int:
    try:
        qty = int(state.get("quantity") or 1)
    except (TypeError, ValueError):
        qty = 1
    return qty if qty >= 1 else 1


async def _load_config() -> PricingConfig:
    from app.services.backend_api import backend_api
    from app.services.pricing_config import excel_reference_pricing_config

    try:
        payload = await backend_api.get_pricing_config()
        cfg = payload.get("_config")
        if isinstance(cfg, PricingConfig) and cfg.styles and cfg.fabric_grades:
            return cfg
        logger.warning("pricing-config incomplete — excel_reference for quote")
    except Exception as exc:  # noqa: BLE001
        logger.warning("pricing-config unavailable for quote: %s — excel_reference", exc)
    return excel_reference_pricing_config()


async def _resolve_fabric_row(state: dict[str, Any], *, mode: CustomizeMode) -> dict[str, Any] | None:
    from app.services.backend_api import backend_api

    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    code = None
    if mode == "product":
        code = (
            details.get("fabric_id")
            or details.get("fabric_catalog_code")
            or details.get("fabric")
        )
        # Prefer explicit product fabric — only fall back to selected swatch if product has none
        if not code:
            code = state.get("selected_fabric_catalog_code")
    else:
        code = state.get("selected_fabric_catalog_code")
        if not code:
            # Path A may still have fabric list selection in profile
            profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
            code = profile.get("selected_fabric_catalog_code")

    code_s = str(code or "").strip()
    if not code_s:
        # try fabrics list match
        for fabric in state.get("fabrics") or []:
            if isinstance(fabric, dict) and fabric_grade_from_row(fabric):
                return fabric
        return None

    for fabric in state.get("fabrics") or []:
        if not isinstance(fabric, dict):
            continue
        if str(fabric.get("catalog_code") or fabric.get("fabric_id") or "") == code_s:
            return fabric

    try:
        return await backend_api.get_fabric_details(code_s)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_fabric_details failed code=%s err=%s", code_s, exc)
        return None


def detect_customize_mode(state: dict[str, Any]) -> CustomizeMode | None:
    if is_custom_checkout(state) or state.get("customization_stage") or state.get("selected_fabric_catalog_code"):
        if state.get("selected_product_id") or (
            isinstance(state.get("product_details"), dict) and state["product_details"].get("product_id")
        ):
            # Product locked → Path B unless user swapped fabric away from product fabric
            details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
            product_fabric = str(
                details.get("fabric_id") or details.get("fabric_catalog_code") or ""
            ).strip()
            selected = str(state.get("selected_fabric_catalog_code") or "").strip()
            if selected and product_fabric and selected != product_fabric:
                return "direct"  # explicit fabric swap
            return "product"
        return "direct"
    return None


async def build_calculator_quote(state: dict[str, Any]) -> dict[str, Any]:
    """Build calculator price_quote for Path A/B or STANDARD price-missing fallback."""
    config = await _load_config()
    mode = detect_customize_mode(state) or (
        "product" if state.get("selected_product_id") else "direct"
    )

    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    style = map_style_key(
        product_type=state.get("product_type") or details.get("category"),
        category=details.get("category"),
        cut_style=state.get("cut_style"),
        variation_name=state.get("selected_variation_name")
        or state.get("selected_product_variation_name"),
        config=config,
    )

    fabric_row = await _resolve_fabric_row(state, mode=mode)  # type: ignore[arg-type]
    grade = resolve_fabric_grade(fabric_row, config)
    if not grade:
        return {
            "ok": False,
            "source": "calculator",
            "customize_mode": mode,
            "error": (
                "Fabric grade missing on fabric record and fabric_type did not match "
                "pricing-config fabric_grades — cannot price."
            ),
            "fabric_catalog_code": (
                (fabric_row or {}).get("catalog_code") if isinstance(fabric_row, dict) else None
            ),
            "fabric_type": (fabric_row or {}).get("fabric_type") if isinstance(fabric_row, dict) else None,
        }

    emb = await infer_embroidery_tier(state)
    tier = emb.get("embroidery_tier") or "Light"

    profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    delivery_option = profile.get("delivery_option") or state.get("delivery_option")
    delivery_fee = resolve_delivery_fee(
        config,
        option=str(delivery_option) if delivery_option else None,
        override=_positive(state.get("delivery_fee") or profile.get("delivery_fee")),
    )

    payload = {
        "style": style,
        "fabric": grade,
        "embroidery": tier,
        "addons": _selected_addon_keys(state, config),
        "quantity": _quantity(state),
        "delivery_fee": delivery_fee,
    }

    try:
        result = calculate_turabees_price(payload, config)
    except PriceCalculationError as exc:
        return {
            "ok": False,
            "source": "calculator",
            "customize_mode": mode,
            "error": str(exc),
            "inputs_attempted": payload,
        }

    calc = result["calculations"]
    return {
        "ok": True,
        "source": "calculator",
        "customize_mode": mode,
        "currency": "GBP",
        "list_price": calc["grand_total"],
        "floor_price": calc["floor_price"],
        "unit_price": calc["unit_price"],
        "total_markup_fee": calc["total_markup_fee"],
        "embroidery_inference": emb,
        "fabric_catalog_code": (
            (fabric_row or {}).get("catalog_code") if isinstance(fabric_row, dict) else None
        ),
        "fabric_grade": grade,
        "inputs": result["inputs"],
        "calculations": calc,
    }


def _positive(value: Any) -> float | None:
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def catalogue_quote_from_product(details: dict[str, Any]) -> dict[str, Any] | None:
    list_price = _positive(details.get("price"))
    if list_price is None:
        return None
    floor = details.get("floor_price")
    floor_f = resolve_floor_price(list_price, floor if _positive(floor) is not None else None)
    return {
        "ok": True,
        "source": "catalogue",
        "customize_mode": None,
        "currency": details.get("currency") or "GBP",
        "list_price": list_price,
        "floor_price": floor_f,
        "unit_price": list_price,
        "product_id": details.get("product_id"),
    }


async def resolve_sellable_price(state: dict[str, Any]) -> dict[str, Any]:
    """
    CUSTOM / customize flow → calculator.
    STANDARD with API price → catalogue.
    STANDARD price missing → calculator fallback.
    """
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    custom = is_custom_checkout(state) or bool(state.get("customization_stage"))
    mode = detect_customize_mode(state)

    if custom or mode in ("direct", "product"):
        return await build_calculator_quote(state)

    # STANDARD
    if details:
        cat = catalogue_quote_from_product(details)
        if cat:
            return cat
        # price unavailable → calculator fallback
        if details.get("price_unavailable") or not _positive(details.get("price")):
            return await build_calculator_quote(state)

    # No product details — if fabric selected still try calculator
    if state.get("selected_fabric_catalog_code"):
        return await build_calculator_quote(state)

    return {
        "ok": False,
        "source": "none",
        "error": "No catalogue price and insufficient inputs for calculator.",
    }


def public_price_quote(quote: dict[str, Any] | None) -> dict[str, Any] | None:
    """Customer/LLM-safe subset — no floor or markup."""
    if not isinstance(quote, dict) or not quote.get("ok"):
        return None
    return {
        "source": quote.get("source"),
        "currency": quote.get("currency") or "GBP",
        "list_price": quote.get("list_price"),
        "unit_price": quote.get("unit_price"),
        "customize_mode": quote.get("customize_mode"),
        "fabric_grade": quote.get("fabric_grade"),
        "embroidery": (quote.get("inputs") or {}).get("embroidery")
        if isinstance(quote.get("inputs"), dict)
        else (quote.get("embroidery_inference") or {}).get("embroidery_tier"),
    }
