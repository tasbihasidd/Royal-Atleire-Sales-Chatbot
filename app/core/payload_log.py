"""Print high-level (moti-moti) request/response summaries — never dump full payloads."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_PROMPT_PREVIEW = 120
_URL_PREVIEW = 80


def _clip(value: Any, max_len: int = _PROMPT_PREVIEW) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}…"


def _url_brief(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    if len(text) <= _URL_PREVIEW:
        return text
    return f"{text[:40]}…{text[-20:]}"


def _item_brief(item: dict[str, Any]) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "item_type": item.get("item_type"),
        "name": item.get("item_name_snapshot") or item.get("name"),
    }
    if item.get("catalog_product_id"):
        brief["catalog_product_id"] = item.get("catalog_product_id")
    if item.get("accessory_id"):
        brief["accessory_id"] = item.get("accessory_id")
    if item.get("unit_price") is not None:
        brief["unit_price"] = item.get("unit_price")
    attrs = item.get("custom_attributes") if isinstance(item.get("custom_attributes"), dict) else {}
    if attrs.get("fabric_id") or attrs.get("fabric"):
        brief["fabric"] = attrs.get("fabric") or attrs.get("fabric_id")
    if attrs.get("generated_image_url"):
        brief["has_generated_image"] = True
    if attrs.get("color"):
        brief["color"] = attrs.get("color")
    if attrs.get("size"):
        brief["size"] = attrs.get("size")
    return {k: v for k, v in brief.items() if v is not None}


def summarize_payload(title: str, payload: Any) -> dict[str, Any] | list[Any] | str | None:
    """Keep only fields useful for live debugging of image / text / checkout."""
    title_l = (title or "").lower()

    if payload is None:
        return None
    if isinstance(payload, (str, int, float, bool)):
        return _clip(payload, 200) if isinstance(payload, str) else payload

    if not isinstance(payload, dict):
        if isinstance(payload, list):
            return {"count": len(payload), "sample": summarize_payload(title, payload[0]) if payload else None}
        return _clip(payload, 200)

    # --- Checkout ---
    if "checkout" in title_l:
        items = payload.get("items")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else None
        out: dict[str, Any] = {}
        if "user_type" in payload or "user_id" in payload:
            out["user_type"] = payload.get("user_type")
            out["user_id"] = _clip(payload.get("user_id"), 40)
        if isinstance(items, list):
            out["item_count"] = len(items)
            out["items"] = [_item_brief(i) for i in items if isinstance(i, dict)]
        src = data or payload
        if src.get("checkout_url") or payload.get("checkout_url"):
            out["checkout_url"] = _url_brief(src.get("checkout_url") or payload.get("checkout_url"))
        if src.get("session_id") or payload.get("session_id"):
            out["session_id"] = _clip(src.get("session_id") or payload.get("session_id"), 40)
        if "success" in payload or (data and "success" in data):
            out["success"] = payload.get("success", True if data else None)
        if payload.get("error"):
            out["error"] = _clip(payload.get("error"), 160)
        if payload.get("type"):
            out["type"] = payload.get("type")
        return out or {"keys": list(payload.keys())[:12]}

    # --- fal / image / LLM text ---
    if "fal" in title_l or "image" in title_l or "llm" in title_l or "seedream" in title_l:
        out = {}
        if payload.get("model"):
            out["model"] = payload.get("model")
        if "prompt" in payload:
            out["prompt"] = _clip(payload.get("prompt"), _PROMPT_PREVIEW)
        if "system_prompt" in payload:
            out["system_prompt"] = _clip(payload.get("system_prompt"), _PROMPT_PREVIEW)
        if "image_urls" in payload or "image_url" in payload:
            urls = payload.get("image_urls") or payload.get("image_url")
            if isinstance(urls, list):
                out["image_url_count"] = len(urls)
                out["image_urls"] = [_url_brief(u) for u in urls[:3]]
            else:
                out["image_url"] = _url_brief(urls)
        for key in ("temperature", "max_tokens", "priority", "num_images", "image_size", "enable_safety_checker"):
            if key in payload and payload[key] is not None:
                out[key] = payload[key]
        # Response shapes
        if "output" in payload:
            out["output"] = _clip(payload.get("output"), 200)
        if "text" in payload:
            out["text"] = _clip(payload.get("text"), 200)
        images = payload.get("images")
        if isinstance(images, list):
            out["images"] = [{"url": _url_brief(i.get("url") if isinstance(i, dict) else i)} for i in images[:3]]
        if payload.get("error"):
            out["error"] = _clip(payload.get("error"), 160)
        # If nothing matched, fall through to generic keys
        if out:
            return out

    # --- Generic node / dict summary ---
    keep_keys = (
        "session_id",
        "intent",
        "sales_stage",
        "buying_intent",
        "required_steps",
        "executed_nodes",
        "customization_stage",
        "selected_product_id",
        "selected_fabric_catalog_code",
        "cut_style",
        "color",
        "event_type",
        "product_type",
        "checkout_url",
        "custom_image_url",
        "user_message",
        "final_response",
        "status",
        "success",
        "error",
        "item_count",
        "handover_pending",
    )
    out = {}
    for key in keep_keys:
        if key not in payload or payload[key] in (None, "", [], {}):
            continue
        val = payload[key]
        if key in ("user_message", "final_response") and isinstance(val, str):
            out[key] = _clip(val, 160)
        elif key == "custom_image_url":
            out[key] = True if val else False
        elif key == "checkout_url":
            out[key] = _url_brief(val)
        elif isinstance(val, (list, dict)) and key not in ("required_steps", "executed_nodes"):
            out[key] = f"<{type(val).__name__} len={len(val)}>"
        else:
            out[key] = val
    if "products" in payload and isinstance(payload["products"], list):
        out["products_count"] = len(payload["products"])
    if "fabrics" in payload and isinstance(payload["fabrics"], list):
        out["fabrics_count"] = len(payload["fabrics"])
    if "messages" in payload and isinstance(payload["messages"], list):
        out["messages_count"] = len(payload["messages"])
    if "close_result" in payload and isinstance(payload["close_result"], dict):
        cr = payload["close_result"]
        out["close_status"] = cr.get("status")
        if cr.get("checkout_url"):
            out["checkout_url"] = _url_brief(cr.get("checkout_url"))
    return out or {"keys": list(payload.keys())[:16]}


def print_payload(title: str, payload: Any) -> None:
    summary = summarize_payload(title, payload)
    try:
        body = json.dumps(summary, indent=2, default=str, ensure_ascii=False)
    except TypeError:
        body = str(summary)
    banner = f"\n── {title} ──\n{body}\n"
    print(banner, flush=True)
    logger.info("%s | %s", title, body)
