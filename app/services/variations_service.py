from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _fold(value: str | None) -> str:
    text = (value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def filter_variations_for_category(
    variations: list[dict[str, Any]],
    category: str | None,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Keep live catalog variations that belong to the given garment category."""
    if not category:
        return []
    cat_fold = _fold(category)
    out: list[dict[str, Any]] = []
    for item in variations or []:
        if not isinstance(item, dict):
            continue
        if active_only and item.get("is_active") is False:
            continue
        cat_name = str(
            (item.get("category") or {}).get("name")
            if isinstance(item.get("category"), dict)
            else item.get("category_name") or ""
        ).strip()
        cat_id_name_fold = _fold(cat_name)
        if not cat_id_name_fold:
            continue
        matched = (
            cat_fold == cat_id_name_fold
            or cat_fold in cat_id_name_fold
            or cat_id_name_fold in cat_fold
            or ("suit" in cat_fold and "suit" in cat_id_name_fold)
            or ("sherwani" in cat_fold and "sherwani" in cat_id_name_fold)
            or ("prince" in cat_fold and "prince" in cat_id_name_fold)
        )
        if matched:
            out.append(item)
    return out


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 1}


def _category_tokens(variations: list[dict[str, Any]]) -> set[str]:
    """Tokens from parent category names — ignore when matching variation style words."""
    out: set[str] = set()
    for item in variations or []:
        cat_name = str(
            (item.get("category") or {}).get("name")
            if isinstance(item.get("category"), dict)
            else item.get("category_name") or ""
        )
        out |= _tokens(cat_name)
    return out


def resolve_variation_against_catalog(
    text: str | None,
    variations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Map user words (double breast, three piece, achkan, tux…) onto a live variation row.
    No hardcoded variation dictionary — only the provided list.

    Does not treat the parent category word alone as a variation pick
    (e.g. "Sherwani chahiye" must NOT auto-select "Maharaja Sherwani").
    """
    raw = (text or "").strip()
    if not raw or not variations:
        return None
    raw_l = raw.lower()
    raw_fold = _fold(raw)
    cat_tokens = _category_tokens(variations)
    raw_style_tokens = _tokens(raw_l) - cat_tokens
    # Message is only the garment category (or filler) — not a variation choice yet.
    if not raw_style_tokens and _tokens(raw_l) <= cat_tokens:
        return None

    # Exact / containment on name or slug (name must appear in the message — not reverse)
    for item in variations:
        name = str(item.get("name") or "").strip()
        slug = str(item.get("slug") or "").strip()
        name_l = name.lower()
        slug_l = slug.lower().replace("-", " ")
        if not name:
            continue
        if name_l == raw_l or _fold(name) == raw_fold:
            return item
        if name_l in raw_l:
            return item
        name_style = _tokens(name_l) - cat_tokens
        if name_style and name_style <= _tokens(raw_l):
            return item
        if slug and len(slug_l) > 2 and slug_l in raw_l:
            return item

    # Token / stem overlap on distinctive style words only
    best: tuple[int, dict[str, Any]] | None = None
    for item in variations:
        name = str(item.get("name") or "").lower()
        slug = str(item.get("slug") or "").lower().replace("-", " ")
        style_tokens = (_tokens(f"{name} {slug}") - cat_tokens)
        if not style_tokens:
            continue
        overlap = 0
        for rt in raw_style_tokens:
            for ct in style_tokens:
                if rt == ct or (len(rt) > 3 and (rt.startswith(ct) or ct.startswith(rt))):
                    overlap += 1
                    break
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, item)
    if best and best[0] >= 1:
        return best[1]
    return None


def summarize_variations_for_prompt(variations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in variations or []:
        cat = item.get("category") if isinstance(item.get("category"), dict) else {}
        rows.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "slug": item.get("slug"),
                "category_id": item.get("category_id") or item.get("categoryId") or cat.get("id"),
                "category_name": cat.get("name") or item.get("category_name"),
                "product_count": int(
                    ((item.get("_count") or {}) if isinstance(item.get("_count"), dict) else {}).get(
                        "products"
                    )
                    or item.get("product_count")
                    or 0
                ),
            }
        )
    return rows


