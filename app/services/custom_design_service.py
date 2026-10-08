import logging
from typing import Any, Dict, Tuple
from app.config import settings
from app.routes.image_generation import (
    analyze_fabric_image,
    save_generated_image,
    default_fabric_analysis,
    generate_image_metadata,
)
from app.services.fal_errors import FalProviderError
from app.services.fal_image import generate_image_bytes_fal
from app.services.garment_silhouette import garment_silhouette_guidance
from app.services.image_store import image_store
from app.services.quota_service import require_quota
from fastapi import Request

logger = logging.getLogger(__name__)


async def generate_bespoke_design(
    base_product: Dict[str, Any] | None,
    fabric_details: Dict[str, Any] | None,
    user_instructions: str,
    ceremony: str | None,
    colors: str | None,
    dress_category: str | None,
    session_id: str | None,
    variation_name: str | None = None,
) -> Tuple[str | None, Dict[str, Any]]:
    """
    Generates a bespoke design image for a given product/fabric and user instructions.
    """
    logger.info("generate_bespoke_design start session_id=%s variation=%s", session_id, variation_name)

    if session_id:
        decision = await require_quota(session_id, "custom_image")
        if not decision.allowed:
            logger.info(
                "generate_bespoke_design quota exceeded session_id=%s used=%s",
                session_id,
                decision.used,
            )
            return None, {
                "error": "quota_exceeded",
                "quota": decision.to_dict(),
                "message": decision.message,
            }

    # 1. Analyze Fabric / Reference Image
    ref_image_url = None
    if fabric_details and fabric_details.get("image_url"):
        ref_image_url = fabric_details["image_url"]
    elif base_product and base_product.get("image_url"):
        ref_image_url = base_product["image_url"]
    elif base_product and isinstance(base_product.get("images"), list) and base_product["images"]:
        ref_image_url = base_product["images"][0]

    if ref_image_url and not ref_image_url.startswith(("http://", "https://")):
        from app.services.backend_api import resolve_backend_asset_url
        ref_image_url = resolve_backend_asset_url(ref_image_url)

    fabric_analysis = default_fabric_analysis()
    if ref_image_url:
        try:
            fabric_analysis = await analyze_fabric_image(ref_image_url)
        except Exception as e:
            logger.warning("Fabric/Product image analysis failed, using default: %s", e)

    product_image_url = None
    if base_product:
        product_image_url = base_product.get("image_url")
        if not product_image_url and isinstance(base_product.get("images"), list) and base_product.get("images"):
            product_image_url = base_product["images"][0]
        if product_image_url and not str(product_image_url).startswith(("http://", "https://")):
            from app.services.backend_api import resolve_backend_asset_url
            product_image_url = resolve_backend_asset_url(product_image_url)

    fabric_image_url = (fabric_details or {}).get("image_url")
    if fabric_image_url and not str(fabric_image_url).startswith(("http://", "https://")):
        from app.services.backend_api import resolve_backend_asset_url
        fabric_image_url = resolve_backend_asset_url(fabric_image_url)

    # Fabric-first reference order: chosen swatch must drive Seedream edit.
    # Product image (if any) is secondary silhouette only.
    image_urls: list[str] = []
    for url in (fabric_image_url, product_image_url):
        if url and str(url) not in image_urls:
            image_urls.append(str(url))

    logger.info(
        "generate_bespoke_design refs session_id=%s fabric_url=%s product_url=%s ref_count=%s",
        session_id,
        bool(fabric_image_url),
        bool(product_image_url),
        len(image_urls),
    )

    # 2. Construct Prompt (as a high-end atelier sales consultant & couturier)
    fabric_name = (
        (fabric_details or {}).get("name")
        or (base_product or {}).get("fabric")
        or "Premium Turabees Fabric"
    )
    if base_product:
        base_product_name = base_product.get("name") or "a bespoke piece"
    else:
        # Fabric-first Path A: edition label is the chosen cloth, not a random catalogue title.
        base_product_name = fabric_name
    if variation_name and base_product:
        if variation_name.lower() not in base_product_name.lower():
            base_product_name = f"{base_product_name} ({variation_name} style)"
    
    if base_product:
        base_desc = base_product.get("description") or ""
        base_fabric = base_product.get("fabric") or fabric_name
        base_embroidery = (
            base_product.get("embroidery")
            or base_product.get("embroidery_level")
            or "intricate royal zardozi"
        )
        base_cat = base_product.get("category") or dress_category or "Sherwani"
        var_cut_line = f"- Specific Variation Cut: {variation_name} styling and silhouette\n" if variation_name else ""
        design_blueprint = f"""
Base Design Blueprint & Signature Style:
- Signature Piece: "{base_product_name}"
- Garment Category & Cut: {base_cat} (preserve the authentic structured royal collar, majestic silhouette, and regal tailored fit)
{var_cut_line}- Design Heritage: {base_desc}
- Original Craftsmanship: {base_fabric} with {base_embroidery}

        CRITICAL FIDELITY & CUSTOMIZATION DIRECTIVE:
You are customizing the Turabees signature piece "{base_product_name}".
1. PRESERVE THE IDENTITY: Retain the signature regal silhouette, structured shoulder cut, neckline, and embroidery density of "{base_product_name}" UNLESS the customer explicitly asks for minimal / no embroidery / simpler look.
2. APPLY CUSTOM MODIFICATIONS ACCORDING TO USER INSTRUCTIONS:
>> Customer Request: {user_instructions} <<
3. The outfit MUST be made from the customer's chosen fabric swatch (see fabric reference). Match that cloth's colour and texture exactly.
4. If the customer asks for minimal / no embroidery: reduce heavy gold/zardozi — keep clean surfaces. Do NOT change the garment category into a western overcoat with lapels to "simplify" it.
"""
    else:
        design_blueprint = f"""
Bespoke Atelier Creation (fabric-first):
- Category / Cut: {dress_category or 'Luxury Wedding Menswear'}
- Chosen Fabric: {fabric_name}
- User Design Request: {user_instructions}

CRITICAL:
1. Build the outfit FROM the fabric reference image (colour, weave, sheen, texture).
2. Follow the cut/style named in the user request exactly (Prince Coat ≠ Sherwani ≠ Suit ≠ winter overcoat).
3. If they ask for minimal / no embroidery: keep clean, tonal surfaces — do NOT invent western lapels or overcoat length unless that cut was requested.
4. Do NOT invent a different fabric colour or a random catalogue piece title.
"""

    cut_lock = garment_silhouette_guidance(dress_category, user_instructions)

    fabric_section = f"""
Fabric & Material Specifications:
- Material: {fabric_name}
- Fabric Texture: {fabric_analysis.get('texture', 'luxurious tactile texture')}
- Weave / Quality: {fabric_analysis.get('fabric_type_guess', 'high-end bridal couture')}
- Dominant Palette: {", ".join(fabric_analysis.get('dominant_colors', [])) or colors or 'regal tones'}
"""

    prompt = f"""
Create a high-end realistic fashion product photograph of a Turabees bespoke wedding outfit displayed on a mannequin.

Context & Vision:
- Ceremony / Occasion: {ceremony or 'Royal Wedding Ceremony'}
- Primary Color Palette: {colors or 'Custom requested shades'}

{design_blueprint}

{cut_lock}

{fabric_section}

Image Aesthetics & Presentation Requirements:
- Display the outfit on a maroon / deep burgundy dressmaker mannequin (tailor's dress form), NOT a human model.
- Minimal, clean photography studio background — soft neutral seamless backdrop (light grey or off-white), gentle even studio lighting.
- The scene must clearly read as a professional fashion studio shoot — no palace, courtyard, outdoor landscape, or busy scenery.
- Full-length mannequin view from head of form to shoes — complete styled look, never cropped above the ankles.
- COMPLETE STYLING (mandatory — not optional):
  • Inner layer must match the brief: if shalwar kameez is requested, use plain white shalwar kameez — not a western dress shirt + tie unless the cut is Suit/Tuxedo.
  • For Prince Coat / Sherwani / Bandhgala: Mandarin or closed band collar on the outer garment; polished formal leather shoes; trousers or shalwar sitting cleanly over shoes.
  • For suits / tuxedos only: dress shirt, coordinated tie or bow tie, and pocket square when the cut allows.
  • NEVER bare mannequin feet or bare ankles; NEVER an open winter overcoat silhouette when Prince Coat or Sherwani was requested.
- Tactile ultra-detailed fabric drape; embroidery only as requested (minimal means sparse/tonal, not dense allover grid robes).
- Sharp photorealistic tailoring, natural fabric folds, and flawless proportions.
- No text, no watermark, no captions, no logos.
"""
    if len(image_urls) >= 2:
        prompt += (
            "\nFigure 1 is the FABRIC SWATCH the customer chose — match its exact colour, weave, "
            "and texture on the outfit. Figure 2 is an optional garment silhouette reference. "
            "The finished outfit MUST look like it is made from Figure 1's fabric."
        )
    elif image_urls:
        prompt += (
            "\nThe reference image is the customer's chosen FABRIC SWATCH. "
            "Generate the outfit using this exact fabric colour, weave, sheen, and texture. "
            "Do not invent a different cloth."
        )
    try:
        # 3. Generate Image Bytes via fal Seedream
        image_bytes = generate_image_bytes_fal(prompt, image_urls=image_urls)

        # 4. Save and build URL
        filename = save_generated_image(image_bytes, "png")
        base_url = settings.BASE_URL.rstrip('/') if settings.BASE_URL else "http://localhost:8015"
        image_url = f"{base_url}/static/generated/{filename}"
        
        # 5. Generate Metadata
        metadata = await generate_image_metadata(
            prompt=prompt,
            dress_category=dress_category or "Menswear",
            ceremony=ceremony or "Wedding"
        )
        
        # 6. Record to DB
        record_id = await image_store.save_generated_image_record(
            session_id=session_id,
            user_id=None,
            image_url=image_url,
            image_filename=filename,
            prompt=prompt,
            fabric_analysis=fabric_analysis,
            preferences={"user_instructions": user_instructions},
            metadata=metadata.model_dump()
        )
        
        result_data = {
            "image_url": image_url,
            "prompt_summary": "Generated bespoke design based on instructions.",
            "modified_features": user_instructions,
            "base_product_name": base_product_name,
            "generated_product_name": metadata.title,
            "record_id": record_id
        }
        
        logger.info("generate_bespoke_design complete session_id=%s image_url=%s", session_id, image_url)
        return image_url, result_data
        
    except FalProviderError as e:
        logger.warning(
            "generate_bespoke_design fal error session_id=%s code=%s",
            session_id,
            e.code,
        )
        return None, {
            "error": "fal_provider_error",
            "code": e.code,
            "message": e.message,
            "fal": e.to_detail(),
        }
    except Exception as e:
        logger.exception("generate_bespoke_design failed")
        raise e
