from __future__ import annotations

import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.config import settings
from app.core.logging_config import safe_len
from app.services.backend_api import backend_api
from app.services.cost_tracking import session_cost_scope
from app.services.image_store import image_store
from app.services.quota_service import require_quota

logger = logging.getLogger(__name__)

router = APIRouter(tags=["image-generation"])

OPENROUTER_API_KEY = settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
OPENROUTER_BASE_URL = settings.OPENROUTER_BASE_URL
BASE_URL = settings.BASE_URL
FABRIC_ANALYSIS_MODEL = settings.FABRIC_ANALYSIS_MODEL
IMAGE_MODEL = settings.IMAGE_MODEL

STATIC_DIR = Path("static")
GENERATED_DIR = STATIC_DIR / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)


class WeddingImageRequest(BaseModel):
    """Slim generate body: prompt + category + system fabric (or optional swatch URL)."""

    prompt: str = Field(..., min_length=1, description="Free-text design brief for the outfit image")
    dress_category: Optional[str] = Field(
        default=None,
        description="Garment category e.g. Sherwani, Suits (alias: category)",
        examples=["Sherwani"],
    )
    category: Optional[str] = Field(
        default=None,
        description="Alias for dress_category",
        examples=["Sherwani"],
    )
    fabric_catalog_code: Optional[str] = Field(
        default=None,
        description="Fabric catalog_code from GET /api/generate-wedding-image/fabrics?category=...",
    )
    fabric_image_url: Optional[HttpUrl] = Field(
        default=None,
        description="Optional direct fabric swatch URL override (if not using catalog_code)",
    )
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    output_format: Literal["png", "jpeg", "webp"] = "png"

    # --- legacy fields (kept for reference — not accepted on this slim API) ---
    # religion: str
    # ceremony: str
    # wedding_date: date
    # wedding_time: time_type
    # location: str
    # venue_type: str
    # styles: List[str] = Field(default_factory=list)
    # colors: List[str] = Field(default_factory=list)
    # embroidery: List[str] = Field(default_factory=list)
    # patterns: List[str] = Field(default_factory=list)
    # fit: str
    # body_type: str
    # skin_tone: str
    # budget: str
    # delivery_timeline: str
    # accessories: List[str] = Field(default_factory=list)
    # footwear: str
    # wedding_theme: str
    # personal_preferences: List[str] = Field(default_factory=list)
    # match_bride: bool = False
    # bride_color: Optional[str] = None
    # bride_fabric: Optional[str] = None
    # bride_embroidery: Optional[str] = None
    # bride_jewelry_tone: Optional[str] = None
    # size: str = "1024x1536"
    # quality: Literal["low", "medium", "high", "auto"] = "medium"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "prompt": "Ivory sherwani with light gold zardozi on collar, clean bandhgala cut, matching shirt and formal shoes, maroon mannequin, minimal studio background",
                    "dress_category": "Sherwani",
                    "fabric_catalog_code": "FAB-001",
                    "output_format": "png",
                }
            ]
        }
    }

    @model_validator(mode="after")
    def _require_category_and_fabric(self) -> "WeddingImageRequest":
        resolved_category = (self.dress_category or self.category or "").strip()
        if not resolved_category:
            raise ValueError("dress_category (or category) is required.")
        self.dress_category = resolved_category
        if not self.fabric_catalog_code and not self.fabric_image_url:
            raise ValueError(
                "Provide fabric_catalog_code (from fabrics-by-category) or fabric_image_url."
            )
        return self


class ImageMetadata(BaseModel):
    title: str
    short_description: str
    long_description: str
    tags: List[str]
    colors: List[str]
    style: str
    occasion: str


class WeddingImageResponse(BaseModel):
    success: bool
    image_url: str
    prompt: str
    fabric_analysis: Dict[str, Any]
    image_record_id: int
    metadata: ImageMetadata
    fabric_catalog_code: Optional[str] = None
    dress_category: Optional[str] = None


class FabricListItem(BaseModel):
    catalog_code: str
    name: Optional[str] = None
    image_url: Optional[str] = None
    dress_category: Optional[Any] = None
    color: Optional[str] = None
    fabric_type: Optional[str] = None


class FabricListResponse(BaseModel):
    category: str
    count: int
    fabrics: List[FabricListItem]
    note: Optional[str] = None


def clean_json_text(text: str) -> str:
    text = text.strip()

    if text.startswith("```json"):
        text = text.replace("```json", "", 1).strip()

    if text.startswith("```"):
        text = text.replace("```", "", 1).strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    return text


def default_fabric_analysis() -> Dict[str, Any]:
    return {
        "fabric_type_guess": "Not provided",
        "dominant_colors": [],
        "secondary_colors": [],
        "pattern": "Not provided",
        "embroidery_style": "Not provided",
        "texture": "Not provided",
        "visual_weight": "Not provided",
        "luxury_level": "Not provided",
        "style_notes": "No fabric image uploaded. Use user-selected colors, styles, embroidery, and patterns.",
        "recommended_use": "Wedding menswear styling",
    }


