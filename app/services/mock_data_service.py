"""
Mock Data Service for testing when backend APIs are unavailable or incomplete.

Toggle via environment variable: USE_MOCK_DATA=true
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Load mock data once at module import
_MOCK_DATA_PATH = Path(__file__).parent.parent / "data" / "mock_products.json"
_MOCK_DATA: dict[str, Any] = {}

try:
    if _MOCK_DATA_PATH.exists():
        with open(_MOCK_DATA_PATH, "r", encoding="utf-8") as f:
            _MOCK_DATA = json.load(f)
        logger.info("Mock data loaded: %d products, %d fabrics", 
                   len(_MOCK_DATA.get("products", [])),
                   len(_MOCK_DATA.get("fabrics", [])))
except Exception as e:
    logger.warning("Failed to load mock data: %s", e)


# Same event, different spellings — not a fashion rule (nikkah vs nikah).
_EVENT_SPELLINGS = {
    "nikah": "nikah",
    "nikkah": "nikah",
    "barat": "barat",
    "baraat": "barat",
    "walima": "walima",
    "valima": "walima",
    "mehndi": "mehndi",
    "mehendi": "mehndi",
}

_QUERY_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "with", "my", "me", "i", "you",
    "please", "show", "some", "want", "need", "like", "can", "could", "just",
    "this", "that", "on", "in", "of", "is", "are", "hai", "k", "ke", "ki",
    "ko", "se", "mein", "main", "mujhe", "chahiye", "dikhao", "wala", "wali",
}


def _reload_mock_data() -> None:
    global _MOCK_DATA
    try:
        if _MOCK_DATA_PATH.exists():
            with open(_MOCK_DATA_PATH, "r", encoding="utf-8") as f:
                _MOCK_DATA = json.load(f)
    except Exception:
        logger.exception("Failed to reload mock data")


def _tokens(text: str) -> set[str]:
    words = []
    current = []
    for ch in (text or "").lower():
        if ch.isalnum():
            current.append(ch)
        else:
            if current:
                words.append("".join(current))
                current = []
    if current:
        words.append("".join(current))
    tokens = set()
    for w in words:
        if len(w) < 3 or w in _QUERY_STOPWORDS:
            continue
        tokens.add(_EVENT_SPELLINGS.get(w, w))
    return tokens


def _fold_event(word: str) -> str | None:
    return _EVENT_SPELLINGS.get((word or "").lower().replace(" ", ""))


def _events_from_text(text: str) -> set[str]:
    canonical = set(_EVENT_SPELLINGS.values())
    return {tok for tok in _tokens(text) if tok in canonical}


def filter_products_by_stated_event(
    products: list[dict[str, Any]],
    *texts: str | None,
) -> list[dict[str, Any]]:
    """Drop items whose suitable_events do not include the user's stated event."""
    stated = _events_from_text(" ".join(str(t) for t in texts if t))
    if not stated:
        return list(products or [])
    kept: list[dict[str, Any]] = []
    for product in products or []:
        prod_events = _product_events(product)
        if prod_events and stated.isdisjoint(prod_events):
            continue
        kept.append(product)
    return kept