def normalize_product_variations(
    raw_variations: list[Any] | None,
    *,
    base_price: float | None = None,
) -> list[dict[str, Any]]:
    """
    Case 1: product.variations[] — each row is a style option on that SKU
    (own id, optional price/images; links to categoryVariation).
    """
    out: list[dict[str, Any]] = []
    for item in raw_variations or []:
        if not isinstance(item, dict):
            continue
        cat_var = item.get("categoryVariation") or item.get("category_variation") or {}
        if not isinstance(cat_var, dict):
            cat_var = {}
        name = (
            item.get("name")
            or item.get("title")
            or cat_var.get("name")
            or ""
        )
        name = str(name).strip()
        if not name:
            continue
        price = item.get("price")
        if price is None:
            price = base_price
        images = item.get("images") or []
        if not isinstance(images, list):
            images = [images] if images else []
        image_url = item.get("image_url") or item.get("imageUrl")
        if not image_url and images:
            image_url = images[0]
        # Absolute-ize relative /uploads paths (lazy import avoids circular with backend_api).
        from app.services.backend_api import resolve_backend_asset_url
        image_url = resolve_backend_asset_url(image_url) if image_url else ""
        images = [resolve_backend_asset_url(img) for img in images if img]
        out.append(
            {
                "id": str(item.get("id") or ""),
                "product_id": str(
                    item.get("productId") or item.get("product_id") or ""
                ),
                "category_variation_id": str(
                    item.get("categoryVariationId")
                    or item.get("category_variation_id")
                    or cat_var.get("id")
                    or ""
                ),
                "name": name,
                "title": item.get("title") or name,
                "slug": cat_var.get("slug") or item.get("slug"),
                "price": float(price) if price is not None else None,
                "description": item.get("description"),
                "images": images,
                "image_url": image_url,
                "stock_quantity": item.get("stockQuantity", item.get("stock_quantity")),
            }
        )
    return out


def summarize_product_variations_for_prompt(
    variations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in variations or []:
        rows.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "price": item.get("price"),
                "image_url": item.get("image_url"),
                "category_variation_id": item.get("category_variation_id"),
                "stock_quantity": item.get("stock_quantity"),
            }
        )
    return rows


def needs_product_variation_choice(
    variations: list[dict[str, Any]] | None,
    selected_product_variation_id: str | None = None,
    selected_product_variation_name: str | None = None,
) -> bool:
    """True when product has 2+ style options and customer has not picked one yet."""
    if selected_product_variation_id or selected_product_variation_name:
        return False
    return len(variations or []) > 1


def apply_product_variation_to_details(
    details: dict[str, Any],
    variation: dict[str, Any],
) -> dict[str, Any]:
    """Overlay chosen product variation image/price onto product_details for the reply.
    
    After a variation is selected:
    - Clears variations list so frontend shows detail card, not variation picker
    - Updates name to "{base} – {variation}" for clear identification
    - Sets variation-specific price and images
    """
    updated = dict(details)
    
    # Update price from variation
    if variation.get("price") is not None:
        updated["price"] = float(variation["price"])
        updated["display_price"] = float(variation["price"])
    
    # Update images from variation
    image = variation.get("image_url")
    if image:
        updated["image_url"] = image
    if variation.get("images"):
        updated["images"] = list(variation["images"])
    
    # Set variation selection markers
    updated["selected_product_variation_id"] = variation.get("id")
    updated["selected_product_variation_name"] = variation.get("name")
    
    # Update product name to include variation name (e.g., "Royal Sherwani – Achkan")
    base_name = str(details.get("name") or "").strip()
    var_name = str(variation.get("name") or "").strip()
    if var_name and base_name and var_name.lower() not in base_name.lower():
        updated["name"] = f"{base_name} – {var_name}"
    elif var_name and not base_name:
        updated["name"] = var_name
    
    # CRITICAL: Clear variations list so frontend shows product detail card, NOT variation picker
    # The customer has already chosen — no need to show the selector again
    updated["variations"] = []
    
    return updated
