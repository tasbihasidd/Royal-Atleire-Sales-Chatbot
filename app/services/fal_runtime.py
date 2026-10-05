"""Shared fal.ai subscribe helper. One FAL_KEY for every model."""

from __future__ import annotations

import asyncio
import os
from contextvars import ContextVar
from typing import Any

from langsmith import traceable

from app.config import settings
from app.core.payload_log import print_payload
from app.services.fal_pricing import resolve_fal_call_usd
from app.services.cost_tracking import get_session_id, is_image_like_label, record_image_usage

_last_fal_request_id: ContextVar[str | None] = ContextVar("fal_last_request_id", default=None)


def get_last_fal_request_id() -> str | None:
    return _last_fal_request_id.get()


def _ensure_fal_key() -> str:
    key = (settings.FAL_KEY or os.getenv("FAL_KEY") or "").strip()
    if not key:
        raise RuntimeError("FAL_KEY is missing. Set it in .env — OpenRouter is no longer used.")
    os.environ["FAL_KEY"] = key
    return key


def _attach_run_metadata(model_id: str, label: str, estimated_cost: float) -> None:
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        if run is None:
            return
        extra = dict(getattr(run, "extra", None) or {})
        meta = dict(extra.get("metadata") or {})
        meta.update(
            {
                "model_id": model_id,
                "label": label,
                "session_id": get_session_id(),
                "estimated_cost_usd": estimated_cost,
            }
        )
        extra["metadata"] = meta
        run.extra = extra
    except Exception:
        pass


@traceable(name="fal.subscribe", run_type="tool")
def subscribe(model_id: str, arguments: dict[str, Any], *, label: str) -> Any:
    import fal_client

    _ensure_fal_key()
    request_id_holder: list[str | None] = [None]

    def _on_enqueue(request_id: str) -> None:
        request_id_holder[0] = request_id
        _last_fal_request_id.set(request_id)

    print_payload(f"FAL REQUEST {label} model={model_id}", arguments)
    result = fal_client.subscribe(
        model_id,
        arguments=arguments,
        on_enqueue=_on_enqueue,
    )
    print_payload(f"FAL RESPONSE {label} model={model_id}", result)

    image_like = is_image_like_label(label, model_id)
    estimated_cost = 0.0
    if image_like:
        estimated_cost, _source = resolve_fal_call_usd(
            endpoint_id=model_id,
            result=result,
            request_id=request_id_holder[0],
            label=label,
            image_like=True,
        )
        record_image_usage(
            label=label,
            model_id=model_id,
            session_id=get_session_id(),
            cost_usd=estimated_cost,
        )
    _attach_run_metadata(model_id, label, estimated_cost)
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
