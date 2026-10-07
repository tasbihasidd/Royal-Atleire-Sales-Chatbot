"""Complementary groom styling brief from optional bride look fields.

Uses only the fields provided — any subset is valid. Does not clone the bride;
steers palette / embroidery weight / metal accents toward a complementary look.
"""

from __future__ import annotations

from typing import Any


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _complement_palette(bride_color: str) -> str:
    low = bride_color.lower()
    # Warm bridal reds/maroons → cooler or ivory/cream contrast for groom
    if any(w in low for w in ("red", "maroon", "crimson", "ruby", "wine", "burgundy")):
        return "ivory, cream, or soft champagne for the groom (avoid matching the bride's red exactly)"
    if any(w in low for w in ("ivory", "cream", "off-white", "white", "beige")):
        return "deep emerald, navy, charcoal, or soft gold accents that frame the bridal ivory"
    if any(w in low for w in ("pink", "rose", "blush", "peach")):
        return "stone, taupe, champagne, or muted sage — soft neutrals that do not compete with blush"
    if any(w in low for w in ("gold", "mustard", "yellow", "amber")):
        return "ivory, warm taupe, or deep espresso with restrained gold thread — not a matching gold twin"
    if any(w in low for w in ("green", "emerald", "mehndi", "olive")):
        return "cream, champagne, or soft sand with subtle green undertone accents only"
    if any(w in low for w in ("blue", "navy", "teal", "turquoise")):
        return "warm ivory, sand, or soft grey with cool-metal restraint — not the same blue"
    if any(w in low for w in ("purple", "lavender", "lilac", "violet")):
        return "silver-grey, ivory, or soft plum undertone — avoid identical purple"
    if any(w in low for w in ("black", "charcoal", "grey", "gray")):
        return "rich ivory, soft cream, or deep jewel accents that lift a dark bridal palette"
    return (
        f"a complementary (not identical) palette relative to the bride's {bride_color} — "
        "harmonise, do not clone"
    )


def _embroidery_lean(bride_embroidery: str | None, groom_tier: str | None) -> str | None:
    if groom_tier:
        return None  # explicit groom tier already set on the request
    text = (bride_embroidery or "").lower()
    if not text:
        return None
    if any(w in text for w in ("heavy", "zyada", "full", "zardozi", "dense", "ornate")):
        return "keep groom embroidery one step lighter than the bride (balanced Medium/Light, not competing)"
    if any(w in text for w in ("light", "halka", "minimal", "subtle", "none", "plain")):
        return "groom may carry Medium embroidery so the pair stays balanced without matching density"
    return "balance embroidery weight against the bride — complement density, do not mirror it"


def _metal_accents(jewelry_tone: str) -> str:
    low = jewelry_tone.lower()
    if "rose" in low:
        return "rose-gold or warm copper-metal accents only (buttons, brooch, thread highlights)"
    if "silver" in low or "platin" in low or "white gold" in low:
        return "silver / cool-metal accents (buttons, brooch, thread) — avoid warm gold clash"
    if "mix" in low:
        return "restrained mixed-metal accents; keep one dominant tone for polish"
    if "gold" in low:
        return "warm gold-metal accents (buttons, brooch, zardozi highlights) aligned with bridal jewellery"
    return f"metal accents compatible with bridal jewellery tone ({jewelry_tone})"


def build_groom_complement_brief(
    *,
    bride_color: str | None = None,
    bride_fabric: str | None = None,
    bride_embroidery: str | None = None,
    bride_jewelry_tone: str | None = None,
    groom_embroidery_tier: str | None = None,
) -> dict[str, Any]:
    """Return structured complement guidance + English brief for the image prompt."""
    color = _clean(bride_color)
    fabric = _clean(bride_fabric)
    embroidery = _clean(bride_embroidery)
    jewelry = _clean(bride_jewelry_tone)
    tier = _clean(groom_embroidery_tier)

    palette = _complement_palette(color) if color else None
    emb_note = _embroidery_lean(embroidery, tier)
    metal = _metal_accents(jewelry) if jewelry else None

    lines: list[str] = [
        "AI MATCH WITH BRIDE (complementary groom styling — do NOT clone the bride's look):",
    ]
    if color:
        lines.append(f"- Bride dress colour: {color}. Groom palette: {palette}.")
    if fabric:
        lines.append(
            f"- Bride fabric note: {fabric}. Echo texture mood subtly if appropriate; "
            "do not copy the bridal fabric verbatim onto the groom."
        )
    if embroidery:
        lines.append(f"- Bride embroidery: {embroidery}.")
        if emb_note:
            lines.append(f"- {emb_note}.")
    if jewelry:
        lines.append(f"- Bride jewellery tone: {jewelry}. Groom accents: {metal}.")

    if not any((color, fabric, embroidery, jewelry)):
        lines.append(
            "- Bride details were not specified. Still design a complementary groom look "
            "that sits beside a bridal outfit without matching it exactly "
            "(balanced palette, restrained embroidery, polished formal finish)."
        )

    brief = "\n".join(lines)
    return {
        "bride_color": color,
        "bride_fabric": fabric,
        "bride_embroidery": embroidery,
        "bride_jewelry_tone": jewelry,
        "suggested_groom_palette": palette,
        "embroidery_guidance": emb_note,
        "metal_accents": metal,
        "brief": brief,
    }