async def image_url_to_data_url(image_url: str) -> str:
    logger.info("Fabric image download start")
    async with httpx.AsyncClient(timeout=40) as http_client:
        response = await http_client.get(image_url)
        response.raise_for_status()

    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    content_size = len(response.content)
    logger.info(
        "Fabric image download end content_type=%s size_bytes=%s",
        content_type,
        content_size,
    )

    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
    if content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid fabric image content type: {content_type}. Use png, jpg, jpeg, or webp.",
        )

    image_b64 = base64.b64encode(response.content).decode("utf-8")

    if content_type == "image/jpg":
        content_type = "image/jpeg"

    return f"data:{content_type};base64,{image_b64}"


async def analyze_fabric_image(fabric_image_url: str) -> Dict[str, Any]:
    logger.info("Fabric analysis start")
    from app.services.ai.fal import FalProvider

    prompt = """
Analyze this fabric image for luxury wedding menswear.

Return ONLY valid JSON in this exact structure:
{
  "fabric_type_guess": "",
  "dominant_colors": [],
  "secondary_colors": [],
  "pattern": "",
  "embroidery_style": "",
  "texture": "",
  "visual_weight": "",
  "luxury_level": "",
  "style_notes": "",
  "recommended_use": ""
}

Rules:
- Keep it short.
- Focus on fabric, color, embroidery, pattern and texture.
- Do not add markdown.
""".strip()
    raw_text = ""
    try:
        raw_text = await FalProvider().analyze_image(
            prompt,
            [fabric_image_url],
            system_prompt="Return ONLY valid JSON. No markdown.",
        )
        analysis = json.loads(clean_json_text(raw_text))
    except Exception:
        logger.exception("Fabric analysis via fal vision failed — using defaults")
        analysis = {
            "fabric_type_guess": "Unknown",
            "dominant_colors": [],
            "secondary_colors": [],
            "pattern": "Unknown",
            "embroidery_style": "Unknown",
            "texture": "Unknown",
            "visual_weight": "Unknown",
            "luxury_level": "Unknown",
            "style_notes": raw_text,
            "recommended_use": "Wedding menswear styling",
        }

    logger.info("Fabric analysis end")
    return analysis


_METADATA_SYSTEM_PROMPT = """
You are a luxury wedding menswear copywriter for a high-end UK boutique.
Given a groom outfit design description, return ONLY valid JSON with these exact fields:
{
  "title": "...",
  "short_description": "...",
  "long_description": "...",
  "tags": [],
  "colors": [],
  "style": "...",
  "occasion": "..."
}
Rules:
- title: 4-8 words, product-name style e.g. "Royal Ivory Sherwani for Walima"
- short_description: 1 sentence, 15-25 words, suitable for product cards
- long_description: 2-3 sentences, rich marketing copy for a product detail page
- tags: 4-8 keywords (dress category, style, ceremony, color, embroidery)
- colors: list of main colors only
- style: single style label e.g. "Royal", "Classic", "Modern", "Luxury"
- occasion: ceremony name from the description e.g. "Walima", "Nikah", "Barat"
- Return ONLY JSON. No markdown. No extra text.
""".strip()


async def generate_image_metadata(prompt: str, dress_category: str = "", ceremony: str = "") -> ImageMetadata:
    logger.info("Image metadata generation start prompt_length=%s", safe_len(prompt))
    from app.services.llm import acall_llm

    try:
        raw_text = await acall_llm(
            _METADATA_SYSTEM_PROMPT,
            prompt,
            json_mode=True,
            max_tokens=600,
        )
        data = json.loads(clean_json_text(raw_text))
        metadata = ImageMetadata(
            title=data.get("title") or f"{dress_category} for {ceremony}".strip(" for"),
            short_description=data.get("short_description") or "",
            long_description=data.get("long_description") or "",
            tags=data.get("tags") or [],
            colors=data.get("colors") or [],
            style=data.get("style") or "",
            occasion=data.get("occasion") or ceremony,
        )
        logger.info("Image metadata generation end title=%s", metadata.title)
        return metadata
    except Exception:
        logger.exception("Image metadata generation failed — using safe defaults")
        return ImageMetadata(
            title=f"{dress_category} for {ceremony}".strip(" for") or "Wedding Outfit",
            short_description="",
            long_description="",
            tags=[],
            colors=[],
            style="",
            occasion=ceremony,
        )


