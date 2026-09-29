from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ACCESSORIES_API_SPEC tiers — permission to offer free gift; margin still caps SKU value.
FREE_ACCESSORY_TIER_PREMIUM = 150_000.0  # Stole / Turban eligible
FREE_ACCESSORY_TIER_STANDARD = 100_000.0  # Khussa / Brooch eligible
FREE_ACCESSORY_TIER_DISCOUNT = 50_000.0  # 50% off accessories (paid), not free
FREE_ACCESSORY_FOREIGN_THRESHOLD = 1_000.0

# Legacy aliases used by older call sites
FREE_ACCESSORY_PRICE_THRESHOLD = FREE_ACCESSORY_TIER_STANDARD


def _fold_label(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = text.replace("valima", "walima").replace("nikkah", "nikah").replace("baraat", "barat")
    return re.sub(r"[^a-z0-9]+", "", text)


def category_matches_pairs(product_category: str | None, pairs_with: list[Any] | None) -> bool:
    """True if accessory pairs_with_categories fits the selected garment category."""
    cat = _fold_label(product_category)
    if not cat:
        return True
    pairs = [str(p).strip() for p in (pairs_with or []) if str(p).strip()]
    if not pairs:
        # No pairing metadata — do not invent a match; exclude.
        return False
    for pair in pairs:
        p = _fold_label(pair)
        if not p:
            continue
        if cat == p or cat in p or p in cat:
            return True
        if "suit" in cat and "suit" in p:
            return True
        if "sherwani" in cat and "sherwani" in p:
            return True
        if "prince" in cat and "prince" in p:
            return True
        if "tuxedo" in cat and "tuxedo" in p:
            return True
    return False


def event_matches_suitable(event_type: str | None, suitable_events: list[Any] | None) -> bool:
    """If accessory lists suitable_events, require overlap with session event."""
    event = _fold_label(event_type)
    if not event:
        return True
    events = [str(e).strip() for e in (suitable_events or []) if str(e).strip()]
    if not events:
        return True
    for item in events:
        e = _fold_label(item)
        if not e:
            continue
        if event == e or event in e or e in event:
            return True
        if "nikah" in event and "nikah" in e:
            return True
        if "walima" in event and "walima" in e:
            return True
        if "barat" in event and "barat" in e:
            return True
        if "mehndi" in event and "mehndi" in e:
            return True
    return False


# Color families for matching
_NEUTRAL_COLORS = {"black", "charcoal", "grey", "gray", "dark"}
_COMPLEMENTARY_PAIRS = {
    # Gold/Golden complements Navy/Blue/Dark
    "gold": {"navy", "blue", "darkblue", "dark", "black", "maroon"},
    "golden": {"navy", "blue", "darkblue", "dark", "black", "maroon"},
    # Silver complements Black/Grey/Navy
    "silver": {"black", "grey", "gray", "charcoal", "navy", "dark"},
    # Maroon complements Gold/Cream/Ivory
    "maroon": {"gold", "golden", "cream", "ivory", "beige", "white"},
    "burgundy": {"gold", "golden", "cream", "ivory", "beige"},
    # White/Ivory complements Dark colors
    "white": {"black", "navy", "maroon", "dark", "charcoal"},
    "ivory": {"black", "navy", "maroon", "dark", "charcoal", "burgundy"},
    "cream": {"black", "navy", "maroon", "dark", "burgundy"},
}


def _normalize_color(color: str | None) -> str:
    """Normalize color string for matching."""
    if not color:
        return ""
    return re.sub(r"[^a-z]+", "", color.strip().lower())


def color_matches_product(
    accessory_colors: list[str] | None,
    product_color: str | None,
) -> tuple[bool, int]:
    """
    Check if any accessory color matches or complements the product color.
    
    Returns:
        (matches, score) where:
        - matches: True if there's a suitable color match
        - score: Priority (higher = better match)
            3 = Exact match (black→black)
            2 = Complementary match (gold→navy)
            1 = Neutral accessory (black/charcoal matches anything)
            0 = No match
    """
    if not product_color:
        return True, 0  # No product color = allow any accessory
    
    prod_norm = _normalize_color(product_color)
    if not prod_norm:
        return True, 0
    
    acc_colors = [_normalize_color(c) for c in (accessory_colors or []) if c]
    if not acc_colors:
        return True, 0  # No accessory colors listed = allow
    
    best_score = 0
    
    for acc_color in acc_colors:
        if not acc_color:
            continue
        
        # Exact match (highest priority)
        if acc_color == prod_norm or acc_color in prod_norm or prod_norm in acc_color:
            return True, 3
        
        # Complementary match
        complements = _COMPLEMENTARY_PAIRS.get(acc_color, set())
        if any(comp in prod_norm or prod_norm in comp for comp in complements):
            best_score = max(best_score, 2)
        
        # Neutral accessory color (matches anything)
        if acc_color in _NEUTRAL_COLORS:
            best_score = max(best_score, 1)
    
    return best_score > 0, best_score


def free_gift_margin_budget(
    list_price: float | None,
    floor_price: float | None,
    bundle_offer: dict[str, Any] | None = None,
) -> float:
    """
    Max complimentary accessory value = min(list − floor, backend max_free_value).
    Never gift an accessory priced above this headroom.
    """
    try:
        list_p = float(list_price or 0)
        floor_p = float(floor_price or 0)
    except (TypeError, ValueError):
        return 0.0
    margin = max(0.0, list_p - floor_p) if list_p > 0 and floor_p >= 0 else 0.0
    if isinstance(bundle_offer, dict):
        raw_max = bundle_offer.get("max_free_value")
        if raw_max is not None:
            try:
                max_free = float(raw_max)
                if max_free >= 0:
                    margin = min(margin, max_free) if margin > 0 else max_free
            except (TypeError, ValueError):
                pass
    return margin


def qualifies_for_free_accessory(
    main_product_price: float | None,
    currency: str | None = "PKR",
    bundle_offer: dict[str, Any] | None = None,
) -> bool:
    try:
        # If backend API explicitly returned a FREE bundle offer
        if isinstance(bundle_offer, dict) and str(bundle_offer.get("type", "")).upper() == "FREE":
            return True
        price = float(main_product_price or 0)
        curr = str(currency or "PKR").strip().upper()
        if curr in ("GBP", "USD", "EUR"):
            return price >= FREE_ACCESSORY_FOREIGN_THRESHOLD
        return price >= FREE_ACCESSORY_TIER_STANDARD
    except (TypeError, ValueError):
        return False


def eligible_types_for_price(
    main_product_price: float | None,
    currency: str | None = "PKR",
    bundle_offer: dict[str, Any] | None = None,
) -> list[str] | None:
    """Spec tiers: >150k Stole/Turban; >100k Khussa/Brooch. None = no type preference."""
    if isinstance(bundle_offer, dict):
        eligible = bundle_offer.get("eligible_types")
        if isinstance(eligible, list) and eligible:
            return [str(t) for t in eligible if t]
    try:
        price = float(main_product_price or 0)
    except (TypeError, ValueError):
        return None
    curr = str(currency or "PKR").strip().upper()
    if curr in ("GBP", "USD", "EUR"):
        if price >= FREE_ACCESSORY_FOREIGN_THRESHOLD:
            return ["Stole", "Turban", "Khussa", "Brooch"]
        return None
    if price >= FREE_ACCESSORY_TIER_PREMIUM:
        return ["Stole", "Turban"]
    if price >= FREE_ACCESSORY_TIER_STANDARD:
        return ["Khussa", "Brooch"]
    return None


def filter_accessories_for_product(
    accessories: list[dict[str, Any]],
    *,
    product_category: str | None,
    event_type: str | None = None,
    eligible_types: list[str] | None = None,
    max_price: float | None = None,
    product_color: str | None = None,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """
    Keep accessories that pair with the garment (and event when listed).
    When max_price is set (free-gift path), only keep 0 < price ≤ max_price.
    When product_color is set, filter and prioritize by color match.
    """
    filtered: list[dict[str, Any]] = []
    eligible_fold = {_fold_label(t) for t in (eligible_types or []) if t}

    for item in accessories:
        if not isinstance(item, dict):
            continue
        if not category_matches_pairs(product_category, item.get("pairs_with_categories")):
            continue
        if not event_matches_suitable(event_type, item.get("suitable_events")):
            continue
        if max_price is not None:
            try:
                price = float(item.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0 or price > float(max_price):
                continue
        # Color matching: filter out non-matching colors when product_color is set
        if product_color:
            acc_colors = item.get("available_colors") or []
            matches, score = color_matches_product(acc_colors, product_color)
            if not matches:
                continue
            item["_color_score"] = score  # Temporary field for sorting
        filtered.append(item)

    # Fallback: if event matching excluded all items, keep items that match category
    if not filtered and accessories and max_price is None:
        for item in accessories:
            if not isinstance(item, dict):
                continue
            if category_matches_pairs(product_category, item.get("pairs_with_categories")):
                filtered.append(item)

    if eligible_fold:
        preferred = [
            a
            for a in filtered
            if _fold_label(str(a.get("accessory_type") or "")) in eligible_fold
        ]
        if preferred:
            filtered = preferred + [
                a
                for a in filtered
                if _fold_label(str(a.get("accessory_type") or "")) not in eligible_fold
            ]

    # Prefer better color matches, then cheaper within margin
    if product_color:
        # Sort by color score descending (3=exact, 2=complement, 1=neutral), then by price
        filtered.sort(
            key=lambda a: (-a.get("_color_score", 0), float(a.get("price") or 0))
        )
    elif max_price is not None:
        # Prefer cheaper gifts within margin (protect margin further)
        filtered.sort(key=lambda a: float(a.get("price") or 0))

    # Dedupe by accessory_id
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in filtered:
        aid = str(item.get("accessory_id") or item.get("id") or "")
        key = aid or str(item.get("name") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        # Clean up temporary sorting field
        item.pop("_color_score", None)
        unique.append(item)
        if len(unique) >= limit:
            break

    logger.info(
        "filter_accessories_for_product category=%s event=%s in=%s out=%s "
        "eligible_types=%s max_price=%s product_color=%s",
        product_category,
        event_type,
        len(accessories),
        len(unique),
        eligible_types,
        max_price,
        product_color,
    )
    return unique


def filter_free_gift_candidates(
    accessories: list[dict[str, Any]],
    *,
    list_price: float,
    floor_price: float | None,
    product_category: str | None,
    event_type: str | None = None,
    bundle_offer: dict[str, Any] | None = None,
    currency: str | None = "PKR",
    product_color: str | None = None,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Margin-safe free gift pool: accessory.price ≤ (list − floor) ∩ max_free_value."""
    margin = free_gift_margin_budget(list_price, floor_price, bundle_offer)
    if margin <= 0:
        logger.info("filter_free_gift_candidates empty — margin_budget=0")
        return []
    eligible = eligible_types_for_price(list_price, currency=currency, bundle_offer=bundle_offer)
    return filter_accessories_for_product(
        accessories,
        product_category=product_category,
        event_type=event_type,
        eligible_types=eligible,
        max_price=margin,
        product_color=product_color,
        limit=limit,
    )


def summarize_accessories_for_prompt(accessories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lean rows for LLM — no inventable gaps."""
    rows: list[dict[str, Any]] = []
    for item in accessories:
        rows.append(
            {
                "accessory_id": item.get("accessory_id"),
                "name": item.get("name"),
                "accessory_type": item.get("accessory_type"),
                "price": item.get("price"),
                "currency": item.get("currency") or "PKR",
                "fabric": item.get("fabric"),
                "available_colors": item.get("available_colors") or [],
                "pairs_with_categories": item.get("pairs_with_categories") or [],
                "suitable_events": item.get("suitable_events") or [],
                "image_url": item.get("image_url"),
            }
        )
    return rows
