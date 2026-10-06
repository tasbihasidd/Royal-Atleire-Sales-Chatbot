from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Any

import httpx

from app.config import settings
from app.core.payload_log import print_payload
from app.schemas.fabric import FabricSchema
from app.schemas.product import ProductSchema
from app.services.negotiation_service import resolve_floor_price

logger = logging.getLogger(__name__)

# Retryable upstream statuses (rate limit + transient server errors).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 2
_BASE_BACKOFF_S = 0.4
_MAX_BACKOFF_S = 8.0

# Process-lifetime caches for catalog metadata (planner fetches every turn).
_categories_cache: list[dict[str, Any]] | None = None
_variations_cache: list[dict[str, Any]] | None = None
_size_charts_cache: list[dict[str, Any]] | None = None
_categories_cache_failed = False
_variations_cache_failed = False
_size_charts_cache_failed = False


class BackendAPIError(Exception):
    """Raised when backend calls fail after retries — callers should degrade, not 500."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        path: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path
        self.retryable = retryable


# Lazy import mock service to avoid circular imports
_mock_service = None

def _get_mock_service():
    """Lazy load mock data service."""
    global _mock_service
    if _mock_service is None:
        from app.services import mock_data_service
        _mock_service = mock_data_service
    return _mock_service

EVENT_TYPE_ALIASES = {
    "nikah": "NIKKAH",
    "nikkah": "NIKKAH",
    "barat": "BARAT",
    "baraat": "BARAT",
    "walima": "WALIMA",
    "valima": "WALIMA",
    "mehndi": "MEHNDI",
    "mehendi": "MEHNDI",
    "shadi": "SHADI",
    "mangni": "MANGNI",
}


def resolve_backend_asset_url(path: str | None) -> str:
    """Convert relative asset paths to full URLs using backend origin."""
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    origin = settings.BACKEND_API_BASE_URL.rstrip("/")
    # Strip API version paths to get base origin for assets
    for suffix in ("/api/v2/sales-agent", "/api/v1/sales-agent", "/api/v2", "/api/v1"):
        if origin.endswith(suffix):
            origin = origin[: -len(suffix)]
            break
    return f"{origin}{path if path.startswith('/') else '/' + path}"


def _normalize_event_type(value: str | None) -> str | None:
    if not value:
        return None
    key = str(value).strip().lower().replace(" ", "_")
    return EVENT_TYPE_ALIASES.get(key, str(value).strip().upper().replace(" ", "_"))


def _normalize_accessory_event_type(value: str | None) -> str | None:
    if not value:
        return None
    norm = _normalize_event_type(value)
    if norm == "NIKKAH":
        return "NIKAH"
    return norm


def _normalize_color(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip()


def _normalize_product_type(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    return raw or None


def resolve_category_against_catalog(
    product_type: str | None,
    catalog_categories: list[dict[str, Any]] | None = None,
) -> str | None:
    """
    Soft-match a free-form garment label to a live catalog category name.
    No hardcoded suit→Suits map — uses the API category list only.
    """
    raw = _normalize_product_type(product_type)
    if not raw:
        return None
    live = [
        str(cat.get("name") or "").strip()
        for cat in (catalog_categories or [])
        if str(cat.get("name") or "").strip() and int(cat.get("product_count") or 0) > 0
    ]
    if not live:
        return raw

    raw_l = raw.lower()
    for name in live:
        if name.lower() == raw_l:
            return name
    # Containment: "suit" ↔ "Suits", "prince" ↔ "Prince Coat"
    for name in live:
        low = name.lower()
        if raw_l in low or low in raw_l:
            return name
    # Token overlap (three-piece suit → Suits): allow stem/prefix matches (suit≈suits)
    raw_tokens = {t for t in re.split(r"[^a-z0-9]+", raw_l) if t}
    best: tuple[int, str] | None = None
    for name in live:
        tokens = {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}
        overlap = 0
        for rt in raw_tokens:
            for ct in tokens:
                if rt == ct or rt.startswith(ct) or ct.startswith(rt):
                    overlap += 1
                    break
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, name)
    if best:
        return best[1]
    return raw


def _first_color(product: dict[str, Any]) -> str | None:
    colors = product.get("available_colors")
    if isinstance(colors, list) and colors:
        return str(colors[0])
    return product.get("color") or product.get("primary_color")


def _first_item_or_join(value: Any, join_sep: str = ", ") -> str | None:
    """Extract first item from array or return string as-is."""
    if isinstance(value, list):
        if not value:
            return None
        return str(value[0]) if len(value) == 1 else join_sep.join(str(v) for v in value)
    return str(value) if value else None


def _coerce_fabric_grade_field(value: Any) -> str | None:
    """API may send fabric_grade as str or list[str]; schema expects a single string."""
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


def _pricing_config_response(cfg: Any, *, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "styles": {
            k: {"base": v.base, "fabric_length": v.fabric_length, "category": v.category}
            for k, v in cfg.styles.items()
        },
        "fabric_grades": dict(cfg.fabric_grades),
        "embroidery": dict(cfg.embroidery),
        "markup": cfg.markup,
        "addons": {
            k: {"price": a.price, "charge_per": a.charge_per, "name": a.name}
            for k, a in cfg.addons.items()
        },
        "delivery_fee_default": cfg.delivery_fee_default,
        "delivery_options": dict(cfg.delivery_options),
        "_config": cfg,
    }
    if raw is not None:
        out["raw"] = raw
    return out


def _normalize_product(product: dict[str, Any]) -> dict[str, Any]:
    """
    Map backend v2 payload into locked ProductSchema fields.
    
    v2 field mappings:
    - fabric_catalog_code → fabric_id
    - pattern (array) → pattern (first/joined)
    - season (array) → season (first/joined)
    - suitable_events → occasion
    - design_fabric.catalogCode → fabric_id fallback
    - variations[] → product style options (Case 1)
    """
    from app.services.variations_service import normalize_product_variations

    raw = dict(product)
    # Support both sales-agent snake_case and admin camelCase envelopes.
    if "title" in raw and not raw.get("name"):
        raw["name"] = raw.get("title")
    if raw.get("productUrl") and not raw.get("product_url"):
        raw["product_url"] = raw.get("productUrl")
    if raw.get("primaryColor") and not raw.get("primary_color"):
        raw["primary_color"] = raw.get("primaryColor")
    if raw.get("availableColors") and not raw.get("available_colors"):
        raw["available_colors"] = raw.get("availableColors")
    if raw.get("availableSizes") and not raw.get("available_sizes"):
        raw["available_sizes"] = raw.get("availableSizes")
    if raw.get("suitableEvents") and not raw.get("suitable_events"):
        raw["suitable_events"] = raw.get("suitableEvents")
    if raw.get("floorPrice") is not None and raw.get("floor_price") is None:
        raw["floor_price"] = raw.get("floorPrice")
    if isinstance(raw.get("category"), dict):
        cat = raw["category"]
        raw["category"] = cat.get("name") or cat.get("slug") or raw.get("category")

    # occasion: from suitable_events (v2) or occasion/event_type (v1)
    occasion = None
    suitable_events = raw.get("suitable_events")
    occasion = None
    if isinstance(suitable_events, list) and suitable_events:
        occasion = ", ".join(str(e) for e in suitable_events)
    if not occasion:
        occasion = raw.get("occasion") or raw.get("event_type")
    if not occasion and raw.get("occasion_tags"):
        tags = raw["occasion_tags"]
        occasion = tags[0] if isinstance(tags, list) and tags else tags
    occasion = str(occasion).strip() if occasion else None

    # fabric_id: from fabric_catalog_code (v2) or fabric_id/catalog_code (v1)
    # Also check design_fabric.catalogCode as fallback
    fabric_id = raw.get("fabric_catalog_code") or raw.get("fabric_id") or raw.get("catalog_code")
    if not fabric_id:
        design_fabric = raw.get("design_fabric") or raw.get("designFabric")
        if isinstance(design_fabric, dict):
            fabric_id = design_fabric.get("catalogCode") or design_fabric.get("catalog_code") or design_fabric.get("id")

    # fabric type name
    fabric = raw.get("fabric")
    if not fabric:
        fabric_type = raw.get("fabric_type")
        fabric = _first_item_or_join(fabric_type) if fabric_type else None

    # season: v2 returns array
    season_raw = raw.get("season")
    season = _first_item_or_join(season_raw)
    if not season:
        desc_l = (raw.get("description") or "").lower()
        fab_l = str(fabric or "").lower()
        if any(w in desc_l or w in fab_l for w in ("velvet", "wool", "winter", "heavy")):
            season = "Winter"
        elif any(w in desc_l or w in fab_l for w in ("cotton", "linen", "summer", "light")):
            season = "Summer"
        else:
            season = "All-Season"

    # pattern: v2 returns array
    pattern_raw = raw.get("pattern")
    pattern = _first_item_or_join(pattern_raw)

    # available_colors
    available_colors = raw.get("available_colors") or []
    if not available_colors and raw.get("colors"):
        available_colors = raw["colors"]
    if not available_colors and raw.get("primary_color"):
        available_colors = [raw["primary_color"]]
        if raw.get("secondary_colors"):
            available_colors = list(available_colors) + list(raw["secondary_colors"])

    available_sizes = raw.get("available_sizes") or []

    currency = str(raw.get("currency") or "GBP").strip().upper()

    raw_price = raw.get("price")
    raw_floor = raw.get("floor_price") if raw.get("floor_price") is not None else raw.get("floorPrice")

    # Never invent sellable prices — missing/zero → price_unavailable for the LLM.
    try:
        has_db_price = raw_price is not None and float(raw_price) > 0
    except (TypeError, ValueError):
        has_db_price = False
    price_f = float(raw_price) if has_db_price else 0.0
    price_unavailable = not has_db_price

    # Prefer real backend floor; estimate only when list price exists and floor missing.
    floor_is_estimated = False
    try:
        has_db_floor = raw_floor is not None and float(raw_floor) > 0
    except (TypeError, ValueError):
        has_db_floor = False
    if has_db_floor:
        floor_price_f = float(raw_floor)
    elif price_f > 0:
        floor_price_f = resolve_floor_price(price_f)
        floor_is_estimated = True
    else:
        floor_price_f = None

    images = raw.get("images") or []
    if not isinstance(images, list):
        images = [images] if images else []
    image_url = raw.get("image_url") or raw.get("primary_image_url") or raw.get("imageUrl")
    if not image_url and images:
        image_url = images[0]

    resolved_image_url = resolve_backend_asset_url(image_url) if image_url else ""
    resolved_images = [resolve_backend_asset_url(img) for img in images if img]

    product_variations = normalize_product_variations(
        raw.get("variations"),
        base_price=price_f if price_f > 0 else None,
    )

    normalized = {
        "product_id": str(raw.get("product_id") or raw.get("id") or ""),
        "name": raw.get("name") or "",
        "description": raw.get("description"),
        "price": price_f,
        "floor_price": floor_price_f,
        "_has_db_price": has_db_price,
        "price_unavailable": price_unavailable,
        "_floor_is_estimated": floor_is_estimated,
        "image_url": resolved_image_url,
        "fabric_id": str(fabric_id) if fabric_id else None,
        "fabric": fabric,
        "pattern": pattern,
        "category": raw.get("category") or raw.get("product_type"),
        "season": str(season).strip().title() if season else "All-Season",
        "available_sizes": [str(s) for s in available_sizes],
        "available_colors": [str(c) for c in available_colors],
        "occasion": occasion,
        "suitable_events": suitable_events if isinstance(suitable_events, list) else [],
        # Extra v2 fields (pass through for agent use)
        "currency": raw.get("currency"),
        "embroidery": _first_item_or_join(raw.get("embroidery")),
        "embroidery_level": raw.get("embroidery_level") or raw.get("embroideryLevel"),
        "lead_time_days": raw.get("lead_time_days") or raw.get("leadTimeDays"),
        "is_bespoke_available": raw.get("is_bespoke_available", raw.get("isBespokeAvailable")),
        "product_url": raw.get("product_url"),
        "images": resolved_images,
        "variations": product_variations,
        # Compatibility aliases used by existing nodes
        "color": _first_color({**raw, "available_colors": available_colors}),
        "product_type": raw.get("product_type") or raw.get("category"),
        "event_type": occasion,
    }

    try:
        ProductSchema(
            product_id=normalized["product_id"],
            name=normalized["name"],
            description=normalized.get("description"),
            price=normalized["price"],
            floor_price=normalized.get("floor_price"),
            image_url=normalized.get("image_url"),
            fabric_id=normalized.get("fabric_id"),
            fabric=normalized.get("fabric"),
            pattern=normalized.get("pattern"),
            category=normalized.get("category"),
            season=normalized.get("season"),
            available_sizes=normalized.get("available_sizes") or [],
            available_colors=normalized.get("available_colors") or [],
            occasion=normalized.get("occasion"),
        )
    except Exception:
        logger.warning(
            "Product payload did not fully match locked schema product_id=%s",
            normalized.get("product_id"),
            exc_info=True,
        )

    return normalized


def _normalize_fabric(fabric: dict[str, Any]) -> dict[str, Any]:
    """
    Map backend v2 fabric payload into locked FabricSchema fields.
    
    v2 field mappings:
    - title → name
    - colors → available_colors
    - dress_category → category
    - fabric_type (array) → fabric_type (first/joined)
    - pattern (array) → pattern (first/joined)
    - season (array) → season (first/joined)
    """
    raw = dict(fabric)
    
    # catalog_code: prefer catalog_code, then fabric_id, then id
    catalog_code = raw.get("catalog_code") or raw.get("fabric_id") or raw.get("id")
    
    # name: from title (v2) or name (v1)
    name = raw.get("title") or raw.get("name") or ""
    
    # available_colors: from colors (v2) or available_colors (v1)
    colors = raw.get("colors") or raw.get("available_colors") or []
    if not colors and raw.get("color"):
        colors = [raw["color"]]

    # fabric_type: v2 returns array
    fabric_type = raw.get("fabric_type")
    fabric_type_str = _first_item_or_join(fabric_type) if isinstance(fabric_type, list) else fabric_type
    if not fabric_type_str:
        fabric_type_str = raw.get("fabric")

    # pattern: v2 returns array
    pattern_raw = raw.get("pattern")
    pattern = _first_item_or_join(pattern_raw) if isinstance(pattern_raw, list) else pattern_raw

    # category: from dress_category (v2) or category (v1)
    category_raw = raw.get("dress_category") or raw.get("category")
    category = _first_item_or_join(category_raw) if isinstance(category_raw, list) else category_raw

    # season: keep API values; do not invent All-Season
    season_raw = raw.get("season")
    seasons: list[str] = []
    if isinstance(season_raw, list):
        seasons = [str(s).strip() for s in season_raw if str(s).strip()]
    elif season_raw:
        seasons = [str(season_raw).strip()]
    season = ", ".join(seasons) if seasons else None

    # embroidery: v2 returns array
    embroidery_raw = raw.get("embroidery")
    embroidery = _first_item_or_join(embroidery_raw) if isinstance(embroidery_raw, list) else embroidery_raw

    image_url = raw.get("image_url") or raw.get("primary_image_url") or raw.get("imageUrl")
    if not image_url:
        images = raw.get("images") or []
        if isinstance(images, list) and images:
            first = images[0]
            image_url = first.get("url") if isinstance(first, dict) else first
        elif isinstance(images, str):
            image_url = images
    if not image_url and raw.get("image"):
        image_url = raw.get("image")

    # Keep full dress_category list for event/garment filtering (not only first item).
    dress_categories: list[str] = []
    if isinstance(category_raw, list):
        dress_categories = [str(c).strip() for c in category_raw if str(c).strip()]
    elif category_raw:
        dress_categories = [str(category_raw).strip()]

    normalized = {
        "catalog_code": str(catalog_code) if catalog_code else "",
        "name": name,
        "available_colors": [str(c) for c in colors],
        "description": raw.get("description") or raw.get("desc"),
        "image_url": resolve_backend_asset_url(image_url) if image_url else None,
        "fabric_type": fabric_type_str,
        "pattern": pattern,
        "category": category,
        "dress_category": dress_categories,
        "season": season,
        "seasons": seasons,
        "embroidery": embroidery,
        # Extra v2 fields (pass through)
        "fabric_id": raw.get("fabric_id"),
        "fabric_grade": _coerce_fabric_grade_field(
            raw.get("fabric_grade")
            or raw.get("grade")
            or raw.get("fabricGrade")
            or raw.get("grade_name")
        ),
        "grade": _coerce_fabric_grade_field(
            raw.get("grade") or raw.get("fabric_grade") or raw.get("fabricGrade")
        ),
        "linked_products_count": raw.get("linked_products_count"),
        "linked_products": raw.get("linked_products"),
    }

    try:
        FabricSchema(**{k: v for k, v in normalized.items() if k in FabricSchema.model_fields})
    except Exception:
        logger.warning(
            "Fabric payload did not fully match locked schema catalog_code=%s",
            normalized.get("catalog_code"),
            exc_info=True,
        )
    return normalized


def _normalize_inventory_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    if "available_quantity" in normalized and "stock_quantity" not in normalized:
        normalized["stock_quantity"] = normalized["available_quantity"]
    return normalized


def _build_handover_api_body(payload: dict[str, Any]) -> dict[str, Any]:
    summary = (payload.get("conversation_summary") or "").strip()
    contact = payload.get("customer_contact") or {}
    name = contact.get("name")
    phone = contact.get("phone") or contact.get("whatsapp")
    email = contact.get("email")
    if name or phone or email:
        contact_line = f"Contact: {name or 'N/A'}, Phone/WhatsApp: {phone or 'N/A'}"
        if email:
            contact_line = f"{contact_line}, Email: {email}"
        summary = f"{summary}\n\n{contact_line}".strip() if summary else contact_line

    handover_reason = payload.get("handover_reason")
    priority = "high" if handover_reason == "explicit_request" else "normal"

    return {
        "session_id": payload["session_id"],
        "reason": payload.get("reason") or "Customer requested consultant.",
        "priority": priority,
        "conversation_summary": summary,
    }


def _normalize_accessory(raw: dict[str, Any]) -> dict[str, Any]:
    """Map accessories API payload into a stable agent shape. Never invent prices."""
    pairs = raw.get("pairs_with_categories") or []
    if not isinstance(pairs, list):
        pairs = [pairs] if pairs else []
    events = raw.get("suitable_events") or []
    if not isinstance(events, list):
        events = [events] if events else []
    colors = raw.get("available_colors") or []
    if not isinstance(colors, list):
        colors = [colors] if colors else []
    sizes = raw.get("available_sizes") or []
    if not isinstance(sizes, list):
        sizes = [sizes] if sizes else []

    raw_price = raw.get("price")
    try:
        has_db_price = raw_price is not None and float(raw_price) > 0
    except (TypeError, ValueError):
        has_db_price = False
    price_f = float(raw_price) if has_db_price else 0.0

    floor_price = raw.get("floor_price") if raw.get("floor_price") is not None else raw.get("floorPrice")
    try:
        floor_f = float(floor_price) if floor_price is not None and float(floor_price) > 0 else None
    except (TypeError, ValueError):
        floor_f = None

    return {
        "accessory_id": str(raw.get("accessory_id") or raw.get("id") or ""),
        "name": raw.get("name") or "",
        "description": raw.get("description"),
        "price": price_f,
        "floor_price": floor_f,
        "_has_db_price": has_db_price,
        "price_unavailable": not has_db_price,
        "currency": raw.get("currency") or "PKR",
        "accessory_type": raw.get("accessory_type"),
        "pairs_with_categories": [str(p) for p in pairs],
        "suitable_events": [str(e) for e in events],
        "available_colors": [str(c) for c in colors],
        "available_sizes": [str(s) for s in sizes],
        "image_url": raw.get("image_url"),
        "images": raw.get("images") or [],
        "fabric": raw.get("fabric"),
        "in_stock": raw.get("in_stock", True),
        "lead_time_days": raw.get("lead_time_days"),
    }


class BackendAPIClient:
    """
    Royal Attire backend API client (v2).

    Base URL includes /api/v2/sales-agent, so paths are relative:
    - POST /products/search
    - GET  /products/{product_id}
    - POST /fabrics/search
    - GET  /fabrics/{fabric_id_or_catalog_code}
    - POST /accessories/search
    - POST /accessories/recommend
    - GET  /accessories/{accessory_id}
    - POST /inventory/check
    - POST /handover/create
    - POST /checkout
    """

    def __init__(self) -> None:
        self.base_url = settings.BACKEND_API_BASE_URL.rstrip("/")
        self.headers: dict[str, str] = {}
        if settings.BACKEND_API_TOKEN:
            self.headers["Authorization"] = f"Bearer {settings.BACKEND_API_TOKEN}"
        # Shared connection pool — one client for process lifetime.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers=self.headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request_url(
        self,
        method: str,
        url: str,
        *,
        path_label: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """HTTP request with 429/5xx retry, Retry-After, and typed failure."""
        label = path_label or url
        logger.info("Backend API request start method=%s path=%s", method, label)
        start = time.perf_counter()
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
                if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = min(float(retry_after), _MAX_BACKOFF_S)
                    else:
                        delay = min(
                            _BASE_BACKOFF_S * (2**attempt) + random.uniform(0, 0.25),
                            _MAX_BACKOFF_S,
                        )
                    logger.warning(
                        "Backend API retryable status=%s path=%s attempt=%s sleep=%.2fs",
                        response.status_code,
                        label,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                duration_ms = (time.perf_counter() - start) * 1000
                logger.info(
                    "Backend API request end method=%s path=%s status_code=%s duration_ms=%.2f",
                    method,
                    label,
                    response.status_code,
                    duration_ms,
                )
                if not response.content:
                    return {}
                return response.json()

            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _BASE_BACKOFF_S * (2**attempt) + random.uniform(0, 0.25)
                    logger.warning(
                        "Backend API timeout path=%s attempt=%s sleep=%.2fs",
                        label,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                duration_ms = (time.perf_counter() - start) * 1000
                logger.warning(
                    "Backend API request timeout method=%s path=%s duration_ms=%.2f",
                    method,
                    label,
                    duration_ms,
                )
                raise BackendAPIError(
                    f"Backend timeout on {label}",
                    path=label,
                    retryable=True,
                ) from exc

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                duration_ms = (time.perf_counter() - start) * 1000
                logger.error(
                    "Backend API request failed method=%s path=%s status_code=%s duration_ms=%.2f",
                    method,
                    label,
                    status,
                    duration_ms,
                )
                raise BackendAPIError(
                    f"Backend HTTP {status} on {label}",
                    status_code=status,
                    path=label,
                    retryable=status in _RETRYABLE_STATUS,
                ) from exc

            except BackendAPIError:
                raise

            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _BASE_BACKOFF_S * (2**attempt) + random.uniform(0, 0.25)
                    logger.warning(
                        "Backend API error path=%s attempt=%s sleep=%.2fs err=%s",
                        label,
                        attempt + 1,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                duration_ms = (time.perf_counter() - start) * 1000
                logger.exception(
                    "Backend API request error method=%s path=%s duration_ms=%.2f",
                    method,
                    label,
                    duration_ms,
                )
                raise BackendAPIError(
                    f"Backend error on {label}: {exc}",
                    path=label,
                    retryable=True,
                ) from exc

        raise BackendAPIError(
            f"Backend exhausted retries on {label}",
            path=label,
            retryable=True,
        ) from last_exc

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        return await self._request_url(method, url, path_label=path, **kwargs)

    async def search_products(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Search products using v2 API.

        Pass through user/LLM fields. Do not invent fashion mappings
        (e.g. Barat→Sherwani or ivory≈cream) in this client.
        """
        if settings.USE_MOCK_DATA:
            logger.info("Using MOCK DATA for search_products")
            mock = _get_mock_service()
            products = mock.search_mock_products(dict(filters))
            return [_normalize_product(p) for p in products]

        event_type = filters.get("occasion") or filters.get("event_type")
        budget = filters.get("budget_max")
        if budget is None and filters.get("budget") is not None:
            budget = filters.get("budget")

        logger.info(
            "Backend API search_products start category=%s color=%s event_type=%s "
            "budget=%s fabric_catalog_code=%s pattern=%s season=%s query=%s",
            filters.get("product_type") or filters.get("category"),
            filters.get("color"),
            event_type,
            budget,
            filters.get("fabric") or filters.get("fabric_catalog_code"),
            filters.get("pattern"),
            filters.get("season"),
            filters.get("query"),
        )

        body: dict[str, Any] = {}
        
        # Free text search
        if filters.get("query"):
            body["query"] = str(filters["query"]).strip()
        
        category = filters.get("product_type") or filters.get("category")
        if category:
            body["category"] = resolve_category_against_catalog(
                category,
                filters.get("catalog_categories"),
            )
        
        if filters.get("color"):
            body["color"] = _normalize_color(filters["color"])
        
        # event_type (was occasion)
        if event_type:
            body["event_type"] = _normalize_event_type(str(event_type))
        
        # budget (was budget_max)
        if budget is not None:
            body["budget"] = float(budget)
        
        # fabric_catalog_code (was fabric)
        fabric_code = filters.get("fabric") or filters.get("fabric_catalog_code")
        if fabric_code:
            body["fabric_catalog_code"] = str(fabric_code).strip()
        
        if filters.get("pattern"):
            body["pattern"] = str(filters["pattern"]).strip()
        
        if filters.get("season"):
            from app.services.styling_rules import map_season_for_catalog_api

            body["season"] = map_season_for_catalog_api(str(filters["season"])) or str(
                filters["season"]
            ).strip()
        
        # Pagination: v2 uses page (1-based) instead of offset
        limit = int(filters.get("limit") or 10)
        offset = int(filters.get("offset") or 0)
        page = (offset // limit) + 1 if limit > 0 else 1
        body["limit"] = limit
        body["page"] = page

        logger.info("Backend API search_products request body=%s", body)
        response = await self._request("POST", "/products/search", json=body)
        products = response.get("products", []) if isinstance(response, dict) else []
        normalized = [_normalize_product(product) for product in products]

        # Prioritize products that have explicit non-zero database prices first
        normalized.sort(key=lambda p: 0 if p.get("_has_db_price") else 1)

        # Client-side budget filter as safety net: only positive prices <= budget
        if budget is not None:
            capped = [p for p in normalized if 0 < float(p.get("price") or 0) <= float(budget)]
            logger.info(
                "Backend API search_products budget filter budget=%s before=%s after=%s",
                budget,
                len(normalized),
                len(capped),
            )
            normalized = capped

        mock = _get_mock_service()
        before_event = len(normalized)
        normalized = mock.filter_products_by_stated_event(
            normalized,
            event_type,
            filters.get("query"),
        )
        if before_event != len(normalized):
            logger.info(
                "Backend API search_products event filter before=%s after=%s event_type=%s",
                before_event,
                len(normalized),
                event_type,
            )

        logger.info("Backend API search_products end result_count=%s", len(normalized))
        return normalized

    async def get_product_details(self, product_id: str) -> dict[str, Any] | None:
        logger.info("Backend API get_product_details start product_id=%s", product_id)
        
        # Check if mock mode is enabled
        if settings.USE_MOCK_DATA:
            logger.info("Using MOCK DATA for get_product_details")
            mock = _get_mock_service()
            product = mock.get_mock_product_details(product_id)
            return _normalize_product(product) if product else None
        
        try:
            product = await self._request("GET", f"/products/{product_id}")
        except BackendAPIError as exc:
            if exc.status_code == 404:
                logger.warning(
                    "Backend API get_product_details not found product_id=%s",
                    product_id,
                )
                return None
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning(
                    "Backend API get_product_details not found product_id=%s",
                    product_id,
                )
                return None
            raise

        # Unwrap {status, data: {product}} or {product} if present
        if isinstance(product, dict):
            if isinstance(product.get("data"), dict):
                data = product["data"]
                product = data.get("product") if isinstance(data.get("product"), dict) else data
            elif isinstance(product.get("product"), dict):
                product = product["product"]

        normalized = _normalize_product(product) if isinstance(product, dict) else product
        logger.info(
            "Backend API get_product_details end product_id=%s found=%s variations=%s",
            product_id,
            normalized is not None,
            len((normalized or {}).get("variations") or []) if isinstance(normalized, dict) else 0,
        )
        return normalized

    async def search_fabrics(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Search fabrics using v2 API.
        
        Agent filters → Backend v2 fields:
        - category → dress_category
        - offset → page (calculated)
        - query, catalog_code, fabric_type, color, pattern, season, limit kept/added
        """
        # Check if mock mode is enabled
        if settings.USE_MOCK_DATA:
            logger.info("Using MOCK DATA for search_fabrics")
            mock = _get_mock_service()
            fabrics = mock.search_mock_fabrics(filters)
            return [_normalize_fabric(f) for f in fabrics]
        
        logger.info(
            "Backend API search_fabrics start fabric_type=%s color=%s dress_category=%s "
            "season=%s pattern=%s query=%s catalog_code=%s",
            filters.get("fabric_type"),
            filters.get("color"),
            filters.get("category") or filters.get("dress_category"),
            filters.get("season"),
            filters.get("pattern"),
            filters.get("query"),
            filters.get("catalog_code"),
        )
        
        body: dict[str, Any] = {}
        
        # Free text search
        if filters.get("query"):
            body["query"] = str(filters["query"]).strip()
        
        # catalog_code filter
        if filters.get("catalog_code"):
            body["catalog_code"] = str(filters["catalog_code"]).strip()
        
        if filters.get("fabric_type"):
            body["fabric_type"] = str(filters["fabric_type"]).strip()
        
        if filters.get("color"):
            body["color"] = str(filters["color"]).strip()
        
        if filters.get("pattern"):
            body["pattern"] = str(filters["pattern"]).strip()
        
        # dress_category (was category)
        dress_category = filters.get("category") or filters.get("dress_category")
        if dress_category:
            body["dress_category"] = str(dress_category).strip()
        
        if filters.get("season"):
            from app.services.styling_rules import map_season_for_catalog_api

            body["season"] = map_season_for_catalog_api(str(filters["season"])) or str(
                filters["season"]
            ).strip()
        
        # Pagination: v2 uses page (1-based) instead of offset
        limit = int(filters.get("limit") or 10)
        offset = int(filters.get("offset") or 0)
        page = (offset // limit) + 1 if limit > 0 else 1
        body["limit"] = limit
        body["page"] = page

        response = await self._request("POST", "/fabrics/search", json=body)
        fabrics = response.get("fabrics", []) if isinstance(response, dict) else []
        normalized = [_normalize_fabric(item) for item in fabrics]
        logger.info("Backend API search_fabrics end result_count=%s", len(normalized))
        return normalized

    async def get_fabric_details(self, catalog_code: str) -> dict[str, Any] | None:
        logger.info("Backend API get_fabric_details start catalog_code=%s", catalog_code)
        
        # Check if mock mode is enabled
        if settings.USE_MOCK_DATA:
            logger.info("Using MOCK DATA for get_fabric_details")
            mock = _get_mock_service()
            fabric = mock.get_mock_fabric_details(catalog_code)
            return _normalize_fabric(fabric) if fabric else None
        
        try:
            fabric = await self._request("GET", f"/fabrics/{catalog_code}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning(
                    "Backend API get_fabric_details not found catalog_code=%s",
                    catalog_code,
                )
                return None
            raise

        normalized = _normalize_fabric(fabric) if isinstance(fabric, dict) else fabric
        logger.info(
            "Backend API get_fabric_details end catalog_code=%s found=%s",
            catalog_code,
            normalized is not None,
        )
        return normalized

    async def check_inventory(
        self,
        product_id: str,
        size: str | None,
        color: str | None,
        quantity: int = 1,
    ) -> dict[str, Any]:
        logger.info(
            "Backend API check_inventory start product_id=%s size=%s color=%s quantity=%s",
            product_id,
            size,
            color,
            quantity,
        )
        
        # Check if mock mode is enabled
        if settings.USE_MOCK_DATA:
            logger.info("Using MOCK DATA for check_inventory")
            mock = _get_mock_service()
            return mock.check_mock_inventory(product_id, size, color, quantity)
        
        if not size or not color:
            result = {"available": False, "reason": "size_or_color_missing"}
            logger.info(
                "Backend API check_inventory end product_id=%s available=%s reason=%s",
                product_id,
                result["available"],
                result["reason"],
            )
            return result

        response = await self._request(
            "POST",
            "/inventory/check",
            json={
                "product_id": product_id,
                "size": size,
                "color": _normalize_color(color),
                "quantity": quantity,
            },
        )
        result = _normalize_inventory_result(response) if isinstance(response, dict) else response
        logger.info(
            "Backend API check_inventory end product_id=%s available=%s stock_quantity=%s",
            product_id,
            result.get("available"),
            result.get("stock_quantity"),
        )
        return result

    async def create_handover(self, payload: dict[str, Any]) -> dict[str, Any]:
        contact = payload.get("customer_contact") or {}
        phone = contact.get("phone") or contact.get("whatsapp") or ""
        phone_digits = "".join(ch for ch in str(phone) if ch.isdigit())
        phone_masked = f"***{phone_digits[-4:]}" if len(phone_digits) >= 4 else "n/a"
        api_body = _build_handover_api_body(payload)
        logger.info(
            "Backend API create_handover start session_id=%s contact_name_present=%s phone_masked=%s summary_length=%s priority=%s",
            payload.get("session_id"),
            bool(contact.get("name")),
            phone_masked,
            len(api_body.get("conversation_summary") or ""),
            api_body.get("priority"),
        )

        result = await self._request("POST", "/handover/create", json=api_body)
        logger.info(
            "Backend API create_handover end session_id=%s ticket_id=%s status=%s",
            payload.get("session_id"),
            result.get("ticket_id") if isinstance(result, dict) else None,
            result.get("status") if isinstance(result, dict) else None,
        )
        return result

    async def get_pricing_config(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """
        GET /pricing-config → Sales Agent pricing tables (styles, grades, embroidery, markup, addons, delivery).

        Returns normalized dict via pricing_config.normalize_pricing_config (also cached).
        On API failure or empty tables, falls back to excel_reference so CUSTOM checkout
        never ships without a calculator unit_price.
        """
        from app.services.pricing_config import (
            excel_reference_pricing_config,
            get_cached_pricing_config,
            normalize_pricing_config,
            set_cached_pricing_config,
        )

        if not force_refresh:
            cached = get_cached_pricing_config()
            # Ignore empty/corrupt cache from older normalizers
            if cached is not None and cached.styles and cached.fabric_grades:
                return _pricing_config_response(cached)

        if settings.USE_MOCK_DATA:
            logger.info("Using MOCK / excel reference pricing-config")
            cfg = set_cached_pricing_config(excel_reference_pricing_config())
            return _pricing_config_response(cfg)

        logger.info("Backend API get_pricing_config start")
        result: Any = None
        try:
            result = await self._request("GET", "/pricing-config")
            cfg = normalize_pricing_config(result)
        except BackendAPIError as exc:
            logger.warning(
                "pricing-config API failed — using excel_reference fallback err=%s",
                exc,
            )
            cfg = set_cached_pricing_config(excel_reference_pricing_config())
            return _pricing_config_response(cfg)
        except Exception as exc:
            logger.warning(
                "pricing-config unexpected error — using excel_reference fallback err=%s",
                exc,
            )
            cfg = set_cached_pricing_config(excel_reference_pricing_config())
            return _pricing_config_response(cfg)

        if not cfg.styles or not cfg.fabric_grades:
            logger.warning(
                "pricing-config empty after normalize styles=%s grades=%s — excel_reference fallback",
                len(cfg.styles),
                len(cfg.fabric_grades),
            )
            cfg = set_cached_pricing_config(excel_reference_pricing_config())
            return _pricing_config_response(
                cfg,
                raw=result if isinstance(result, dict) else {},
            )

        set_cached_pricing_config(cfg)
        logger.info(
            "Backend API get_pricing_config end styles=%s grades=%s embroidery=%s addons=%s markup=%s delivery_default=%s",
            len(cfg.styles),
            len(cfg.fabric_grades),
            len(cfg.embroidery),
            len(cfg.addons),
            cfg.markup,
            cfg.delivery_fee_default,
        )
        return _pricing_config_response(
            cfg,
            raw=result if isinstance(result, dict) else {},
        )

    async def get_chatbot_quotas(self, session_id: str | None = None) -> dict[str, Any]:
        """
        GET /limits — daily chatbot quotas from Royal Attire.

        Expected:
        { "status": "success", "data": { "dailyMessageLimit": 50, "dailyImageLimit": 10 } }
        """
        _ = session_id
        logger.info("Backend API get_chatbot_quotas start (GET /limits)")
        result = await self._request("GET", "/limits")
        data = (
            result.get("data")
            if isinstance(result, dict) and isinstance(result.get("data"), dict)
            else result
        )
        if not isinstance(data, dict):
            data = {}
        # Support nested limits{} or flat dailyMessageLimit / dailyImageLimit.
        limits = data.get("limits") if isinstance(data.get("limits"), dict) else data
        msg = (
            limits.get("dailyMessageLimit")
            if limits.get("dailyMessageLimit") is not None
            else limits.get("no_of_ai_messages_perday")
            if limits.get("no_of_ai_messages_perday") is not None
            else limits.get("ai_messages")
        )
        img = (
            limits.get("dailyImageLimit")
            if limits.get("dailyImageLimit") is not None
            else limits.get("no_of_custom_images_perday")
            if limits.get("no_of_custom_images_perday") is not None
            else limits.get("custom_images")
        )
        ok = True
        if isinstance(result, dict):
            if "status" in result:
                ok = str(result.get("status")).lower() in ("success", "ok", "true")
            elif "success" in result:
                ok = bool(result.get("success"))
        out = {
            "success": ok,
            "timezone": data.get("timezone") or "Asia/Karachi",
            "limits": {
                "no_of_ai_messages_perday": msg,
                "no_of_custom_images_perday": img,
            },
        }
        logger.info("Backend API get_chatbot_quotas end limits=%s", out["limits"])
        return out

    async def create_checkout_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /checkout → { data: { checkout_url, session_id, ... } }."""
        items = payload.get("items") or []
        logger.info(
            "Backend API create_checkout_session start user_type=%s item_count=%s",
            payload.get("user_type"),
            len(items) if isinstance(items, list) else 0,
        )
        if settings.USE_MOCK_DATA:
            logger.info("Backend API create_checkout_session skipped — USE_MOCK_DATA")
            print_payload("CHECKOUT API SKIPPED USE_MOCK_DATA", payload)
            return {"success": False, "checkout_url": None, "session_id": None}

        print_payload("CHECKOUT API REQUEST POST /checkout", payload)
        try:
            result = await self._request("POST", "/checkout", json=payload)
        except Exception as exc:
            print_payload("CHECKOUT API ERROR POST /checkout", {"error": str(exc), "type": type(exc).__name__})
            raise
        print_payload("CHECKOUT API RESPONSE POST /checkout", result)
        if not isinstance(result, dict):
            logger.warning("Backend API create_checkout_session unexpected payload type")
            return {"success": False, "checkout_url": None, "session_id": None}

        data = result.get("data") if isinstance(result.get("data"), dict) else result
        checkout_url = data.get("checkout_url")
        session_id = data.get("session_id")
        success = bool(result.get("success", True) and checkout_url)
        logger.info(
            "Backend API create_checkout_session end success=%s has_url=%s session_id=%s",
            success,
            bool(checkout_url),
            session_id,
        )
        return {
            "success": success,
            "checkout_url": checkout_url,
            "session_id": session_id,
            "items_summary": data.get("items_summary"),
            "total_amount": data.get("total_amount"),
            "currency": data.get("currency"),
            "expires_in": data.get("expires_in"),
        }

    async def search_accessories(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """POST /accessories/search"""
        body: dict[str, Any] = {}
        for key in ("category", "event_type", "accessory_type", "color", "query"):
            val = filters.get(key)
            if val:
                if key == "event_type":
                    body[key] = _normalize_accessory_event_type(str(val)) or str(val).strip()
                else:
                    body[key] = str(val).strip()
        body["page"] = int(filters.get("page") or 1)
        body["limit"] = int(filters.get("limit") or 6)

        logger.info("Backend API search_accessories start body=%s", body)
        response = await self._request("POST", "/accessories/search", json=body)
        raw = response.get("accessories", []) if isinstance(response, dict) else []
        normalized = [_normalize_accessory(item) for item in raw if isinstance(item, dict)]

        # Fallback: if event_type was provided but returned 0 accessories, retry without event_type
        if not normalized and body.get("event_type"):
            logger.info("search_accessories returned 0 items with event_type=%s, retrying without event_type", body["event_type"])
            fallback_body = dict(body)
            fallback_body.pop("event_type", None)
            fallback_resp = await self._request("POST", "/accessories/search", json=fallback_body)
            fallback_raw = fallback_resp.get("accessories", []) if isinstance(fallback_resp, dict) else []
            normalized = [_normalize_accessory(item) for item in fallback_raw if isinstance(item, dict)]

        logger.info("Backend API search_accessories end count=%s", len(normalized))
        return normalized

    async def get_accessory(self, accessory_id: str) -> dict[str, Any] | None:
        """GET /accessories/{accessory_id}"""
        if not accessory_id:
            return None
        try:
            response = await self._request("GET", f"/accessories/{accessory_id}")
        except Exception:
            logger.exception("Backend API get_accessory failed accessory_id=%s", accessory_id)
            raise
        if not isinstance(response, dict):
            return None
        return _normalize_accessory(response)

    async def recommend_accessories(
        self,
        *,
        category: str,
        product_id: str | None = None,
        event_type: str | None = None,
        main_product_price: float | None = None,
        limit: int = 4,
    ) -> dict[str, Any]:
        """
        POST /accessories/recommend
        Returns {accessories: [...], bundle_offer: {...}}.
        """
        body: dict[str, Any] = {
            "category": str(category).strip(),
            "limit": int(limit or 4),
        }
        if product_id:
            body["product_id"] = str(product_id)
        if event_type:
            body["event_type"] = _normalize_accessory_event_type(str(event_type)) or str(event_type).strip()
        if main_product_price is not None:
            body["main_product_price"] = float(main_product_price)

        logger.info("Backend API recommend_accessories start body=%s", body)
        response = await self._request("POST", "/accessories/recommend", json=body)
        if not isinstance(response, dict):
            return {"accessories": [], "bundle_offer": None}

        accessories = [
            _normalize_accessory(item)
            for item in (response.get("accessories") or [])
            if isinstance(item, dict)
        ]
        bundle_offer = response.get("bundle_offer")
        if bundle_offer is not None and not isinstance(bundle_offer, dict):
            bundle_offer = None

        # Fallback: if event_type was provided but returned 0 accessories, retry without event_type
        if not accessories and body.get("event_type"):
            logger.info("recommend_accessories returned 0 items with event_type=%s, retrying without event_type", body["event_type"])
            fallback_body = dict(body)
            fallback_body.pop("event_type", None)
            fallback_resp = await self._request("POST", "/accessories/recommend", json=fallback_body)
            if isinstance(fallback_resp, dict):
                accessories = [
                    _normalize_accessory(item)
                    for item in (fallback_resp.get("accessories") or [])
                    if isinstance(item, dict)
                ]
                if not bundle_offer and isinstance(fallback_resp.get("bundle_offer"), dict):
                    bundle_offer = fallback_resp.get("bundle_offer")

        logger.info(
            "Backend API recommend_accessories end count=%s bundle_type=%s",
            len(accessories),
            (bundle_offer or {}).get("type"),
        )
        return {"accessories": accessories, "bundle_offer": bundle_offer}

    def _api_v2_root(self) -> str:
        """Base URL without /sales-agent, e.g. https://host/api/v2."""
        base = self.base_url.rstrip("/")
        if base.endswith("/sales-agent"):
            return base[: -len("/sales-agent")]
        return base

    async def list_categories(self) -> list[dict[str, Any]]:
        """GET /api/v2/categories — live catalog category names and product counts."""
        global _categories_cache, _categories_cache_failed
        if settings.USE_MOCK_DATA:
            mock = _get_mock_service()
            return mock.list_mock_categories()

        if _categories_cache is not None:
            return list(_categories_cache)
        if _categories_cache_failed:
            return []

        url = f"{self._api_v2_root()}/categories"
        logger.info("Backend API list_categories start url=%s", url)
        start = time.perf_counter()
        try:
            payload = await self._request_url("GET", url, path_label="/categories")
        except BackendAPIError:
            _categories_cache_failed = True
            logger.exception("Backend API list_categories failed — caching empty for process lifetime")
            return []
        duration_ms = (time.perf_counter() - start) * 1000

        raw_list = []
        if isinstance(payload, dict):
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            raw_list = (data or {}).get("categories") or payload.get("categories") or []
        elif isinstance(payload, list):
            raw_list = payload

        categories: list[dict[str, Any]] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            count_block = item.get("_count") or {}
            product_count = count_block.get("products")
            if product_count is None:
                product_count = item.get("product_count") or item.get("products_count") or 0
            categories.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "slug": item.get("slug"),
                    "product_count": int(product_count or 0),
                }
            )
        _categories_cache = categories
        logger.info(
            "Backend API list_categories end count=%s duration_ms=%.2f",
            len(categories),
            duration_ms,
        )
        return list(categories)

    async def list_variations(self) -> list[dict[str, Any]]:
        """GET /api/v2/variations — live category style variations (not fixed)."""
        global _variations_cache, _variations_cache_failed
        if settings.USE_MOCK_DATA:
            return []

        if _variations_cache is not None:
            return list(_variations_cache)
        if _variations_cache_failed:
            return []

        url = f"{self._api_v2_root()}/variations"
        logger.info("Backend API list_variations start url=%s", url)
        start = time.perf_counter()
        try:
            payload = await self._request_url("GET", url, path_label="/variations")
        except BackendAPIError:
            _variations_cache_failed = True
            logger.exception("Backend API list_variations failed — caching empty for process lifetime")
            return []
        duration_ms = (time.perf_counter() - start) * 1000

        raw_list: list[Any] = []
        if isinstance(payload, dict):
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            raw_list = (data or {}).get("variations") or payload.get("variations") or []
        elif isinstance(payload, list):
            raw_list = payload

        variations: list[dict[str, Any]] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            cat = item.get("category") if isinstance(item.get("category"), dict) else {}
            count_block = item.get("_count") if isinstance(item.get("_count"), dict) else {}
            variations.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "slug": item.get("slug"),
                    "description": item.get("description"),
                    "image_url": item.get("imageUrl") or item.get("image_url"),
                    "is_active": bool(item.get("isActive", item.get("is_active", True))),
                    "category_id": item.get("categoryId") or item.get("category_id") or cat.get("id"),
                    "category": {
                        "id": cat.get("id"),
                        "name": cat.get("name"),
                        "slug": cat.get("slug"),
                        "is_active": cat.get("isActive", cat.get("is_active", True)),
                    }
                    if cat
                    else None,
                    "category_name": cat.get("name"),
                    "product_count": int(count_block.get("products") or item.get("product_count") or 0),
                }
            )
        _variations_cache = variations
        logger.info(
            "Backend API list_variations end count=%s duration_ms=%.2f",
            len(variations),
            duration_ms,
        )
        return list(variations)

    async def list_size_charts(self) -> list[dict[str, Any]]:
        """GET {api_v2_root}/size-charts → data.sizeCharts (same host as categories, not /sales-agent)."""
        global _size_charts_cache, _size_charts_cache_failed
        if settings.USE_MOCK_DATA:
            mock = _get_mock_service()
            return mock.list_mock_size_charts()

        if _size_charts_cache is not None:
            return list(_size_charts_cache)
        if _size_charts_cache_failed:
            return []

        url = f"{self._api_v2_root()}/size-charts"
        logger.info("Backend API list_size_charts start url=%s", url)
        start = time.perf_counter()
        try:
            payload = await self._request_url("GET", url, path_label="/size-charts")
        except BackendAPIError:
            _size_charts_cache_failed = True
            logger.exception("Backend API list_size_charts failed — caching empty for process lifetime")
            return []
        duration_ms = (time.perf_counter() - start) * 1000

        raw_list: list[Any] = []
        if isinstance(payload, dict):
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            raw_list = (data or {}).get("sizeCharts") or payload.get("sizeCharts") or payload.get("size_charts") or []
        elif isinstance(payload, list):
            raw_list = payload

        charts: list[dict[str, Any]] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            charts.append(_normalize_size_chart(item))
        _size_charts_cache = charts
        logger.info(
            "Backend API list_size_charts end count=%s duration_ms=%.2f",
            len(charts),
            duration_ms,
        )
        return list(charts)


def _normalize_size_chart(item: dict[str, Any]) -> dict[str, Any]:
    """Stable agent shape for a live size-chart payload."""
    cat = item.get("category") if isinstance(item.get("category"), dict) else {}
    how_raw = item.get("howToMeasure") or item.get("how_to_measure") or []
    if not isinstance(how_raw, list):
        how_raw = []
    rows_raw = item.get("rows") or []
    if not isinstance(rows_raw, list):
        rows_raw = []
    columns = item.get("columns") or []
    if not isinstance(columns, list):
        columns = []
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "category_id": item.get("categoryId") or item.get("category_id") or cat.get("id"),
        "category": {
            "id": cat.get("id"),
            "name": cat.get("name"),
            "slug": cat.get("slug"),
        }
        if cat
        else None,
        "category_name": cat.get("name") or item.get("category_name"),
        "description": item.get("description"),
        "columns": [str(c) for c in columns],
        "rows": [dict(r) for r in rows_raw if isinstance(r, dict)],
        "how_to_measure": [
            {"part": h.get("part"), "instruction": h.get("instruction")}
            for h in how_raw
            if isinstance(h, dict)
        ],
        "is_default": bool(item.get("isDefault", item.get("is_default", False))),
    }


backend_api = BackendAPIClient()
