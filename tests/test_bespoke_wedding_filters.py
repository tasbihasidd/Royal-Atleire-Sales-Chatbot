"""Bespoke wedding-image filters: categories, embroidery tier, optional bride match."""
from __future__ import annotations

from app.routes.image_generation import WeddingImageRequest, build_wedding_prompt
from app.services.bride_match import build_groom_complement_brief


def test_bride_match_empty_fields_gives_generic_brief():
    result = build_groom_complement_brief()
    assert "complement" in result["brief"].lower() or "Complementary" in result["brief"]
    assert result["bride_color"] is None
    assert result["suggested_groom_palette"] is None


def test_bride_match_color_alone_complements_not_clones():
    result = build_groom_complement_brief(bride_color="Maroon")
    assert result["bride_color"] == "Maroon"
    palette = (result["suggested_groom_palette"] or "").lower()
    assert "maroon" not in palette or "avoid" in palette or "not" in palette
    assert "ivory" in palette or "cream" in palette or "champagne" in palette


def test_bride_match_jewelry_gold_alone():
    result = build_groom_complement_brief(bride_jewelry_tone="Gold")
    assert result["metal_accents"]
    assert "gold" in (result["metal_accents"] or "").lower()


def test_wedding_request_accepts_match_bride_with_no_bride_fields():
    req = WeddingImageRequest(
        prompt="Ivory prince coat with clean front",
        dress_category="Prince Coat",
        fabric_catalog_code="7826001",
        embroidery_tier="Light",
        match_bride=True,
    )
    assert req.match_bride is True
    assert req.bride_color is None
    assert req.embroidery_tier == "Light"


def test_wedding_request_blank_bride_strings_become_none():
    req = WeddingImageRequest(
        prompt="Classic sherwani",
        dress_category="Sherwani",
        fabric_catalog_code="FAB-1",
        match_bride=True,
        bride_color="  ",
        bride_fabric="",
    )
    assert req.bride_color is None
    assert req.bride_fabric is None


def test_build_wedding_prompt_includes_embroidery_and_bride():
    req = WeddingImageRequest(
        prompt="Clean bandhgala prince coat",
        dress_category="Prince Coat",
        fabric_catalog_code="7826001",
        embroidery_tier="Heavy",
        match_bride=True,
        bride_color="Red",
        bride_jewelry_tone="Gold",
    )
    bride = build_groom_complement_brief(
        bride_color=req.bride_color,
        bride_jewelry_tone=req.bride_jewelry_tone,
        groom_embroidery_tier=req.embroidery_tier,
    )
    prompt = build_wedding_prompt(
        req,
        {
            "fabric_type_guess": "Jacquard",
            "dominant_colors": ["Silver"],
            "secondary_colors": [],
            "pattern": "Intricate",
            "embroidery_style": "Light",
            "texture": "Smooth",
            "visual_weight": "Medium",
            "luxury_level": "High",
            "style_notes": "",
        },
        fabric_name="Silver Grey",
        has_fabric_image=True,
        bride_match=bride,
    )
    assert "Embroidery density: Heavy" in prompt
    assert "AI MATCH WITH BRIDE" in prompt
    assert "Red" in prompt or "red" in prompt.lower()


def test_categories_endpoint_filters_stocked(monkeypatch):
    import asyncio

    from app.routes import image_generation as ig

    async def fake_list():
        return [
            {"id": "1", "name": "Sherwani", "slug": "sherwani", "product_count": 23},
            {"id": "2", "name": "Empty Cat", "slug": "empty", "product_count": 0},
            {"id": "3", "name": "Prince coat", "slug": "prince-coat", "product_count": 6},
        ]

    monkeypatch.setattr(ig.backend_api, "list_categories", fake_list)
    resp = asyncio.run(ig.list_wedding_image_categories())
    names = [c.name for c in resp.categories]
    assert names == ["Sherwani", "Prince coat"]
    assert resp.count == 2
