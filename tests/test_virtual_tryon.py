"""Virtual try-on argument mapping (no network)."""

from __future__ import annotations

from app.services.virtual_tryon import BRIA_TRYON_MODEL, build_tryon_arguments


def test_bria_args_from_image_and_product():
    args = build_tryon_arguments(
        model=BRIA_TRYON_MODEL,
        person_image_url="https://example.com/person.jpg",
        garment_image_url="https://example.com/product.jpg",
    )
    assert args == {
        "person_image_url": "https://example.com/person.jpg",
        "garment_image_urls": ["https://example.com/product.jpg"],
    }