def _product_events(product: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    events = list(product.get("suitable_events") or [])
    if not events:
        occasion = product.get("occasion") or product.get("event_type") or ""
        events = [part.strip() for part in str(occasion).split(",") if part.strip()]
    for event in events:
        folded = _fold_event(str(event))
        found.add(folded or str(event).lower())
    return found


def _product_search_text(product: dict[str, Any]) -> str:
    parts = [
        product.get("name"),
        product.get("description"),
        product.get("category"),
        product.get("fabric"),
        product.get("pattern"),
        product.get("season"),
        product.get("embroidery"),
        " ".join(str(x) for x in (product.get("available_colors") or [])),
        " ".join(str(x) for x in (product.get("suitable_events") or [])),
    ]
    return " ".join(str(p) for p in parts if p)


def _matches_filter(product: dict, key: str, value: Any) -> bool:
    """Check if product matches a filter condition."""
    if value is None:
        return True
    
    product_value = product.get(key)
    if product_value is None:
        return True  # Don't filter out if field missing
    
    # Handle list fields (colors, sizes, events)
    if isinstance(product_value, list):
        if isinstance(value, str):
            return any(value.lower() in str(v).lower() for v in product_value)
        return value in product_value
    
    # String comparison (case-insensitive partial match)
    if isinstance(value, str) and isinstance(product_value, str):
        return value.lower() in product_value.lower()
    
    return product_value == value


def search_mock_products(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Rank catalog rows against the user's words.

    Hard filters: numeric budget, and stated ceremony (Nikkah vs Walima) after
    spelling fold only. Colour / season matching is token overlap — no fashion
    lookup tables. The LLM decides what to recommend from the ranked list.
    """
    _reload_mock_data()
    products = _MOCK_DATA.get("products", [])

    budget = filters.get("budget") or filters.get("budget_max")
    limit = int(filters.get("limit") or 10)

    query_parts = [
        filters.get("query"),
        filters.get("color"),
        filters.get("product_type") or filters.get("category"),
        filters.get("event_type") or filters.get("occasion"),
        filters.get("fabric"),
        filters.get("pattern"),
        filters.get("season"),
    ]
    query_tokens = _tokens(" ".join(str(p) for p in query_parts if p))
    stated_events = _events_from_text(" ".join(str(p) for p in query_parts if p))
    query_tokens |= stated_events

    requested_category = filters.get("product_type") or filters.get("category")
    catalog_categories = [
        str(p.get("category")).strip()
        for p in products
        if p.get("category") and p.get("category") != "Accessories"
    ]
    unique_cats = {c.lower(): c for c in catalog_categories}
    query_blob = " ".join(str(p) for p in query_parts if p).lower()
    if not requested_category:
        for cat_lower, cat in unique_cats.items():
            if cat_lower in query_tokens or cat_lower in query_blob:
                requested_category = cat
                break

    scored: list[tuple[int, dict[str, Any]]] = []
    for product in products:
        if (product.get("category") or "") == "Accessories":
            continue
        if requested_category:
            prod_cat = (product.get("category") or "").lower()
            if requested_category.lower() not in prod_cat and prod_cat not in requested_category.lower():
                continue
        if budget is not None and float(product.get("price") or 0) > float(budget):
            continue

        blob = _product_search_text(product)
        blob_tokens = _tokens(blob) | _product_events(product)
        score = len(query_tokens & blob_tokens) if query_tokens else 1
        if stated_events and stated_events & _product_events(product):
            score += 2
        if score <= 0:
            continue
        scored.append((score, product))

    scored.sort(key=lambda item: item[0], reverse=True)
    ranked = [p for _, p in scored]
    results = filter_products_by_stated_event(ranked, *query_parts)[:limit]

    logger.info(
        "Mock search_products query_tokens=%s results=%d top_scores=%s",
        sorted(query_tokens),
        len(results),
        [s for s, _ in scored[:limit]],
    )
    return results


def get_mock_product_details(product_id: str) -> dict[str, Any] | None:
    """Get product by ID from mock data."""
    products = _MOCK_DATA.get("products", [])
    for product in products:
        if product.get("product_id") == product_id:
            return product
    return None


def search_mock_fabrics(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Search mock fabrics with filters.
    
    Supported filters:
    - fabric_type
    - color
    - dress_category / category
    - season
    - pattern
    - query
    """
    fabrics = _MOCK_DATA.get("fabrics", [])
    
    fabric_type = filters.get("fabric_type")
    color = filters.get("color")
    category = filters.get("dress_category") or filters.get("category")
    season = filters.get("season")
    pattern = filters.get("pattern")
    query = filters.get("query", "").lower()
    limit = int(filters.get("limit") or 10)
    
    results = []
    
    for fabric in fabrics:
        # Fabric type filter
        if fabric_type:
            types = fabric.get("fabric_type", [])
            if not any(fabric_type.lower() in t.lower() for t in types):
                continue
        
        # Color filter
        if color:
            colors = fabric.get("colors", [])
            if not any(color.lower() in c.lower() for c in colors):
                continue
        
        # Category filter
        if category:
            categories = fabric.get("dress_category", [])
            if not any(category.lower() in c.lower() for c in categories):
                continue
        
        # Season filter
        if season:
            seasons = fabric.get("season", [])
            if not any(season.lower() in s.lower() for s in seasons):
                continue
        
        # Pattern filter
        if pattern:
            patterns = fabric.get("pattern", [])
            if not any(pattern.lower() in p.lower() for p in patterns):
                continue
        
        # Text query
        if query:
            searchable = f"{fabric.get('name', '')} {fabric.get('description', '')}".lower()
            if query not in searchable:
                continue
        
        results.append(fabric)
    
    logger.info(
        "Mock search_fabrics filters=%s results=%d",
        {k: v for k, v in filters.items() if v},
        len(results)
    )
    
    return results[:limit]


def get_mock_fabric_details(catalog_code: str) -> dict[str, Any] | None:
    """Get fabric by catalog_code from mock data."""
    fabrics = _MOCK_DATA.get("fabrics", [])
    for fabric in fabrics:
        if fabric.get("catalog_code") == catalog_code or fabric.get("fabric_id") == catalog_code:
            return fabric
    return None


def list_mock_size_charts() -> list[dict[str, Any]]:
    """Live-shaped Suits chart only — do not invent Sherwani/Prince rows."""
    return [
        {
            "id": "8c69bd10-d537-4171-9b91-cbb0854e4f32",
            "name": "Standard Chart for Men's Suits",
            "category_id": "fcd721bb-a109-4432-9461-1324fd43490d",
            "category": {
                "id": "fcd721bb-a109-4432-9461-1324fd43490d",
                "name": "Suits",
                "slug": "suits-4975",
            },
            "category_name": "Suits",
            "description": None,
            "columns": ["Size", "Chest (in)", "Waist (in)", "Shoulder (in)", "Sleeve (in)", "Length (in)"],
            "rows": [
                {"Size": "36R", "Chest (in)": "36", "Waist (in)": "30", "Length (in)": "29.5", "Sleeve (in)": "24.5", "Shoulder (in)": "17.5"},
                {"Size": "38R", "Chest (in)": "38", "Waist (in)": "32", "Length (in)": "30.0", "Sleeve (in)": "25.0", "Shoulder (in)": "18.0"},
                {"Size": "40R", "Chest (in)": "40", "Waist (in)": "34", "Length (in)": "30.5", "Sleeve (in)": "25.5", "Shoulder (in)": "18.5"},
                {"Size": "42R", "Chest (in)": "42", "Waist (in)": "36", "Length (in)": "31.0", "Sleeve (in)": "26.0", "Shoulder (in)": "19.0"},
                {"Size": "44R", "Chest (in)": "44", "Waist (in)": "38", "Length (in)": "31.5", "Sleeve (in)": "26.5", "Shoulder (in)": "19.5"},
            ],
            "how_to_measure": [
                {"part": "Chest", "instruction": "Measure around the fullest part of your chest, keeping the tape horizontal under arms."},
                {"part": "Waist", "instruction": "Measure around your natural waistline, where your trousers usually sit comfortably."},
                {"part": "Shoulder", "instruction": "Measure across the back from the tip of one shoulder point to the other."},
                {"part": "Sleeve Length", "instruction": "Measure from shoulder seam down the outer arm to the wrist bone."},
                {"part": "Jacket Length", "instruction": "Measure down the center back from base of collar to desired bottom hem."},
            ],
            "is_default": False,
        }
    ]


def list_mock_categories() -> list[dict[str, Any]]:
    """Unique product categories from mock catalog, with counts."""
    _reload_mock_data()
    counts: dict[str, int] = {}
    for product in _MOCK_DATA.get("products") or []:
        name = str(product.get("category") or "").strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return [
        {"id": None, "name": name, "slug": name.lower().replace(" ", "-"), "product_count": count}
        for name, count in sorted(counts.items())
    ]


def check_mock_inventory(product_id: str, size: str | None, color: str | None, quantity: int = 1) -> dict[str, Any]:
    """Mock inventory check - always returns available for mock products."""
    product = get_mock_product_details(product_id)
    if not product:
        return {
            "available": False,
            "product_id": product_id,
            "message": "Product not found"
        }
    
    sizes = product.get("available_sizes", [])
    colors = product.get("available_colors", [])
    
    size_available = not size or size in sizes
    color_available = not color or any(color.lower() in c.lower() for c in colors)
    
    return {
        "available": size_available and color_available,
        "product_id": product_id,
        "size": size,
        "color": color,
        "requested_quantity": quantity,
        "available_quantity": 5 if size_available and color_available else 0,
        "stock_status": "in_stock" if size_available and color_available else "out_of_stock",
        "bespoke_available": product.get("is_bespoke_available", True),
        "message": f"{'Available' if size_available and color_available else 'Not available'} in requested configuration"
    }
