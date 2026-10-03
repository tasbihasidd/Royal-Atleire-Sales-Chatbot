"""Normalize + cache Sales Agent pricing-config."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Process cache (TTL seconds)
_CACHE: dict[str, Any] | None = None
_CACHE_AT: float = 0.0
_CACHE_TTL_S = 600.0  # 10 minutes


@dataclass
class StylePricing:
    base: float
    fabric_length: float
    category: str | None = None


@dataclass
class AddonPricing:
    price: float
    charge_per: str  # "outfit" | "order"
    name: str | None = None


@dataclass
class PricingConfig:
    styles: dict[str, StylePricing] = field(default_factory=dict)
    fabric_grades: dict[str, float] = field(default_factory=dict)  # grade -> rate/m
    embroidery: dict[str, float] = field(default_factory=dict)  # tier -> £
    markup: float = 0.15
    addons: dict[str, AddonPricing] = field(default_factory=dict)
    delivery_fee_default: float = 0.0
    delivery_options: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def style_keys(self) -> list[str]:
        return list(self.styles.keys())


def clear_pricing_config_cache() -> None:
    global _CACHE, _CACHE_AT
    _CACHE = None
    _CACHE_AT = 0.0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _charge_per(value: Any) -> str:
    raw = str(value or "outfit").strip().lower()
    if raw in ("order", "once", "flat"):
        return "order"
    return "outfit"


def _unwrap_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if isinstance(data.get("pricing_config"), dict):
        return dict(data["pricing_config"])
    if isinstance(data.get("pricing"), dict):
        return dict(data["pricing"])
    return dict(data)


def _normalize_styles(blob: Any) -> dict[str, StylePricing]:
    out: dict[str, StylePricing] = {}
    if isinstance(blob, dict):
        for key, val in blob.items():
            if isinstance(val, dict) and (
                "base" in val
                or "fabric_length" in val
                or "fabricLength" in val
                or "metres" in val
                or "meters" in val
            ):
                out[str(key)] = StylePricing(
                    base=_as_float(val.get("base") or val.get("style_base") or val.get("base_price")),
                    fabric_length=_as_float(
                        val.get("fabric_length")
                        or val.get("fabricLength")
                        or val.get("metres")
                        or val.get("meters")
                    ),
                    category=str(val.get("category") or "") or None,
                )
    if isinstance(blob, list):
        for row in blob:
            if not isinstance(row, dict):
                continue
            name = row.get("style") or row.get("name") or row.get("key")
            if not name:
                continue
            out[str(name)] = StylePricing(
                base=_as_float(row.get("base") or row.get("style_base") or row.get("base_price")),
                fabric_length=_as_float(
                    row.get("fabric_length")
                    or row.get("fabricLength")
                    or row.get("metres")
                    or row.get("meters")
                ),
                category=str(row.get("category") or "") or None,
            )
    return out


def _normalize_grades(blob: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(blob, dict):
        for key, val in blob.items():
            if isinstance(val, dict):
                rate = val.get("rate_per_metre") or val.get("rate") or val.get("price") or val.get("per_metre")
                out[str(key)] = _as_float(rate)
            else:
                out[str(key)] = _as_float(val)
    elif isinstance(blob, list):
        for row in blob:
            if not isinstance(row, dict):
                continue
            name = row.get("grade") or row.get("name") or row.get("fabric") or row.get("key")
            if not name:
                continue
            rate = (
                row.get("rate_per_metre")
                or row.get("rate")
                or row.get("price")
                or row.get("per_metre")
            )
            out[str(name)] = _as_float(rate)
    return out


def _normalize_embroidery(blob: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(blob, dict):
        for key, val in blob.items():
            if isinstance(val, dict):
                out[str(key)] = _as_float(val.get("price") or val.get("cost") or val.get("amount"))
            else:
                out[str(key)] = _as_float(val)
    elif isinstance(blob, list):
        for row in blob:
            if not isinstance(row, dict):
                continue
            name = row.get("tier") or row.get("embroidery") or row.get("name") or row.get("key")
            if name is None:
                continue
            out[str(name)] = _as_float(row.get("price") or row.get("cost") or row.get("amount"))
    return out


def _normalize_addons(blob: Any) -> dict[str, AddonPricing]:
    out: dict[str, AddonPricing] = {}
    rows: list[tuple[str, dict[str, Any]]] = []
    if isinstance(blob, dict):
        for key, val in blob.items():
            if isinstance(val, dict):
                rows.append((str(key), val))
            else:
                rows.append((str(key), {"price": val, "charge_per": "outfit"}))
    elif isinstance(blob, list):
        for row in blob:
            if not isinstance(row, dict):
                continue
            key = str(
                row.get("id")
                or row.get("key")
                or row.get("addon")
                or row.get("name")
                or ""
            ).strip()
            if not key:
                continue
            rows.append((key, row))
    for key, row in rows:
        price = _as_float(row.get("price") or row.get("amount") or row.get("cost"))
        charge = _charge_per(
            row.get("charge_per")
            or row.get("charged_per")
            or row.get("per")
            or row.get("type")
        )
        display = row.get("name") or row.get("title") or key
        out[key] = AddonPricing(price=price, charge_per=charge, name=str(display))
        # Also index by lowercase name for matching accessories
        name_key = str(display).strip().lower()
        if name_key and name_key not in out:
            out[name_key] = out[key]
    return out


def _normalize_delivery(blob: Any, root: dict[str, Any]) -> tuple[float, dict[str, float]]:
    options: dict[str, float] = {}
    default = _as_float(
        root.get("delivery_fee")
        or root.get("default_delivery_fee")
        or root.get("deliveryFee")
    )
    if isinstance(blob, (int, float, str)):
        default = _as_float(blob, default)
        return default, options
    if isinstance(blob, dict):
        default = _as_float(
            blob.get("default")
            or blob.get("fee")
            or blob.get("delivery_fee")
            or blob.get("standard")
            or default
        )
        for key, val in blob.items():
            if key in ("default", "fee", "delivery_fee", "standard"):
                continue
            if isinstance(val, dict):
                options[str(key)] = _as_float(val.get("fee") or val.get("price") or val.get("amount"))
            else:
                options[str(key)] = _as_float(val)
    elif isinstance(blob, list):
        for row in blob:
            if not isinstance(row, dict):
                continue
            key = str(row.get("id") or row.get("name") or row.get("key") or "").strip()
            fee = _as_float(row.get("fee") or row.get("price") or row.get("amount"))
            if key:
                options[key] = fee
            if row.get("default") or row.get("is_default"):
                default = fee
    return default, options


def _root_get(root: dict[str, Any], *names: str) -> Any:
    """Case-insensitive key lookup (API returns STYLES / FABRICS / EMBROIDERY)."""
    for name in names:
        if name in root and root[name] is not None:
            return root[name]
    lower_map = {str(k).lower(): v for k, v in root.items()}
    for name in names:
        key = name.lower()
        if key in lower_map and lower_map[key] is not None:
            return lower_map[key]
    return None


def normalize_pricing_config(raw: Any) -> PricingConfig:
    root = _unwrap_payload(raw)
    styles = _normalize_styles(
        _root_get(root, "styles", "style", "garment_styles", "STYLES")
    )
    grades = _normalize_grades(
        _root_get(
            root,
            "fabric_grades",
            "fabrics",
            "fabric_grades_rates",
            "fabricGrades",
            "FABRICS",
        )
    )
    embroidery = _normalize_embroidery(
        _root_get(root, "embroidery", "embroidery_tiers", "embroideryTiers", "EMBROIDERY")
    )
    markup = _as_float(
        _root_get(root, "markup", "markup_rate", "mark_up") or 0.15,
        0.15,
    )
    if markup > 1:
        # allow 15 meaning 15%
        markup = markup / 100.0
    addons = _normalize_addons(_root_get(root, "addons", "add_ons", "addOns"))
    delivery_fee_default, delivery_options = _normalize_delivery(
        _root_get(root, "delivery", "delivery_fees", "shipping"),
        root,
    )
    return PricingConfig(
        styles=styles,
        fabric_grades=grades,
        embroidery=embroidery,
        markup=markup,
        addons=addons,
        delivery_fee_default=delivery_fee_default,
        delivery_options=delivery_options,
        raw=root,
    )


def resolve_delivery_fee(
    config: PricingConfig,
    *,
    option: str | None = None,
    override: float | None = None,
) -> float:
    if override is not None:
        return float(override)
    if option:
        key = str(option).strip()
        if key in config.delivery_options:
            return float(config.delivery_options[key])
        # case-insensitive
        lower = {k.lower(): v for k, v in config.delivery_options.items()}
        if key.lower() in lower:
            return float(lower[key.lower()])
    return float(config.delivery_fee_default or 0.0)


def excel_reference_pricing_config() -> PricingConfig:
    """Excel Price List mirror — tests / offline only. Runtime prefers live API."""
    return PricingConfig(
        styles={
            "Sherwani": StylePricing(base=50, fabric_length=4.5, category="Sherwani"),
            "2-Piece Suit": StylePricing(base=40, fabric_length=3.0, category="Suit"),
            "3-Piece Suit": StylePricing(base=50, fabric_length=3.6, category="Suit"),
            "Double-Breasted Suit": StylePricing(base=52, fabric_length=3.4, category="Suit"),
            "Tuxedo": StylePricing(base=48, fabric_length=3.2, category="Tuxedo"),
            "Dinner Suit": StylePricing(base=44, fabric_length=3.0, category="Tuxedo"),
        },
        fabric_grades={
            "Standard blend": 10.0,
            "Silk blend": 16.0,
            "Jamawar / Velvet": 24.0,
        },
        embroidery={"None": 0.0, "Light": 20.0, "Medium": 40.0, "Heavy": 80.0},
        markup=0.15,
        addons={
            "swatch": AddonPricing(price=5.0, charge_per="order", name="Fabric swatch pack (posted)"),
            "Fabric swatch pack (posted)": AddonPricing(price=5.0, charge_per="order", name="Fabric swatch pack (posted)"),
            "stole": AddonPricing(price=5.0, charge_per="outfit", name="stole"),
            "alteration": AddonPricing(price=10.0, charge_per="outfit", name="Extra alteration round"),
            "rush": AddonPricing(price=30.0, charge_per="outfit", name="Rush production (peak season)"),
            "delay_guarantee": AddonPricing(price=10.0, charge_per="order", name="Delay-compensation guarantee"),
            "waistcoat": AddonPricing(price=20.0, charge_per="outfit", name="Extra piece (waistcoat / stole)"),
            "Extra piece (waistcoat / stole)": AddonPricing(
                price=20.0, charge_per="outfit", name="Extra piece (waistcoat / stole)"
            ),
            "monogram": AddonPricing(price=8.0, charge_per="outfit", name="Monogram / premium lining"),
        },
        delivery_fee_default=15.0,
        delivery_options={"standard": 15.0, "express": 30.0},
        raw={"source": "excel_reference"},
    )


def get_cached_pricing_config() -> PricingConfig | None:
    global _CACHE, _CACHE_AT
    if _CACHE is None:
        return None
    if time.monotonic() - _CACHE_AT > _CACHE_TTL_S:
        return None
    return _CACHE  # type: ignore[return-value]


def set_cached_pricing_config(config: PricingConfig) -> PricingConfig:
    global _CACHE, _CACHE_AT
    _CACHE = config
    _CACHE_AT = time.monotonic()
    return config
