from __future__ import annotations

"""
Shared helpers used by image generation prompts only.
Chatbot styling advice is NOT hardcoded here — the LLM explains style from product data.
"""

from datetime import date, time as time_type


TIME_BASED_COLORS = {
    "Morning": ["White", "Ivory", "Cream", "Light Beige", "Champagne", "Pastel Blue"],
    "Afternoon": ["Gold", "Olive", "Emerald", "Sand", "Sage", "Peach"],
    "Sunset": ["Maroon", "Burgundy", "Rust", "Copper", "Navy", "Bottle Green"],
    "Evening": ["Black", "Midnight Blue", "Charcoal", "Wine", "Royal Blue", "Deep Emerald"],
}

SEASONAL_RECOMMENDATIONS = {
    "Spring": {
        "fabrics": ["Light Silk", "Cotton Silk", "Organza"],
        "colors": ["Ivory", "Sage", "Mint", "Sky Blue"],
        "details": ["Floral embroidery", "Soft pastel styling"],
    },
    "Summer": {
        "fabrics": ["Cotton Silk", "Linen Blend", "Chiffon Dupatta", "Lightweight Sherwani"],
        "colors": ["White", "Beige", "Ice Blue", "Powder Pink"],
        "details": ["Breathable fabric", "Lightweight construction"],
    },
    "Autumn": {
        "fabrics": ["Raw Silk", "Jamawar", "Jacquard"],
        "colors": ["Rust", "Mustard", "Olive", "Maroon"],
        "details": ["Rich texture", "Warm color palette"],
    },
    "Winter": {
        "fabrics": ["Velvet", "Wool Blend", "Heavy Silk", "Cashmere Shawl"],
        "colors": ["Navy", "Black", "Emerald", "Wine", "Gold"],
        "details": ["Layered luxury look", "Warm premium fabric"],
    },
}


def get_time_period(wedding_time: time_type) -> str:
    hour = wedding_time.hour
    if 5 <= hour < 12:
        return "Morning"
    if 12 <= hour < 17:
        return "Afternoon"
    if 17 <= hour < 19:
        return "Sunset"
    return "Evening"


def get_season(wedding_date: date) -> str:
    month = wedding_date.month
    if month in [3, 4, 5]:
        return "Spring"
    if month in [6, 7, 8]:
        return "Summer"
    if month in [9, 10, 11]:
        return "Autumn"
    return "Winter"


def derive_season_from_text(wedding_date: str | None) -> str | None:
    if not wedding_date:
        return None
    text = wedding_date.lower()
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    for key, month in month_map.items():
        if key in text:
            return get_season(date(2026, month, 15)).lower()
    for season in ("winter", "summer", "autumn", "spring", "fall"):
        if season in text:
            return "autumn" if season == "fall" else season
    return None


# Live catalogue season labels (fabrics + products search APIs).
_CATALOG_SEASON_BY_KEY = {
    "summer": "Spring / Summer",
    "spring": "Spring / Summer",
    "winter": "Autumn / Winter",
    "autumn": "Autumn / Winter",
    "fall": "Autumn / Winter",
    "all-season": "All Season",
    "all season": "All Season",
    "allseason": "All Season",
}


def map_season_for_catalog_api(season: str | None) -> str | None:
    """
    Map agent season words (summer/winter/…) to catalogue API labels
    (e.g. 'Spring / Summer'). Pass through values that already look like API labels.
    """
    if not season:
        return None
    raw = str(season).strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower in _CATALOG_SEASON_BY_KEY:
        return _CATALOG_SEASON_BY_KEY[lower]
    # Already an API-style label
    if "/" in raw or lower == "all season":
        # Normalize casing of known labels
        for label in _CATALOG_SEASON_BY_KEY.values():
            if label.lower() == lower:
                return label
        return raw
    return _CATALOG_SEASON_BY_KEY.get(lower) or raw


def body_type_recommendation(body_type: str) -> str:
    rules = {
        "Slim": "structured shoulders, slightly layered silhouette, medium embroidery, richer fabric",
        "Athletic": "tailored fit, clean shoulder line, fitted waist, premium structured fabric",
        "Broad Shoulders": "balanced collar, minimal shoulder padding, longer vertical lines",
        "Heavy Build": "regular tailored fit, darker tones, vertical pattern, lightweight structure",
        "Tall": "balanced length, detailed cuffs, textured fabric, statement collar",
        "Short": "shorter jacket length, monochrome palette, vertical detailing, slim collar",
        "Plus Size": "regular fit, soft structured fabric, darker elegant tones, minimal bulk",
    }
    return rules.get(body_type, "balanced tailored fit")


def skin_tone_recommendation(skin_tone: str) -> str:
    rules = {
        "Fair": "ivory, navy, emerald, burgundy, champagne",
        "Light": "royal blue, sage, maroon, beige, gold",
        "Wheatish": "bottle green, rust, ivory, navy, copper",
        "Olive": "cream, wine, deep emerald, bronze, charcoal",
        "Tan": "white, beige, royal blue, mustard, maroon",
        "Brown": "gold, ivory, emerald, navy, wine",
        "Dark": "cream, champagne, royal blue, silver, emerald",
    }
    for key, value in rules.items():
        if key.lower() == (skin_tone or "").lower():
            return value
    return "balanced elegant colors"
