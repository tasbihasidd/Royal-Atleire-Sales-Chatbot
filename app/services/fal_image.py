"""Seedream 5.0 Lite image generation via fal.ai."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.services.fal_runtime import subscribe


def generate_image_bytes_fal(
    prompt: str,
    *,
    image_urls: list[str] | None = None,
    image_size: str = "portrait_16_9",
) -> bytes:
    urls = [u for u in (image_urls or []) if u]
    if urls:
        model_id = settings.FAL_IMAGE_MODEL
        arguments: dict[str, Any] = {
            "prompt": prompt,
            "image_urls": urls,
            "image_size": image_size,
            "num_images": 1,
            "enable_safety_checker": True,
        }
        label = "seedream-edit"
    else:
        model_id = settings.FAL_IMAGE_T2I_MODEL
        arguments = {
            "prompt": prompt,
            "image_size": image_size,
            "num_images": 1,
            "enable_safety_checker": True,
        }
        label = "seedream-t2i"

    result = subscribe(model_id, arguments, label=label)
    image_url = _first_image_url(result)
    if not image_url:
        raise RuntimeError(f"fal image generation returned no image url: {result!r}")
    with httpx.Client(timeout=60.0) as client:
        response = client.get(image_url)
        response.raise_for_status()
        return response.content


def _first_image_url(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    images = result.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
        if isinstance(first, str):
            return first
    if result.get("image") and isinstance(result["image"], dict):
        return result["image"].get("url")
    return result.get("url")
