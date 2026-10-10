"""Virtual try-on via fal.ai — fixed to Bria FIBO-Edit 1.5."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import settings
from app.services.fal_image import _first_image_url
from app.services.fal_runtime import subscribe

BRIA_TRYON_MODEL = "bria/fibo-edit-1.5/virtual-try-on"

# Kept for reference / future swap; runtime always uses FAL_TRYON_MODEL (defaults to Bria).
KNOWN_TRYON_MODELS: dict[str, dict[str, Any]] = {
    BRIA_TRYON_MODEL: {
        "label": "Bria FIBO-Edit 1.5 Virtual Try-On",
        "person_key": "person_image_url",
        "garment_key": "garment_image_urls",
        "garment_as_list": True,
        "notes": "person_image_url + garment_image_urls[list].",
    },
}


def build_tryon_arguments(
    *,
    model: str,
    person_image_url: str,
    garment_image_url: str,
) -> dict[str, Any]:
    """Map image_url / product_url onto the Bria (or known) fal schema."""
    meta = KNOWN_TRYON_MODELS.get(model)
    if meta:
        garment_value: Any = (
            [garment_image_url] if meta.get("garment_as_list") else garment_image_url
        )
        return {
            str(meta["person_key"]): person_image_url,
            str(meta["garment_key"]): garment_value,
        }

    # Fallback for an unexpected FAL_TRYON_MODEL override.
    if "bria" in model or "fibo" in model:
        return {
            "person_image_url": person_image_url,
            "garment_image_urls": [garment_image_url],
        }
    return {
        "person_image_url": person_image_url,
        "garment_image_urls": [garment_image_url],
        "model_image": person_image_url,
        "garment_image": garment_image_url,
    }


def run_virtual_tryon(
    *,
    person_image_url: str,
    garment_image_url: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Sync helper. Prefer ``run_virtual_tryon_async`` inside FastAPI handlers."""
    model_id = (model or settings.FAL_TRYON_MODEL or BRIA_TRYON_MODEL).strip()
    if not model_id:
        model_id = BRIA_TRYON_MODEL

    arguments = build_tryon_arguments(
        model=model_id,
        person_image_url=person_image_url,
        garment_image_url=garment_image_url,
    )
    result = subscribe(model_id, arguments, label="virtual-tryon")
    fal_url = _first_image_url(result)
    if not fal_url:
        raise RuntimeError(f"fal try-on returned no image url: {type(result).__name__}")

    with httpx.Client(timeout=90.0) as client:
        response = client.get(fal_url)
        response.raise_for_status()
        image_bytes = response.content

    return {
        "model": model_id,
        "fal_image_url": fal_url,
        "image_bytes": image_bytes,
    }


async def run_virtual_tryon_async(
    *,
    person_image_url: str,
    garment_image_url: str,
    model: str | None = None,
) -> dict[str, Any]:
    model_id = (model or settings.FAL_TRYON_MODEL or BRIA_TRYON_MODEL).strip()
    if not model_id:
        model_id = BRIA_TRYON_MODEL

    arguments = build_tryon_arguments(
        model=model_id,
        person_image_url=person_image_url,
        garment_image_url=garment_image_url,
    )
    result = await asyncio.to_thread(
        lambda: subscribe(model_id, arguments, label="virtual-tryon")
    )
    fal_url = _first_image_url(result)
    if not fal_url:
        raise RuntimeError(f"fal try-on returned no image url: {type(result).__name__}")

    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.get(fal_url)
        response.raise_for_status()
        image_bytes = response.content

    return {
        "model": model_id,
        "fal_image_url": fal_url,
        "image_bytes": image_bytes,
    }