def build_wedding_prompt(
    data: WeddingImageRequest,
    fabric_analysis: Dict[str, Any],
    *,
    fabric_name: str | None = None,
    has_fabric_image: bool = False,
) -> str:
    """Compose fal prompt from user free-text + category + fabric analysis."""
    category = (data.dress_category or data.category or "Wedding menswear").strip()
    user_brief = (data.prompt or "").strip()

    if has_fabric_image:
        fabric_section = f"""
Fabric reference analysis:
- Catalog fabric name: {fabric_name or "Catalogue swatch"}
- Fabric type guess: {fabric_analysis.get("fabric_type_guess", "")}
- Dominant colors: {", ".join(fabric_analysis.get("dominant_colors", []) or [])}
- Secondary colors: {", ".join(fabric_analysis.get("secondary_colors", []) or [])}
- Pattern: {fabric_analysis.get("pattern", "")}
- Embroidery style: {fabric_analysis.get("embroidery_style", "")}
- Texture: {fabric_analysis.get("texture", "")}
- Visual weight: {fabric_analysis.get("visual_weight", "")}
- Luxury level: {fabric_analysis.get("luxury_level", "")}
- Style notes: {fabric_analysis.get("style_notes", "")}
"""
        design_instruction = """
Important design instruction:
Use the fabric swatch image as the main material reference.
The outfit must clearly reflect the same fabric colour, weave, texture, pattern mood, and embroidery language.
"""
    else:
        fabric_section = """
Fabric reference:
- No fabric swatch image was available.
- Follow the user's prompt and dress category only.
"""
        design_instruction = """
Important design instruction:
Create the outfit from the user prompt and dress category. Do not invent a named catalogue fabric.
"""

    return f"""
Create a high-end realistic fashion product photograph of a Turabees bespoke wedding outfit.

User design brief (follow closely):
{user_brief}

Outfit context:
- Dress category: {category}
{fabric_section}
{design_instruction}

Image requirements:
- Display the outfit on a maroon / deep burgundy dressmaker mannequin (NOT a human model).
- Minimal clean photography studio background — soft neutral seamless backdrop, even studio lighting.
- Full-length mannequin view from form to shoes — complete styled look, never cropped above the ankles.
- COMPLETE STYLING (mandatory): matching dress shirt under the jacket/sherwani; polished formal shoes (Oxfords/brogues/loafers) clearly visible — NEVER bare mannequin feet; for suits/tuxedos also a coordinated tie or bow tie and pocket square when the cut allows; trousers break cleanly over the shoes.
- Sharp photorealistic tailoring and fabric drape.
- No text, no watermark, no logos, no extra people.
- Commercially presentable for an online bespoke wedding menswear platform.
""".strip()


def generate_image_bytes(
    prompt: str,
    size: str,
    quality: str,
    output_format: str,
) -> bytes:
    from app.services.fal_image import generate_image_bytes_fal

    logger.info(
        "Image generation start via fal size=%s output_format=%s prompt_length=%s",
        size,
        output_format,
        safe_len(prompt),
    )
    image_bytes = generate_image_bytes_fal(prompt)
    logger.info("Image generation end output_size_bytes=%s", len(image_bytes))
    return image_bytes


def save_generated_image(image_bytes: bytes, output_format: str) -> str:
    file_id = str(uuid.uuid4())
    ext = "jpg" if output_format == "jpeg" else output_format
    filename = f"{file_id}.{ext}"
    file_path = GENERATED_DIR / filename
    with open(file_path, "wb") as f:
        f.write(image_bytes)
    return filename


def build_public_image_url(request: Request, filename: str) -> str:
    base_url = BASE_URL or str(request.base_url).rstrip("/")
    return f"{base_url}/static/generated/{filename}"


def _slim_fabric_row(row: dict[str, Any]) -> FabricListItem | None:
    code = str(row.get("catalog_code") or row.get("fabric_id") or "").strip()
    if not code:
        return None
    return FabricListItem(
        catalog_code=code,
        name=row.get("name"),
        image_url=row.get("image_url") or row.get("imageUrl"),
        dress_category=row.get("dress_category") or row.get("category"),
        color=row.get("color") or row.get("dominant_color"),
        fabric_type=row.get("fabric_type") or row.get("type"),
    )


@router.get("/api/generate-wedding-image/fabrics", response_model=FabricListResponse)
async def list_wedding_image_fabrics(
    category: str = Query(..., min_length=1, description="Dress category e.g. Sherwani, Suits"),
):
    """Return live catalogue fabrics for the chosen category (no inventing / no Suit dump)."""
    cat = category.strip()
    logger.info("wedding_image fabrics list start category=%s", cat)
    try:
        rows = await backend_api.search_fabrics(
            {"category": cat, "dress_category": cat, "limit": 100}
        )
    except Exception as exc:
        logger.exception("wedding_image fabrics list failed category=%s", cat)
        raise HTTPException(status_code=502, detail=f"Fabric catalogue unavailable: {exc}") from exc

    fabrics: list[FabricListItem] = []
    for row in rows or []:
        if isinstance(row, dict):
            item = _slim_fabric_row(row)
            if item:
                fabrics.append(item)

    note = None
    if not fabrics:
        note = (
            f"No fabric swatches found in catalogue for category '{cat}'. "
            "Do not substitute another category's fabrics."
        )
    logger.info("wedding_image fabrics list end category=%s count=%s", cat, len(fabrics))
    return FabricListResponse(category=cat, count=len(fabrics), fabrics=fabrics, note=note)


