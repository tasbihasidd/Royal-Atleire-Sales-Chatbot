"""Prompt snippets for exact-swatch vs dye/recolor wedding image modes."""
from __future__ import annotations

_FABRIC_SURFACE_LOCK = """
FABRIC SURFACE LOCK (CRITICAL — do not invent embroidery on the cloth):
- Figure 1 / the catalogue swatch defines the CLOTH SURFACE only: weave, embossing, geometric/
  jacquard/self-pattern, texture, and sheen — repeat that SAME surface across the whole outfit.
- If the swatch is plain weave, embossed geometric, or self-pattern WITHOUT sewn floral/zardozi
  embroidery, the coat body must stay that way. Do NOT invent dense chest, collar, or panel
  embroidery, floral motifs, or a second fabric pattern that is not visible on the swatch.
- Embroidery tier (if any) means optional SEPARATE tailor thread-work (thin accents on collar/
  cuff/placket only) — it must NEVER rewrite, cover, or replace the swatch fabric surface.
- Do not treat "embroidery language" as permission to invent embroidery; copy only what is
  literally on the swatch.
""".strip()


def exact_swatch_design_instruction() -> str:
    return f"""
Important design instruction:
Use the fabric swatch image as the main material reference.
The outfit must clearly reflect the same fabric colour, weave, texture, and surface pattern
as the swatch — nothing more on the cloth body.
The fabric choice does NOT change the garment cut — cut comes from the user design brief.

{_FABRIC_SURFACE_LOCK}
""".strip()


def recolor_design_instruction(dye_color: str) -> str:
    return f"""
Important design instruction (FABRIC DYE / RECOLOR):
Use Figure 1 (catalogue fabric swatch) ONLY for weave, embossing, jacquard/self-pattern structure,
texture, and sheen — the cloth surface must match the swatch (recolored).
Do NOT copy the photographed swatch hue. Dominant colours from fabric analysis are reference-only — ignore them for final garment colour.
Dye the MAIN outfit fabric to {dye_color} consistently (coat/jacket and matching trousers/shalwar as one cloth family unless the user brief explicitly asks for contrast trim).
fabric_dye_color={dye_color} wins over any conflicting colour in the user brief for the main cloth.
Cut follows the brief; embroidery tier is separate thin thread-work only — do not invent fabric embroidery when recoloring.

{_FABRIC_SURFACE_LOCK}
""".strip()


def swatch_tail_append(*, dye_color: str | None) -> str:
    """Figure-1 instruction appended when a fabric swatch URL is passed to fal."""
    lock = (
        " Keep the SAME embossed/geometric/self-pattern surface from Figure 1 on the whole "
        "garment. Do NOT invent floral, zardozi, or dense chest embroidery that is not on "
        "the swatch."
    )
    if dye_color:
        return (
            f"\n\nFigure 1 is the fabric swatch from our catalogue. Keep its weave, "
            f"texture, and pattern structure, but DYE / RECOLOR "
            f"the entire main outfit fabric to {dye_color}. Do NOT keep the swatch "
            f"photograph's hue.{lock} No text."
        )
    return (
        "\n\nFigure 1 is the fabric swatch from our catalogue. Apply this exact fabric "
        f"colour, weave, texture, and surface pattern onto the full-length outfit.{lock} No text."
    )
