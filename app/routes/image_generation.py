from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from datetime import date, time as time_type
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, Field, HttpUrl

from app.config import settings
from app.core.logging_config import log_openai_call, safe_len
from app.services.image_store import image_store
from app.services.styling_rules import (
    TIME_BASED_COLORS,
    SEASONAL_RECOMMENDATIONS,
    get_time_period,
    get_season,
    body_type_recommendation,
    skin_tone_recommendation,
)

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


def get_openai_client() -> OpenAI:
    """OpenRouter OpenAI-compatible client."""
    if not OPENROUTER_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENROUTER_API_KEY (or OPENAI_API_KEY) environment variable is missing.",
        )
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": settings.OPENROUTER_HTTP_REFERER,
            "X-Title": settings.OPENROUTER_APP_TITLE,
        },
    )


class WeddingImageRequest(BaseModel):

    session_id: Optional[str] = None
    user_id: Optional[str] = None
    
    # fabric_image_url is the URL of the fabric image to be used for the wedding image
    fabric_image_url: Optional[HttpUrl] = None
    
    # religion and ceremony are the religion and ceremony of the wedding
    religion: str
    ceremony: str
    
    # wedding_date and wedding_time are the date and time of the wedding
    wedding_date: date
    wedding_time: time_type
    location: str
    # venue_type is the type of the venue
    venue_type: str

    # dress_category is the category of the dress
    dress_category: str
    # styles, colors, embroidery, patterns are the styles, colors, embroidery, and patterns of the dress
    styles: List[str] = Field(default_factory=list)
    # colors are the colors of the dress
    colors: List[str] = Field(default_factory=list)
    # embroidery are the embroidery of the dress
    embroidery: List[str] = Field(default_factory=list)
    # patterns are the patterns of the dress
    patterns: List[str] = Field(default_factory=list)
    # fit is the fit of the dress
    fit: str

    # body_type is the body type of the groom
    body_type: str
    # skin_tone is the skin tone of the groom
    skin_tone: str

    # budget is the budget of the groom
    budget: str
    # delivery_timeline is the delivery timeline of the groom
    delivery_timeline: str

    # accessories are the accessories of the groom
    accessories: List[str] = Field(default_factory=list)
    # footwear is the footwear of the groom
    footwear: str

    # wedding_theme is the theme of the wedding
    wedding_theme: str
    # personal_preferences are the personal preferences of the groom
    personal_preferences: List[str] = Field(default_factory=list)

    # match_bride is a boolean indicating if the groom should match the bride
    match_bride: bool = False
    # bride_color is the color of the bride
    bride_color: Optional[str] = None
    # bride_fabric is the fabric of the bride
    bride_fabric: Optional[str] = None
    # bride_embroidery is the embroidery of the bride
    bride_embroidery: Optional[str] = None
    # bride_jewelry_tone is the jewelry tone of the bride
    bride_jewelry_tone: Optional[str] = None

    # size is the size of the image
    size: str = "1024x1536"
    # quality is the quality of the image
    quality: Literal["low", "medium", "high", "auto"] = "medium"
    # output_format is the format of the image
    output_format: Literal["png", "jpeg", "webp"] = "png"

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "fabric_image_url": "https://your-domain.com/uploads/fabric.png",
                    "religion": "Muslim",
                    "ceremony": "Walima",
                    "wedding_date": "2026-12-25",
                    "wedding_time": "19:30:00",
                    "location": "London, UK",
                    "venue_type": "Hotel Ballroom",
                    "dress_category": "Sherwani",
                    "styles": ["Royal", "Luxury"],
                    "colors": ["Ivory", "Gold"],
                    "embroidery": ["Hand Embroidery", "Antique Gold"],
                    "patterns": ["Mughal", "Paisley"],
                    "fit": "Tailored",
                    "body_type": "Athletic",
                    "skin_tone": "Wheatish",
                    "budget": "£500-1000",
                    "delivery_timeline": "One Month",
                    "accessories": ["Brooch", "Pocket Square", "Khussa"],
                    "footwear": "Khussa",
                    "wedding_theme": "Royal Palace",
                    "personal_preferences": ["Prefer Lightweight", "Breathable"],
                    "match_bride": True,
                    "bride_color": "Ivory",
                    "bride_fabric": "Silk",
                    "bride_embroidery": "Gold Zari",
                    "bride_jewelry_tone": "Gold",
                    "size": "1024x1536",
                    "quality": "medium",
                    "output_format": "png"
                }
            ]
        }
    }


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
    client = get_openai_client()
    data_url = await image_url_to_data_url(fabric_image_url)

    with log_openai_call(logger, operation="fabric_analysis", model=FABRIC_ANALYSIS_MODEL):
        response = client.responses.create(
            model=FABRIC_ANALYSIS_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": """
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
""",
                        },
                        {
                            "type": "input_image",
                            "image_url": data_url,
                        },
                    ],
                }
            ],
        )

    raw_text = clean_json_text(response.output_text)

    try:
        analysis = json.loads(raw_text)
    except Exception:
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
    client = get_openai_client()
    try:
        with log_openai_call(logger, operation="image_metadata", model=FABRIC_ANALYSIS_MODEL):
            response = client.chat.completions.create(
                model=FABRIC_ANALYSIS_MODEL,
                messages=[
                    {"role": "system", "content": _METADATA_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=600,
            )
        raw_text = clean_json_text(response.choices[0].message.content or "")
        data = json.loads(raw_text)
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


def build_wedding_prompt(data: WeddingImageRequest, fabric_analysis: Dict[str, Any]) -> str:
    time_period = get_time_period(data.wedding_time)
    season = get_season(data.wedding_date)

    time_colors = TIME_BASED_COLORS[time_period]
    seasonal = SEASONAL_RECOMMENDATIONS[season]

    bride_context = ""
    if data.match_bride:
        bride_context = f"""
Bride matching guidance:
- Bride dress color: {data.bride_color or "Not provided"}
- Bride fabric: {data.bride_fabric or "Not provided"}
- Bride embroidery: {data.bride_embroidery or "Not provided"}
- Bride jewelry tone: {data.bride_jewelry_tone or "Not provided"}
- Groom outfit should complement the bride, not exactly copy her look.
"""

    if data.fabric_image_url:
        fabric_section = f"""
Uploaded fabric reference analysis:
- Fabric type guess: {fabric_analysis.get("fabric_type_guess", "")}
- Dominant colors: {", ".join(fabric_analysis.get("dominant_colors", []))}
- Secondary colors: {", ".join(fabric_analysis.get("secondary_colors", []))}
- Pattern: {fabric_analysis.get("pattern", "")}
- Embroidery style: {fabric_analysis.get("embroidery_style", "")}
- Texture: {fabric_analysis.get("texture", "")}
- Visual weight: {fabric_analysis.get("visual_weight", "")}
- Luxury level: {fabric_analysis.get("luxury_level", "")}
- Style notes: {fabric_analysis.get("style_notes", "")}
- Recommended use: {fabric_analysis.get("recommended_use", "")}
"""
        design_instruction = """
Important design instruction:
Use the uploaded fabric image as the main design reference.
The final groom outfit should clearly reflect the same fabric feel, color family, embroidery language, texture, pattern mood, and luxury level from the uploaded fabric.
"""
    else:
        fabric_section = """
Fabric reference:
- No fabric image was uploaded.
- Design the outfit using the user-selected colors, styles, embroidery, patterns, dress category, and seasonal recommendations below.
"""
        design_instruction = """
Important design instruction:
No fabric image was provided. Create the groom outfit based on the user-selected dress category, colors, styles, embroidery, patterns, fit, and seasonal recommendations.
"""

    prompt = f"""
Create a high-end realistic fashion editorial image of a groom wearing a luxury wedding outfit.

Wedding context:
- Religion / tradition: {data.religion}
- Ceremony: {data.ceremony}
- Wedding date: {data.wedding_date.strftime("%d %B %Y")}
- Wedding time: {data.wedding_time.strftime("%I:%M %p")}
- Time period: {time_period}
- Location: {data.location}
- Venue type: {data.venue_type}
- Wedding theme: {data.wedding_theme}

Outfit design:
- Dress category: {data.dress_category}
- Style: {", ".join(data.styles)}
- User selected colors: {", ".join(data.colors)}
- Recommended time-based colors: {", ".join(time_colors)}
- Season: {season}
- Seasonal recommended colors: {", ".join(seasonal["colors"])}
- Seasonal recommended fabrics: {", ".join(seasonal["fabrics"])}
- User selected embroidery preferences: {", ".join(data.embroidery)}
- User selected pattern preferences: {", ".join(data.patterns)}
- Fit: {data.fit}
{fabric_section}
AI personalization:
- Body type: {data.body_type}
- Body type styling recommendation: {body_type_recommendation(data.body_type)}
- Skin tone: {data.skin_tone}
- Complexion-friendly color recommendation: {skin_tone_recommendation(data.skin_tone)}
- Budget range: {data.budget}
- Delivery timeline: {data.delivery_timeline}
- Personal preferences: {", ".join(data.personal_preferences)}

Accessories:
- Accessories: {", ".join(data.accessories)}
- Footwear: {data.footwear}

{bride_context}
{design_instruction}

Image requirements:
- Full-body groom outfit.
- Premium catalog photography.
- Realistic luxury wedding fashion.
- Elegant confident pose.
- Clean background inspired by selected venue.
- Realistic fabric texture, embroidery, collar, buttons, cuffs, footwear, and accessories.
- No text.
- No watermark.
- No logo.
- No extra people.
- No distorted hands.
- Commercially presentable for an online bespoke wedding menswear platform.
""".strip()

    return prompt


def generate_image_bytes(
    prompt: str,
    size: str,
    quality: str,
    output_format: str,
) -> bytes:
    client = get_openai_client()

    logger.info("Image generation start size=%s output_format=%s prompt_length=%s", size, output_format, safe_len(prompt))
    with log_openai_call(logger, operation="image_generation", model=IMAGE_MODEL):
        result = client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            size=size,
            quality=quality,
            output_format=output_format,
            n=1,
        )

    image_base64 = result.data[0].b64_json
    image_bytes = base64.b64decode(image_base64)
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


@router.post("/api/generate-wedding-image", response_model=WeddingImageResponse)
async def generate_wedding_image(payload: WeddingImageRequest, request: Request):
    try:
        logger.info(
            "Wedding image request start ceremony=%s dress_category=%s size=%s "
            "output_format=%s fabric_image_present=%s",
            payload.ceremony,
            payload.dress_category,
            payload.size,
            payload.output_format,
            payload.fabric_image_url is not None,
        )

        if not payload.styles:
            raise HTTPException(status_code=400, detail="styles is required.")

        if not payload.colors:
            raise HTTPException(status_code=400, detail="colors is required.")

        if not payload.embroidery:
            raise HTTPException(status_code=400, detail="embroidery is required.")

        if not payload.patterns:
            raise HTTPException(status_code=400, detail="patterns is required.")

        if payload.fabric_image_url:
            fabric_analysis = await analyze_fabric_image(str(payload.fabric_image_url))
        else:
            logger.info("Fabric analysis skipped no fabric image provided")
            fabric_analysis = default_fabric_analysis()

        prompt = build_wedding_prompt(payload, fabric_analysis)
        logger.info("Wedding prompt built prompt_length=%s", safe_len(prompt))

        image_bytes = generate_image_bytes(
            prompt=prompt,
            size=payload.size,
            quality=payload.quality,
            output_format=payload.output_format,
        )

        filename = save_generated_image(
            image_bytes=image_bytes,
            output_format=payload.output_format,
        )
        logger.info("Generated image saved filename=%s", filename)

        image_url = build_public_image_url(request, filename)
        logger.info("Generated image public url=%s", image_url)

        image_metadata = await generate_image_metadata(
            prompt=prompt,
            dress_category=payload.dress_category,
            ceremony=payload.ceremony,
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
        logger.info("Wedding image request complete record_id=%s metadata_title=%s", image_record_id, image_metadata.title)

        return WeddingImageResponse(
            success=True,
            image_url=image_url,
            prompt=prompt,
            fabric_analysis=fabric_analysis,
            image_record_id=image_record_id,
            metadata=image_metadata,
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