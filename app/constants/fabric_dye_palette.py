"""Canonical fabric dye / recolor palette for bespoke wedding image generation.

Curated for Sherwani / Prince Coat / Waistcoat (pastels + dark formal jewel tones
including greens grooms wear). Suit colours are included freely.

Frontend should fetch GET /api/generate-wedding-image/dye-colors rather than
hardcoding a divergent list.
"""
from __future__ import annotations

FABRIC_DYE_COLORS: tuple[str, ...] = (
    # Soft / pastel — Nikkah, day events, light sherwani & waistcoat
    "Ivory",
    "Cream",
    "Champagne",
    "Pearl",
    "Soft Beige",
    "Blush Pink",
    "Soft Peach",
    "Powder Blue",
    "Sky Blue",
    "Lavender",
    "Mint",
    "Sage",
    # Classic lights & midtones (suits + formal)
    "White",
    "Silver",
    "Grey",
    "Gold",
    "Sand",
    # Dark formal — Barat / evening / prince coat & sherwani
    "Black",
    "Charcoal",
    "Navy",
    "Midnight Blue",
    "Royal Blue",
    "Maroon",
    "Burgundy",
    "Wine",
    # Greens grooms commonly wear
    "Bottle Green",
    "Emerald",
    "Forest Green",
    "Teal",
    "Olive",
)

# Lowercased alias → canonical label (must be one of FABRIC_DYE_COLORS).
_ALIASES: dict[str, str] = {
    "midnight blue": "Midnight Blue",
    "navy blue": "Navy",
    "navy": "Navy",
    "royal blue": "Royal Blue",
    "bottle green": "Bottle Green",
    "emerald green": "Emerald",
    "emerald": "Emerald",
    "forest green": "Forest Green",
    "deep green": "Forest Green",
    "mehendi green": "Bottle Green",
    "mehndi green": "Bottle Green",
    "off white": "Ivory",
    "off-white": "Ivory",
    "pearl white": "Pearl",
    "pearl grey": "Pearl",
    "pearl gray": "Pearl",
    "soft beige": "Soft Beige",
    "light beige": "Soft Beige",
    "beige": "Soft Beige",
    "blush": "Blush Pink",
    "blush pink": "Blush Pink",
    "pastel pink": "Blush Pink",
    "peach": "Soft Peach",
    "soft peach": "Soft Peach",
    "powder blue": "Powder Blue",
    "pastel blue": "Powder Blue",
    "sky blue": "Sky Blue",
    "light blue": "Powder Blue",
    "lavender": "Lavender",
    "lilac": "Lavender",
    "soft lavender": "Lavender",
    "mint": "Mint",
    "mint green": "Mint",
    "sage": "Sage",
    "sage green": "Sage",
    "gray": "Grey",
    "grey": "Grey",
    "golden": "Gold",
    "sand": "Sand",
    "wine red": "Wine",
    "deep maroon": "Maroon",
    "deep burgundy": "Burgundy",
    "teal": "Teal",
    "deep teal": "Teal",
    "olive": "Olive",
    "olive green": "Olive",
}

_CANONICAL_BY_LOWER: dict[str, str] = {c.lower(): c for c in FABRIC_DYE_COLORS}


def normalize_fabric_dye_color(raw: str | None) -> str | None:
    """Strip and map to a canonical palette label, or None if blank/unknown."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    key = text.lower()
    if key in _ALIASES:
        return _ALIASES[key]
    if key in _CANONICAL_BY_LOWER:
        return _CANONICAL_BY_LOWER[key]
    # Title-case fallback only when it already matches a palette entry.
    titled = " ".join(part.capitalize() for part in text.split())
    if titled in FABRIC_DYE_COLORS:
        return titled
    return None


def is_valid_fabric_dye_color(value: str) -> bool:
    """True when value normalizes to a known palette colour."""
    return normalize_fabric_dye_color(value) is not None
