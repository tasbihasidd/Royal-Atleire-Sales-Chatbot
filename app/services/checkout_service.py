"""Build Sales Agent Checkout API payloads from graph state."""

from __future__ import annotations

from typing import Any


def is_custom_checkout(state: dict[str, Any]) -> bool:
    if state.get("custom_image_url"):
        return True
    result = state.get("custom_design_result")
    return bool(
        isinstance(result, dict)
        and (result.get("image_url") or result.get("generated_product_name"))
        and not result.get("error")
    )


def _positive_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _gbp_unit_price(state: dict[str, Any]) -> float | None:
    negotiation = state.get("negotiation_result") if isinstance(state.get("negotiation_result"), dict) else {}
    offered = _positive_price(negotiation.get("offered_price"))
    if offered is not None:
        return offered
    quote = state.get("price_quote") if isinstance(state.get("price_quote"), dict) else {}
    if quote.get("ok"):
        # Prefer per-outfit marked-up unit for line item; else grand total for qty=1
        unit = _positive_price(quote.get("unit_price"))
        if unit is not None:
            return unit
        total = _positive_price(quote.get("list_price"))
        if total is not None:
            return total
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    return _positive_price(details.get("price"))


def _item_name(state: dict[str, Any]) -> str | None:
    custom = state.get("custom_design_result") if isinstance(state.get("custom_design_result"), dict) else {}
    for key in ("generated_product_name", "base_product_name"):
        name = custom.get(key)
        if name:
            return str(name)
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    if details.get("name"):
        return str(details["name"])
    product_id = str(state.get("selected_product_id") or "")
    for product in state.get("products") or []:
        if not isinstance(product, dict):
            continue
        if product_id and str(product.get("product_id") or "") == product_id and product.get("name"):
            return str(product["name"])
    return None


def _selected_product_id(state: dict[str, Any]) -> str | None:
    product_id = state.get("selected_product_id")
    if product_id:
        return str(product_id)
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    if details.get("product_id"):
        return str(details["product_id"])
    for product in state.get("products") or []:
        if isinstance(product, dict) and product.get("product_id"):
            return str(product["product_id"])
    return None


def _fabric_fields(state: dict[str, Any]) -> tuple[str | None, str | None]:
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    custom = state.get("custom_design_result") if isinstance(state.get("custom_design_result"), dict) else {}
    fabric_id = (
        state.get("selected_fabric_catalog_code")
        or custom.get("fabric_id")
        or details.get("fabric_id")
        or details.get("fabric_catalog_code")
    )
    fabric_name = custom.get("fabric") or details.get("fabric")
    if not fabric_name:
        selected = str(state.get("selected_fabric_catalog_code") or "")
        for fabric in state.get("fabrics") or []:
            if not isinstance(fabric, dict):
                continue
            code = str(fabric.get("catalog_code") or fabric.get("fabric_id") or "")
            if selected and code == selected:
                fabric_name = fabric.get("name")
                fabric_id = fabric_id or code
                break
    return (str(fabric_id) if fabric_id else None, str(fabric_name) if fabric_name else None)


def _measurements(state: dict[str, Any]) -> dict[str, Any] | None:
    result = state.get("measurement_result") if isinstance(state.get("measurement_result"), dict) else {}
    body = result.get("body_measurements") or state.get("body_measurements")
    if isinstance(body, dict) and body:
        return dict(body)
    collected: dict[str, Any] = {}
    for key in ("chest", "waist", "shoulder", "sleeve", "jacket_length", "height"):
        value = result.get(key) or state.get(key)
        if value:
            collected[key] = value
    return collected or None


def _custom_attributes(state: dict[str, Any]) -> dict[str, Any]:
    custom = state.get("custom_design_result") if isinstance(state.get("custom_design_result"), dict) else {}
    image_url = state.get("custom_image_url") or custom.get("image_url")
    fabric_id, fabric_name = _fabric_fields(state)
    attrs: dict[str, Any] = {}
    if fabric_id:
        attrs["fabric_id"] = fabric_id
    if fabric_name:
        attrs["fabric"] = fabric_name
    if image_url:
        attrs["generated_image_url"] = image_url
    measurements = _measurements(state)
    if measurements:
        attrs["measurements"] = measurements
    size = state.get("size")
    measurement = state.get("measurement_result") if isinstance(state.get("measurement_result"), dict) else {}
    if not size:
        size = measurement.get("size")
    if size:
        attrs["size"] = size
    color = state.get("color")
    if color:
        attrs["color"] = color
    return attrs


_GIFT_BLOCK_ACTIONS = frozenset(
    ("cutoff_and_pivot", "budget_pivot", "defend_value", "ask_color_preference")
)
_GIFT_OFFER_ACTIONS = frozenset(
    ("offer_free_accessory", "offer_bundle", "reinforce_gift_or_consultant", "offer_paid_accessory_upsell")
)


