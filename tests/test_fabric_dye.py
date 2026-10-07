"""Fabric dye / recolor palette, request validation, and prompt branching."""
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.constants.fabric_dye_palette import (
    FABRIC_DYE_COLORS,
    is_valid_fabric_dye_color,
    normalize_fabric_dye_color,
)
from app.routes.image_generation import (
    WeddingImageRequest,
    build_wedding_prompt,
    list_wedding_image_dye_colors,
)
from app.services.fabric_recolor_prompt import swatch_tail_append


_FABRIC_ANALYSIS = {
    "fabric_type_guess": "Jacquard",
    "dominant_colors": ["Silver", "Grey"],
    "secondary_colors": [],
    "pattern": "Intricate",
    "embroidery_style": "Light",
    "texture": "Smooth",
    "visual_weight": "Medium",
    "luxury_level": "High",
    "style_notes": "",
}


def test_normalize_fabric_dye_color_aliases():
    assert normalize_fabric_dye_color("  maroon  ") == "Maroon"
    assert normalize_fabric_dye_color("midnight blue") == "Midnight Blue"
    assert normalize_fabric_dye_color("NAVY BLUE") == "Navy"
    assert normalize_fabric_dye_color("powder blue") == "Powder Blue"
    assert normalize_fabric_dye_color("blush") == "Blush Pink"
    assert normalize_fabric_dye_color("mehndi green") == "Bottle Green"
    assert normalize_fabric_dye_color("forest green") == "Forest Green"
    assert normalize_fabric_dye_color("") is None
    assert normalize_fabric_dye_color(None) is None
    assert normalize_fabric_dye_color("Neon Pink") is None
    assert is_valid_fabric_dye_color("Ivory")
    assert is_valid_fabric_dye_color("Mint")
    assert not is_valid_fabric_dye_color("Neon Pink")


def test_wedding_request_accepts_and_normalizes_dye_color():
    req = WeddingImageRequest(
        prompt="Clean bandhgala prince coat",
        dress_category="Prince Coat",
        fabric_catalog_code="7826001",
        fabric_dye_color="maroon",
    )
    assert req.fabric_dye_color == "Maroon"


def test_wedding_request_blank_dye_becomes_none():
    req = WeddingImageRequest(
        prompt="Classic sherwani",
        dress_category="Sherwani",
        fabric_catalog_code="FAB-1",
        fabric_dye_color="  ",
    )
    assert req.fabric_dye_color is None


def test_wedding_request_rejects_invalid_dye_color():
    with pytest.raises(ValidationError):
        WeddingImageRequest(
            prompt="Classic sherwani",
            dress_category="Sherwani",
            fabric_catalog_code="FAB-1",
            fabric_dye_color="Neon Pink",
        )


def test_build_wedding_prompt_with_dye_recolors_not_exact_hue():
    req = WeddingImageRequest(
        prompt="Clean bandhgala prince coat",
        dress_category="Prince Coat",
        fabric_catalog_code="7826001",
        fabric_dye_color="Maroon",
    )
    prompt = build_wedding_prompt(
        req,
        _FABRIC_ANALYSIS,
        fabric_name="Silver Grey Jacquard",
        has_fabric_image=True,
    )
    assert "Maroon" in prompt
    assert "DYE" in prompt.upper() or "recolor" in prompt.lower() or "RECOLOR" in prompt
    assert "same fabric colour" not in prompt.lower()
    assert "exact fabric colour" not in prompt.lower()
    assert "ignore for final garment hue" in prompt.lower()
    assert "FABRIC SURFACE LOCK" in prompt
    assert "Do NOT invent" in prompt or "do not invent" in prompt.lower()


def test_build_wedding_prompt_fabric_surface_lock_even_without_dye():
    req = WeddingImageRequest(
        prompt="Sherwani from this fabric",
        dress_category="Sherwani",
        fabric_catalog_code="7826006",
        embroidery_tier="Light",
    )
    prompt = build_wedding_prompt(
        req,
        {
            **_FABRIC_ANALYSIS,
            "pattern": "Embossed geometric grid",
            "embroidery_style": "none",
        },
        fabric_name="Silver Grey Embossed Geometric",
        has_fabric_image=True,
    )
    assert "FABRIC SURFACE LOCK" in prompt
    assert "Embroidery density: Light" in prompt


def test_build_wedding_prompt_without_dye_keeps_exact_colour():
    req = WeddingImageRequest(
        prompt="Clean bandhgala prince coat",
        dress_category="Prince Coat",
        fabric_catalog_code="7826001",
    )
    prompt = build_wedding_prompt(
        req,
        _FABRIC_ANALYSIS,
        fabric_name="Silver Grey Jacquard",
        has_fabric_image=True,
    )
    assert "same fabric colour" in prompt.lower()
    assert "fabric_dye_color" not in prompt.lower()


def test_swatch_tail_append_dye_vs_exact():
    exact = swatch_tail_append(dye_color=None)
    assert "exact fabric colour" in exact.lower()
    dyed = swatch_tail_append(dye_color="Maroon")
    assert "Maroon" in dyed
    assert "exact fabric colour" not in dyed.lower()
    assert "dye" in dyed.lower() or "recolor" in dyed.lower()


def test_dye_colors_endpoint():
    resp = asyncio.run(list_wedding_image_dye_colors())
    assert resp.count == len(FABRIC_DYE_COLORS)
    assert resp.colors == list(FABRIC_DYE_COLORS)
    assert "Maroon" in resp.colors
    assert "Midnight Blue" in resp.colors
    assert "Powder Blue" in resp.colors
    assert "Blush Pink" in resp.colors
    assert "Bottle Green" in resp.colors
    assert "Forest Green" in resp.colors
    assert "Mint" in resp.colors