async def _resolve_fabric_swatch(
    payload: WeddingImageRequest,
) -> tuple[str | None, str | None, dict[str, Any]]:
    """Return (swatch_url, fabric_name, fabric_row_or_empty)."""
    if payload.fabric_image_url:
        return str(payload.fabric_image_url), None, {}

    code = (payload.fabric_catalog_code or "").strip()
    if not code:
        return None, None, {}

    fabric = await backend_api.get_fabric_details(code)
    if not fabric or fabric.get("error"):
        raise HTTPException(
            status_code=404,
            detail=f"Fabric not found for catalog_code={code!r}.",
        )
    image_url = fabric.get("image_url") or fabric.get("imageUrl")
    if not image_url:
        raise HTTPException(
            status_code=400,
            detail=f"Fabric {code} has no image_url in catalogue.",
        )
    return str(image_url), fabric.get("name"), fabric


@router.post("/api/generate-wedding-image", response_model=WeddingImageResponse)
async def generate_wedding_image(payload: WeddingImageRequest, request: Request):
    with session_cost_scope(payload.session_id):
        return await _generate_wedding_image_impl(payload, request)


async def _generate_wedding_image_impl(payload: WeddingImageRequest, request: Request):
    try:
        if not payload.session_id and settings.CHATBOT_QUOTA_ENFORCE:
            raise HTTPException(
                status_code=400,
                detail="session_id is required for custom image generation (quota tracking).",
            )
        if payload.session_id:
            decision = await require_quota(payload.session_id, "custom_image")
            if not decision.allowed:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "QUOTA_EXCEEDED",
                        "kind": "custom_image",
                        "message": decision.message,
                        "quota": decision.to_dict(),
                    },
                )

        category = (payload.dress_category or payload.category or "").strip()
        logger.info(
            "Wedding image request start category=%s fabric_code=%s "
            "fabric_url_override=%s output_format=%s",
            category,
            payload.fabric_catalog_code,
            payload.fabric_image_url is not None,
            payload.output_format,
        )

        swatch_url, fabric_name, _fabric_row = await _resolve_fabric_swatch(payload)

        if swatch_url:
            fabric_analysis = await analyze_fabric_image(swatch_url)
        else:
            logger.info("Fabric analysis skipped no fabric image")
            fabric_analysis = default_fabric_analysis()

        prompt = build_wedding_prompt(
            payload,
            fabric_analysis,
            fabric_name=fabric_name,
            has_fabric_image=bool(swatch_url),
        )
        logger.info("Wedding prompt built prompt_length=%s", safe_len(prompt))

        from app.services.fal_image import generate_image_bytes_fal

        fabric_urls = [swatch_url] if swatch_url else []
        if fabric_urls:
            prompt += (
                "\n\nFigure 1 is the fabric swatch from our catalogue. Apply this exact fabric "
                "colour, weave, texture, and embroidery onto the full-length outfit. No text."
            )
        image_bytes = generate_image_bytes_fal(prompt, image_urls=fabric_urls)

        filename = save_generated_image(
            image_bytes=image_bytes,
            output_format=payload.output_format,
        )
        logger.info("Generated image saved filename=%s", filename)

        image_url = build_public_image_url(request, filename)
        logger.info("Generated image public url=%s", image_url)

        image_metadata = await generate_image_metadata(
            prompt=prompt,
            dress_category=category,
            ceremony=category,
        )

        image_record_id = await image_store.save_generated_image_record(
            session_id=payload.session_id,
            user_id=payload.user_id,
            image_url=image_url,
            image_filename=filename,
            prompt=prompt,
            fabric_analysis=fabric_analysis,
            preferences=payload.model_dump(mode="json"),
            metadata=image_metadata.model_dump(),
        )
        logger.info(
            "Wedding image request complete record_id=%s metadata_title=%s",
            image_record_id,
            image_metadata.title,
        )

        return WeddingImageResponse(
            success=True,
            image_url=image_url,
            prompt=prompt,
            fabric_analysis=fabric_analysis,
            image_record_id=image_record_id,
            metadata=image_metadata,
            fabric_catalog_code=payload.fabric_catalog_code,
            dress_category=category,
        )

    except HTTPException:
        raise

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not fetch fabric image URL: {str(e)}",
        )

    except Exception as e:
        logger.exception("Image generation failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Image generation failed: {str(e)}",
        )
