"""POST /api/virtual-try-on — only image_url + product_url (Bria FIBO try-on)."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl

from app.config import settings
from app.services.fal_runtime import upload_image_bytes
from app.services.virtual_tryon import BRIA_TRYON_MODEL, run_virtual_tryon

logger = logging.getLogger(__name__)

router = APIRouter(tags=["virtual-try-on"])

GENERATED_DIR = Path("static/generated")
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TurabeesTryOn/1.0; +https://turabees.com) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


class VirtualTryOnRequest(BaseModel):
    image_url: HttpUrl = Field(
        ...,
        description="Person photo — direct image URL (.jpg / .png / .webp)",
        examples=["https://labs-assets.bria.ai/fal-examples/fibo-edit-1.5/virtual-try-on/person.jpg"],
    )
    product_url: HttpUrl = Field(
        ...,
        description="Product / garment photo — direct image URL (.jpg / .png / .webp)",
        examples=["https://labs-assets.bria.ai/fal-examples/fibo-edit-1.5/virtual-try-on/garment-1.jpg"],
    )


class VirtualTryOnResponse(BaseModel):
    success: bool = True
    model: str
    image_url: str
    fal_image_url: str | None = None


def _looks_like_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _IMAGE_EXT)


def _is_private_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return True
    if host.startswith("192.168.") or host.startswith("10."):
        return True
    # 172.16.0.0 – 172.31.255.255
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
            if 16 <= second <= 31:
                return True
        except (IndexError, ValueError):
            pass
    return False


def _body_looks_like_image(data: bytes) -> bool:
    if not data or len(data) < 4:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    return False


def _guess_content_type(url: str, body: bytes, header_ctype: str) -> str:
    if header_ctype.startswith("image/"):
        return header_ctype
    if body[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    path = urlparse(url).path.lower()
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


async def _fetch_image_bytes(url: str, *, label: str) -> tuple[bytes, str]:
    """
    Download a remote image for try-on.

    Returns (bytes, content_type). Raises HTTPException on failure.
    We re-upload to fal CDN so Bria can always fetch the file (avoids flaky origin hosts).
    """
    if _is_private_host(url):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{label} points at a private/LAN host ({urlparse(url).hostname}). "
                "Use a public HTTPS image URL (e.g. https://royal-attire-api.devssh.xyz/uploads/...)."
            ),
        )

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            # Full GET first — some hosts 404 / mis-label Range or HEAD.
            resp = await client.get(url, headers=_FETCH_HEADERS)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{label} URL could not be fetched. "
                f"Open the link in a browser — it must download a raw jpeg/png/webp. Error: {exc}"
            ),
        ) from exc

    if resp.status_code == 404:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{label} returned HTTP 404 — file not found at that URL. "
                "Open the same link in your browser. If it 404s there too, re-upload the photo "
                "and paste the new public /uploads/... URL."
            ),
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail=f"{label} returned HTTP {resp.status_code}. Use a public direct image URL.",
        )

    body = resp.content or b""
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()

    if not (_body_looks_like_image(body) or ctype.startswith("image/")):
        if _looks_like_image_url(url) and ctype in ("", "application/octet-stream", "binary/octet-stream"):
            pass  # trust extension + opaque bytes
        else:
            snippet = ""
            if ctype == "application/json" or body[:1] == b"{":
                snippet = body[:180].decode("utf-8", errors="replace")
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{label} is not a direct image URL (content-type={ctype or 'unknown'}, "
                    f"bytes={len(body)}). Paste a URL that opens the raw .jpg/.png/.webp. "
                    + (f"Response starts with: {snippet}" if snippet else "")
                ),
            )

    if len(body) < 100:
        raise HTTPException(
            status_code=400,
            detail=f"{label} response too small ({len(body)} bytes) — not a usable image.",
        )

    return body, _guess_content_type(url, body, ctype)


def _friendly_tryon_error(exc: BaseException) -> tuple[int, str | dict]:
    from app.services.fal_errors import FalProviderError, classify_fal_failure

    if isinstance(exc, FalProviderError):
        return exc.http_status, exc.to_detail()
    text = str(exc)
    if re.search(r"unsupported format|valid JPEG|PNG|GIF|WebP", text, re.I):
        return 400, (
            "Image rejected: need direct JPEG/PNG/GIF/WebP file URLs for image_url and product_url."
        )
    if "FAL_KEY" in text:
        return 500, "FAL_KEY is missing."
    # fal_client HTTP errors → structured quota / rate-limit detail
    try:
        from fal_client.client import FalClientError, FalClientHTTPError

        if isinstance(exc, (FalClientHTTPError, FalClientError)):
            mapped = classify_fal_failure(exc)
            return mapped.http_status, mapped.to_detail()
    except ImportError:
        pass
    if re.search(r"quota|credit|billing|rate.?limit|too many requests", text, re.I):
        mapped = classify_fal_failure(exc)
        return mapped.http_status, mapped.to_detail()
    return 502, f"Try-on failed: {text[:300]}"


@router.post("/api/virtual-try-on", response_model=VirtualTryOnResponse)
async def virtual_tryon(payload: VirtualTryOnRequest, request: Request):
    """
    Virtual try-on with Bria FIBO-Edit 1.5.
    Body: only `image_url` (person) + `product_url` (garment/product image).

    Images are downloaded here and re-hosted on fal CDN so the try-on model
    does not depend on flaky origin content-types / private hosts.
    """
    if not settings.FAL_KEY:
        raise HTTPException(status_code=500, detail="FAL_KEY is missing.")

    person = str(payload.image_url)
    product = str(payload.product_url)

    person_bytes, person_ctype = await _fetch_image_bytes(person, label="image_url")
    product_bytes, product_ctype = await _fetch_image_bytes(product, label="product_url")

    try:
        person_fal = upload_image_bytes(
            person_bytes, content_type=person_ctype, file_name="person.jpg"
        )
        product_fal = upload_image_bytes(
            product_bytes, content_type=product_ctype, file_name="product.jpg"
        )
    except Exception as exc:
        logger.exception("virtual_tryon fal upload failed")
        raise HTTPException(status_code=502, detail=f"Failed to stage images for try-on: {exc}") from exc

    model_id = (settings.FAL_TRYON_MODEL or BRIA_TRYON_MODEL).strip()
    logger.info(
        "virtual_tryon start model=%s person_bytes=%s product_bytes=%s",
        model_id,
        len(person_bytes),
        len(product_bytes),
    )

    try:
        result = run_virtual_tryon(
            person_image_url=person_fal,
            garment_image_url=product_fal,
            model=model_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        status, detail = _friendly_tryon_error(exc)
        headers = None
        if isinstance(detail, dict) and detail.get("retry_after"):
            headers = {"Retry-After": str(detail["retry_after"])}
        logger.exception("virtual_tryon failed model=%s", model_id)
        raise HTTPException(status_code=status, detail=detail, headers=headers) from exc

    filename = f"{uuid.uuid4()}.png"
    (GENERATED_DIR / filename).write_bytes(result["image_bytes"])
    base_url = (settings.BASE_URL or str(request.base_url)).rstrip("/")
    out_url = f"{base_url}/static/generated/{filename}"

    logger.info("virtual_tryon ok model=%s image=%s", result["model"], filename)
    return VirtualTryOnResponse(
        success=True,
        model=result["model"],
        image_url=out_url,
        fal_image_url=result.get("fal_image_url"),
    )
