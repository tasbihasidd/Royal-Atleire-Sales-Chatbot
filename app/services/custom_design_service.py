import logging
from typing import Any, Dict, Tuple
from app.config import settings
from app.routes.image_generation import (
    analyze_fabric_image,
    generate_image_bytes,
    save_generated_image,
    default_fabric_analysis,
    generate_image_metadata,
)
from app.services.image_store import image_store
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

    # 2. Construct Prompt (as a high-end atelier sales consultant & couturier)
    base_product_name = base_product.get("name") if base_product else "a bespoke piece"
    if variation_name and base_product:
        if variation_name.lower() not in base_product_name.lower():
            base_product_name = f"{base_product_name} ({variation_name} style)"
    
    if base_product:
        base_desc = base_product.get("description") or ""
        base_fabric = base_product.get("fabric") or ""
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
You are customizing the Royal Atelier signature piece "{base_product_name}".
1. PRESERVE THE IDENTITY: Retain the signature regal silhouette, structured shoulder cut, neckline, and opulent embroidery density of "{base_product_name}".
2. APPLY CUSTOM MODIFICATIONS ACCORDING TO USER INSTRUCTIONS:
>> Customer Request: {user_instructions} <<
(For example: if requested emerald green with silver work instead of gold, transform the garment base into a deep, rich emerald green silk/velvet, and adapt the embroidery threads and zardozi to shimmering antique silver work while maintaining the exquisite placement, sleeve details, and royal motifs of {base_product_name}).
3. DO NOT generate an unrelated, plain, or western-cut garment. It must look undeniably like the bespoke custom edition of "{base_product_name}".
"""
    else:
        design_blueprint = f"""
Bespoke Atelier Creation:
- Category: {dress_category or 'Luxury Wedding Menswear'}
- User Design Request: {user_instructions}
"""

    fabric_name = (
        (fabric_details or {}).get("name")
        or (base_product or {}).get("fabric")
        or "Premium Royal Atelier Fabric"
    )
    fabric_section = f"""
Fabric & Material Specifications:
- Material: {fabric_name}
- Fabric Texture: {fabric_analysis.get('texture', 'luxurious tactile texture')}
- Weave / Quality: {fabric_analysis.get('fabric_type_guess', 'high-end bridal couture')}
- Dominant Palette: {", ".join(fabric_analysis.get('dominant_colors', [])) or colors or 'regal tones'}
"""

    prompt = f"""
Create a high-end realistic fashion editorial photograph of a South Asian groom wearing an opulent bespoke wedding outfit from The Royal Atelier.

Context & Vision:
- Ceremony / Occasion: {ceremony or 'Royal Wedding Ceremony'}
- Primary Color Palette: {colors or 'Custom requested shades'}

{design_blueprint}

{fabric_section}

Image Aesthetics & Presentation Requirements:
- Full-length groom portrait showcasing the complete royal attire, trousers/churidar, and matching footwear.
- High-fashion bridal editorial photography (Vogue South Asia / Harper's Bazaar style).
- Tactile ultra-detailed fabric drape, 3D threadwork, luminous metallic zardozi/dabka embroidery.
- Confident, noble groom pose inside a grand heritage palace or royal courtyard with soft ambient natural light.
- Sharp photorealistic tailoring, natural fabric folds, and flawless proportions.
- No text, no watermark, no captions, no logos.
"""
    try:
        # 3. Generate Image Bytes
        image_bytes = generate_image_bytes(
            prompt=prompt,
            size="1024x1536",
            quality="medium",
            output_format="png"
        )

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
            "record_id": record_id
        }
        
        logger.info("generate_bespoke_design complete session_id=%s image_url=%s", session_id, image_url)
        return image_url, result_data
        
    except Exception as e:
        logger.exception("generate_bespoke_design failed")
        raise e
