"""Prince Coat / garment cut prompt locks for bespoke image gen."""

from __future__ import annotations

from app.services.garment_silhouette import garment_silhouette_guidance, infer_garment_cut


def test_infer_prince_coat_from_instructions():
    assert (
        infer_garment_cut(None, "minimalistic prince coat on plain white shalwar kameez")
        == "prince_coat"
    )
    assert infer_garment_cut("Prince Coat", "silver grey fabric") == "prince_coat"


def test_user_brief_suit_beats_sherwani_category():
    assert (
        infer_garment_cut("Sherwani", "Black 2 piece suit with white shirt") == "suit"
    )


def test_prince_coat_guidance_blocks_winter_coat():
    text = garment_silhouette_guidance(
        "Prince Coat",
        "minimalistic prince coat made using this fabric on plain white shalwar kameez",
    )
    low = text.lower()
    assert "mandarin" in low or "nehru" in low or "bandhgala" in low
    assert "lapels" in low  # forbidden
    assert "winter" in low or "overcoat" in low
    assert "hip-length" in low or "hips" in low
    assert "shalwar" in low


def test_minimal_does_not_force_suit_lapels():
    text = garment_silhouette_guidance("Sherwani", "minimal sherwani")
    assert "sherwani" in text.lower()
    assert "overcoat" in text.lower()