def slim_negotiation_result_for_session(negotiation: Any) -> dict[str, Any] | None:
    """
    Persist a lean gift slice across chat turns so close_sale can attach ACCESSORY.

    Returns None when there is no complimentary gift to keep (clears prior gift).
    """
    if not isinstance(negotiation, dict) or not negotiation:
        return None
    action = str(negotiation.get("action") or "")
    if action in _GIFT_BLOCK_ACTIONS:
        return None
    free = bool(negotiation.get("free_accessory"))
    if not free and not negotiation.get("approved"):
        return None
    raw = negotiation.get("accessories_full") or []
    accessories_full: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("accessory_id") or item.get("id") or "").strip()
        if not aid:
            continue
        image_url = item.get("image_url") or item.get("imageUrl")
        if not image_url:
            images = item.get("images") or []
            if isinstance(images, list) and images:
                first = images[0]
                if isinstance(first, str):
                    image_url = first
                elif isinstance(first, dict):
                    image_url = first.get("url") or first.get("image_url")
        accessories_full.append(
            {
                "accessory_id": aid,
                "name": item.get("name"),
                "accessory_type": item.get("accessory_type"),
                "price": item.get("price"),
                "image_url": image_url,
            }
        )
        if len(accessories_full) >= 1:
            break
    if not accessories_full:
        # Approved empty-stock offer — nothing to attach at checkout.
        if free or action in _GIFT_OFFER_ACTIONS:
            return None
        return None
    display = negotiation.get("accessories") or []
    if not isinstance(display, list):
        display = []
    return {
        "approved": True,
        "free_accessory": free,
        "action": action or "offer_free_accessory",
        "accessories_full": accessories_full,
        "accessories": [str(a) for a in display if a][:3]
        or [str(accessories_full[0].get("name") or "Complimentary accessory")],
        "offered_price": negotiation.get("offered_price"),
        "original_price": negotiation.get("original_price"),
        "currency": negotiation.get("currency"),
    }


def _accepted_accessories(state: dict[str, Any]) -> list[dict[str, Any]]:
    negotiation = state.get("negotiation_result") if isinstance(state.get("negotiation_result"), dict) else {}
    if not negotiation:
        return []
    action = str(negotiation.get("action") or "")
    if action in _GIFT_BLOCK_ACTIONS:
        return []
    free = bool(negotiation.get("free_accessory"))
    approved = bool(negotiation.get("approved"))
    # Complimentary gifts, or approved gift / paid-upsell rows with SKUs.
    if not (free or approved):
        return []
    raw = negotiation.get("accessories_full") or []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("accessory_id") or item.get("id") or "").strip()
        if not aid:
            continue
        rows.append(item)
    return rows


def build_checkout_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    quantity = state.get("quantity")
    try:
        qty = int(quantity) if quantity is not None else 1
    except (TypeError, ValueError):
        qty = 1
    if qty < 1:
        qty = 1

    if is_custom_checkout(state):
        item: dict[str, Any] = {
            "item_type": "CUSTOM",
            "catalog_product_id": None,
        }
        name = _item_name(state)
        if name:
            item["item_name_snapshot"] = name
        price = _gbp_unit_price(state)
        if price is not None:
            item["unit_price"] = price
        else:
            # Last-resort: calculator list on quote even if ok flag missing shape
            quote = state.get("price_quote") if isinstance(state.get("price_quote"), dict) else {}
            calc = quote.get("calculations") if isinstance(quote.get("calculations"), dict) else {}
            fallback = _positive_price(calc.get("unit_price") or calc.get("grand_total") or quote.get("list_price"))
            if fallback is not None:
                item["unit_price"] = fallback
        if qty != 1:
            item["quantity"] = qty
        attrs = _custom_attributes(state)
        if attrs:
            item["custom_attributes"] = attrs
        items.append(item)
    else:
        product_id = _selected_product_id(state)
        if product_id:
            item = {
                "item_type": "STANDARD",
                "catalog_product_id": product_id,
            }
            name = _item_name(state)
            if name:
                item["item_name_snapshot"] = name
            if qty != 1:
                item["quantity"] = qty
            items.append(item)

    negotiation = state.get("negotiation_result") if isinstance(state.get("negotiation_result"), dict) else {}
    complimentary = bool(negotiation.get("free_accessory"))

    seen: set[str] = set()
    for accessory in _accepted_accessories(state):
        accessory_id = str(accessory.get("accessory_id") or accessory.get("id") or "")
        if not accessory_id or accessory_id in seen:
            continue
        seen.add(accessory_id)
        # Turabees POST /checkout auto-fetches ACCESSORY catalogue price and ignores
        # unit_price overrides — complimentary gifts would inflate the bag (£1000+£85).
        # Send free gifts as CUSTOM @ unit_price 0 so bag shows the pagri and total stays list.
        if complimentary:
            name = str(accessory.get("name") or "").strip() or "Complimentary accessory"
            if not name.lower().startswith("complimentary"):
                name = f"Complimentary {name}"
            attrs: dict[str, Any] = {
                "accessory_id": accessory_id,
                "complimentary": True,
            }
            atype = accessory.get("accessory_type")
            if atype:
                attrs["accessory_type"] = atype
            # CUSTOM lines do not auto-fetch accessory catalogue media — pass pagri image
            # explicitly or the cart falls back to an unrelated placeholder.
            image_url = accessory.get("image_url") or accessory.get("imageUrl")
            if not image_url:
                images = accessory.get("images") or []
                if isinstance(images, list) and images:
                    first = images[0]
                    if isinstance(first, str):
                        image_url = first
                    elif isinstance(first, dict):
                        image_url = first.get("url") or first.get("image_url")
            if image_url:
                attrs["generated_image_url"] = str(image_url)
                attrs["image_url"] = str(image_url)
            items.append(
                {
                    "item_type": "CUSTOM",
                    "catalog_product_id": None,
                    "item_name_snapshot": name,
                    "unit_price": 0,
                    "custom_attributes": attrs,
                }
            )
        else:
            items.append({"item_type": "ACCESSORY", "accessory_id": accessory_id})
    return items


def build_checkout_request(state: dict[str, Any]) -> dict[str, Any] | None:
    items = build_checkout_items(state)
    if not items:
        return None
    return {
        "user_id": str(state.get("session_id") or "guest"),
        "user_type": "GUEST",
        "items": items,
    }
