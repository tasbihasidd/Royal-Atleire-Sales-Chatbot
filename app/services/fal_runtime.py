"""Shared fal.ai subscribe helper. One FAL_KEY for every model."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from app.config import settings
from app.core.payload_log import print_payload


def _ensure_fal_key() -> str:
    key = (settings.FAL_KEY or os.getenv("FAL_KEY") or "").strip()
    if not key:
        raise RuntimeError("FAL_KEY is missing. Set it in .env — OpenRouter is no longer used.")
    os.environ["FAL_KEY"] = key
    return key


def subscribe(model_id: str, arguments: dict[str, Any], *, label: str) -> Any:
    import fal_client

    _ensure_fal_key()
    print_payload(f"FAL REQUEST {label} model={model_id}", arguments)
    result = fal_client.subscribe(model_id, arguments=arguments)
    print_payload(f"FAL RESPONSE {label} model={model_id}", result)
    return result


async def asubscribe(model_id: str, arguments: dict[str, Any], *, label: str) -> Any:
    return await asyncio.to_thread(subscribe, model_id, arguments, label=label)


def upload_image_bytes(data: bytes, *, content_type: str = "image/jpeg", file_name: str = "upload.jpg") -> str:
    """Upload raw image bytes to fal CDN; returns a public URL fal models can fetch."""
    import fal_client

    _ensure_fal_key()
    # fal_client.upload(data, content_type) — preferred for in-memory bytes
    try:
        url = fal_client.upload(data, content_type)
    except TypeError:
        # Older clients: upload_file needs a path
        import tempfile
        from pathlib import Path

        suffix = Path(file_name).suffix or ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            url = fal_client.upload_file(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    if not url:
        raise RuntimeError("fal upload returned empty url")
    print_payload("FAL UPLOAD", {"content_type": content_type, "bytes": len(data), "url": url})
    return str(url)
