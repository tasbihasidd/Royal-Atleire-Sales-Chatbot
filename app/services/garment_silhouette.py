"""Garment cut / silhouette locks for bespoke Seedream prompts."""

from __future__ import annotations

import re


def infer_garment_cut(dress_category: str | None, user_instructions: str) -> str:
    """
    Normalize cut key. User free-text wins over dress_category when both name a cut
    (e.g. brief says '2 piece suit' but category field still says Sherwani).
    """
    brief = (user_instructions or "").lower()
    from_brief = _cut_from_text(brief)
    if from_brief != "unknown":
        return from_brief
    return _cut_from_text((dress_category or "").lower())


def _cut_from_text(blob: str) -> str:
    if not blob:
        return "unknown"
    if re.search(r"\bprince\s*coat\b|\bbandhgala\b|\bjodhpuri\b", blob):
        return "prince_coat"
    if re.search(r"\bsherwani\b|\bachkan\b", blob):
        return "sherwani"
    if re.search(r"\btuxedo\b|\bdinner\s*jacket\b", blob):
        return "tuxedo"
    if re.search(r"\b2\s*piece\s*suit\b|\bthree\s*piece\s*suit\b|\b3\s*piece\s*suit\b|\bsuit\b|\bblazer\b", blob):
        return "suit"
    if "prince" in blob:
        return "prince_coat"
    if "sherwani" in blob:
        return "sherwani"
    if "tuxedo" in blob:
        return "tuxedo"
    if "suit" in blob:
        return "suit"
    return "unknown"


def category_label_for_cut(cut: str, fallback: str | None = None) -> str:
    return {
        "prince_coat": "Prince Coat",
        "sherwani": "Sherwani",
        "tuxedo": "Tuxedo",
        "suit": "Suits",
    }.get(cut) or (fallback or "Wedding menswear")


def garment_silhouette_guidance(dress_category: str | None, user_instructions: str) -> str:
    """
    Hard garment-shape rules for Seedream. Prevents Prince Coat → winter overcoat drift.
    """
    cut = infer_garment_cut(dress_category, user_instructions)
    wants_shalwar = bool(
        re.search(r"shalwar|shalwar\s*kameez|shalwaar|white\s+kameez", (user_instructions or "").lower())
    )
    underlayer = (
        "plain white shalwar kameez (mandarin-collar kameez + matching white shalwar trousers)"
        if wants_shalwar
        else "a clean formal inner layer appropriate to the cut"
    )

    if cut == "prince_coat":
        return f"""
GARMENT CUT LOCK — PRINCE COAT (mandatory; do not reinterpret as another coat):
A Turabees Prince Coat is Indo-Western formalwear — NOT a winter overcoat, trench, pea coat, or western topcoat.

MUST LOOK LIKE:
- Hip-length to mid-thigh jacket (ends around the hips / upper thigh) — NEVER knee-length or longer.
- High closed Mandarin / Nehru / bandhgala stand collar — NO notch lapels, NO peak lapels, NO shawl lapels, NO folded open collar.
- Front is buttoned or clearly meant to fasten with a neat buttoned placket (bandhgala-style) — NOT worn like an open bathrobe or open overcoat.
- Structured tailored shoulders, clean fitted torso, slim sleeves — sleek wedding silhouette.
- Underneath: {underlayer}. Inner white kameez collar may peek slightly at the neck if the coat collar allows; do not replace the Prince Coat collar with a western shirt collar + lapels.
- If "minimalistic": keep embroidery sparse / tonal / edge-only — do NOT turn the jacket into a heavy allover embroidered robe, and do NOT invent western lapels to "simplify" it.

STRICTLY FORBIDDEN (these make it look like a winter coat):
- Wide lapels, open-front long coat, overcoat length past mid-thigh, double-breasted trench look, wool winter coat vibe, flared hem like a long sherwani when the request is Prince Coat.
"""

    if cut == "sherwani":
        return f"""
GARMENT CUT LOCK — SHERWANI:
- Knee-length or just-above-knee structured sherwani with closed Mandarin / bandhgala collar.
- Buttoned front placket — not an open western overcoat with lapels.
- Underneath: {underlayer}.
- No notch/peak/shawl lapels unless the customer explicitly asks for an Indo-Western fusion with lapels.
"""

    if cut == "tuxedo":
        return """
GARMENT CUT LOCK — TUXEDO:
- Classic dinner jacket length (hip), satin shawl or peak lapels only as tuxedo formalwear — not a wool winter topcoat.
- Matching formal trousers, dress shirt, bow tie.
"""

    if cut == "suit":
        return """
GARMENT CUT LOCK — SUIT:
- Standard tailored suit jacket (hip length) with the lapel style requested; matching trousers.
- Not a knee-length overcoat unless the customer asked for an overcoat.
"""

    return f"""
GARMENT CUT LOCK — GENERAL:
- Follow the named cut in the customer request exactly (Prince Coat ≠ Sherwani ≠ Suit ≠ winter coat).
- Underlayer: {underlayer}.
- Never default to open-front western overcoat with wide lapels unless that cut was requested.
"""
