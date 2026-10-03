"""Slim wedding image request + prompt builder (no network)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.routes.image_generation import WeddingImageRequest, build_wedding_prompt, _slim_fabric_row


def test_slim_request_fields_only():
    fields = set(WeddingImageRequest.model_fields)
    assert "prompt" in fields
    assert "dress_category" in fields or "category" in fields
    assert "fabric_catalog_code" in fields
    assert "religion" not in fields
    assert "styles" not in fields
    assert "wedding_date" not in fields


def test_slim_request_requires_prompt_category_fabric():
    with pytest.raises(ValidationError):
        WeddingImageRequest(prompt="nice sherwani")
    with pytest.raises(ValidationError):
        WeddingImageRequest(prompt="nice", dress_category="Sherwani")
    ok = WeddingImageRequest(
        prompt="Ivory sherwani, light embroidery",
        category="Sherwani",
        fabric_catalog_code="FAB-1",
    )
    assert ok.dress_category == "Sherwani"
    assert ok.prompt.startswith("Ivory")


def test_build_prompt_includes_user_brief():
    req = WeddingImageRequest(
        prompt="Minimal bandhgala, soft gold thread only on collar",
        dress_category="Sherwani",
        fabric_catalog_code="FAB-9",
    )
    text = build_wedding_prompt(
        req,
        {
            "fabric_type_guess": "raw silk",
            "dominant_colors": ["ivory"],
            "secondary_colors": [],
            "pattern": "plain",
            "embroidery_style": "light",
            "texture": "smooth",
            "visual_weight": "light",
            "luxury_level": "high",
            "style_notes": "clean",
        },
        fabric_name="Ivory Silk",
        has_fabric_image=True,
    )
    assert "Minimal bandhgala" in text
    assert "Sherwani" in text
    assert "Ivory Silk" in text
    assert "maroon" in text.lower()


def test_slim_fabric_row():
    item = _slim_fabric_row(
        {"catalog_code": "C1", "name": "Silk", "image_url": "https://x/y.jpg", "dress_category": ["Sherwani"]}
    )
    assert item is not None
    assert item.catalog_code == "C1"
    assert _slim_fabric_row({"name": "no-code"}) is None
