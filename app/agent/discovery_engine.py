from __future__ import annotations

import logging
import re
from typing import Any

from app.schemas.profile import CustomerProfileSchema

logger = logging.getLogger(__name__)

DISCOVERY_PRIORITY_CHAIN = [
    ("event_type", "Occasion (Nikah, Barat, Walima, Mehndi)"),
    ("preference", "Month/season, colour preference, or budget range"),
]

# Search only after event is identified (or explicit show/dikhao).
CORE_DISCOVERY_SLOTS = (
    "event_type",
)

CATALOG_ASK_CUES = [
    "show me",
    "show some",
    "show options",
    "show",
    "dikhao",
    "dikha",
    "dikhaen",
    "options dikhao",
    "options",
    "kuch options",
    "kuch dikhao",
    "kuch dekhao",
    "kuch designs",
    "kuch piece",
    "kuch pieces",
    "catalog",
    "available outfits",
    "jo available",
    "see some",
    "dekhna",
    "dekhao",
    "dekhne hain",
    "dekhna chahta",
    "collection dikhao",
    "designs dikhao",
    "haan dikhao",
    "han dikhao",
    "yes show",
    "please show",
    "products dikhao",
    "pieces dikhao",
    "sherwani dikhao",
    "suits dikhao",
    "suit dikhao",
    "tuxedo dikhao",
    "coat dikhao",
    "prince coat dikhao",
    "show more",
    "show more options",
    "more options",
    "aur dikhao",
    "aur dekhao",
    "kuch aur",
    "kuch aur dikhao",
    "kuch or dikhao",
    "more designs",
    "more pieces",
    "any other",
    "koi aur",
    "koi or",
    "aur options",
]

# Soft guidance / confusion cues (customer wants consultative styling advice, not product dump)
GUIDANCE_ASK_CUES = [
    "suggest",
    "suggets",  # common typo
    "recommend",
    "suggestion",
    "suggestions",
    "no idea",
    "dont know",
    "don't know",
    "kuch suggest",
    "aap batao",
    "aap hi batao",
    "what should",
    "which one should",
    "koi idea",
    "idea nahi",
    "idea nhi",
    "confuse",
    "confused",
    "samajh nahi",
    "samjh nahi",
    "guidance",
    "help me choose",
    "kya pehnu",
    "kya pehno",
    "what to wear",
]

# Hard and soft slots: empty so they never block search when event/garment is known
SEARCH_HARD_SLOTS = ()
SEARCH_SOFT_SLOTS = ()

# Direct stock / colour availability questions → search candidate (still gated by prerequisites).
AVAILABILITY_ASK_CUES = [
    "available",
    "availbale",  # common typo
    "availability",
    "stock",
    "kon kon",
    "kon konsay",
    "konsay",
    "konsa",
    "konsi",
    "which color",
    "which colour",
    "what color",
    "what colour",
    "colors main",
    "colours main",
    "color mein",
    "colour mein",
    "colors mein",
    "colours mein",
    "kitne color",
    "kitne colour",
    "hai kya",
    "hain kya",
    "milaiga",
    "milega",
    "milengi",
]

DECLINE_PATTERNS = [
    re.compile(r"\b(skip|later|baad\s*mein|not\s*now|don't\s*want|dont\s*want|no\s*budget|prefer\s*not)\b", re.I),
    re.compile(r"\b(budget\s*nahi|size\s*nahi|bata\s*nahi|skip\s*kar)\b", re.I),
    re.compile(
        r"\b(preference\s+nahi|koi\s+preference\s+nahi|jo\s+acha|anything\s+fine|"
        r"aap\s+hi\s+batao|no\s+preference)\b",
        re.I,
    ),
]


class DiscoveryEngine:
    """
    Consultative Discovery Engine:
    Ask 1 structured question at a time when profile gaps exist.
    Never re-ask fields the customer declined.
    """

    def evaluate_entity_gaps(
        self,
        profile: CustomerProfileSchema,
        category_variations: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        declined = set(profile.declined_slots or [])
        missing: list[str] = []
        has_concrete_preference = bool(
            (getattr(profile, "preferred_colors", []) or [])
            or getattr(profile, "color", None)
            or profile.budget is not None
            or "preference" in declined
            or "budget" in declined
            or "color" in declined
        )
        if not profile.event_type and "event_type" not in declined:
            missing.append("event_type")
        elif not has_concrete_preference:
            missing.append("preference")
        return missing

    def get_next_missing_entity(
        self,
        profile: CustomerProfileSchema,
        category_variations: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str] | None:
        missing_fields = set(self.evaluate_entity_gaps(profile, category_variations))
        if not missing_fields:
            return None
        for key, desc in DISCOVERY_PRIORITY_CHAIN:
            if key in missing_fields:
                return (key, desc)
        return None

    def detect_declined_slots(
        self,
        profile: CustomerProfileSchema,
        user_message: str,
        category_variations: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        """If customer declines answering the current gap, mark that slot declined."""
        if not any(p.search(user_message) for p in DECLINE_PATTERNS):
            return list(profile.declined_slots or [])

        next_gap = self.get_next_missing_entity(profile, category_variations)
        if not next_gap:
            return list(profile.declined_slots or [])

        key, _ = next_gap
        declined = list(dict.fromkeys([*(profile.declined_slots or []), key]))
        logger.info(
            "Discovery slot declined session_id=%s slot=%s",
            profile.session_id,
            key,
        )
        return declined

    def customer_asked_for_catalog(self, user_message: str) -> bool:
        lowered = (user_message or "").lower()
        if self.customer_asked_for_guidance(lowered):
            return any(
                cue in lowered
                for cue in [
                    "dikhao",
                    "dekhao",
                    "show me",
                    "show options",
                    "designs dikhao",
                    "catalog dikhao",
                    "pieces dikhao",
                    "sherwani dikhao",
                    "suits dikhao",
                    "suit dikhao",
                ]
            )
        return any(cue in lowered for cue in CATALOG_ASK_CUES)

    def customer_asked_availability(self, user_message: str) -> bool:
        lowered = (user_message or "").lower()
        return any(cue in lowered for cue in AVAILABILITY_ASK_CUES)

    def customer_asked_for_guidance(self, user_message: str) -> bool:
        lowered = (user_message or "").lower()
        return any(cue in lowered for cue in GUIDANCE_ASK_CUES)

    def should_run_product_search(self, user_message: str) -> bool:
        """Show options OR ask what's available / which colours / ask suggestions → candidate for catalog."""
        return (
            self.customer_asked_for_catalog(user_message)
            or self.customer_asked_availability(user_message)
            or self.customer_asked_for_guidance(user_message)
        )

    def missing_search_prerequisites(
        self,
        profile: CustomerProfileSchema,
        category_variations: list[dict[str, Any]] | None = None,
        user_message: str = "",
    ) -> list[str]:
        """Event is needed first, followed by at least one concrete preference (color/budget) unless customer explicitly commands show/catalog or asks guidance."""
        missing: list[str] = []
        # Impatient catalog ask ("dikhao" / show options): search immediately — do not block on event/prefs.
        if self.customer_asked_for_catalog(user_message):
            return []
        if not (profile.product_type or profile.event_type):
            missing.append("event_type")
            return missing
        # Guidance without a garment/event still needs an event before search.
        if self.customer_asked_for_guidance(user_message):
            return []
        # If products already shown in session, allow search
        if profile.products_shown_ids or profile.selected_product_id:
            return []
        declined = set(profile.declined_slots or [])
        has_concrete_pref = bool(
            (getattr(profile, "preferred_colors", []) or [])
            or getattr(profile, "color", None)
            or profile.budget is not None
            or "preference" in declined
            or "budget" in declined
            or "color" in declined
        )
        if not has_concrete_pref:
            missing.append("preference")
        return missing

    def can_run_product_search(
        self,
        profile: CustomerProfileSchema,
        user_message: str,
        category_variations: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Allow search whenever event is known AND (preferences provided OR customer asked to see catalog/options/guidance)."""
        return not self.missing_search_prerequisites(profile, category_variations, user_message=user_message)

    def should_trigger_discovery(
        self,
        profile: CustomerProfileSchema,
        user_message: str,
        intent: str,
        category_variations: list[dict[str, Any]] | None = None,
    ) -> bool:
        lowered = (user_message or "").lower()
        if any(
            kw in lowered
            for kw in ["consultant", "handover", "human", "baat karwa", "checkout", "buy"]
        ):
            return False
        if intent in (
            "handover",
            "discount_request",
            "inventory_check",
            "measurement_check",
            "fabric_custom",
        ):
            return False
        if self.customer_asked_for_catalog(user_message) or self.customer_asked_for_guidance(user_message):
            return False

        # If products already shown in this session, do not re-enter initial discovery
        if profile.products_shown_ids or profile.selected_product_id:
            return False

        # Step 1: Missing event
        if not profile.event_type:
            return True

        # Step 2: Event is known, but no concrete preference (color or budget) given yet
        declined = set(profile.declined_slots or [])
        has_concrete_preference = bool(
            (getattr(profile, "preferred_colors", []) or [])
            or getattr(profile, "color", None)
            or profile.budget is not None
            or "preference" in declined
            or "budget" in declined
            or "color" in declined
        )
        if not has_concrete_preference:
            return True

        return False


    def _event_fit_categories(
        self,
        event_type: str | None,
        catalog_categories: list[dict[str, Any]] | None,
    ) -> tuple[str, list[str], list[str]]:
        """Return (styling_rule, preferred live names, avoid live names)."""
        event = (event_type or "").lower()
        live_names = [
            str(cat.get("name") or "").strip()
            for cat in (catalog_categories or [])
            if str(cat.get("name") or "").strip() and int(cat.get("product_count") or 0) > 0
        ]
        live_l = [(n, n.lower()) for n in live_names]

        def pick(*needles: str) -> list[str]:
            found: list[str] = []
            for name, low in live_l:
                if any(n in low for n in needles) and name not in found:
                    found.append(name)
            return found

        if any(x in event for x in ("walima", "valima", "reception")):
            rule = (
                "Walima/Valima → Western formal: Suit / three-piece / Tuxedo. "
                "Do NOT suggest Sherwani or Prince Coat unless they ask traditional."
            )
            preferred = pick("suit", "tuxedo", "blazer", "dinner")
            avoid = pick("sherwani", "prince")
        elif any(x in event for x in ("nikah", "nikkah")):
            rule = (
                "Nikkah → Sherwani first. Do NOT open with Suit/Tuxedo/Prince Coat "
                "if Sherwani is in catalog."
            )
            preferred = pick("sherwani")
            avoid = pick("suit", "tuxedo", "prince")
        elif any(x in event for x in ("barat", "baraat")):
            rule = (
                "Barat → Sherwani or Prince Coat (traditional groom). "
                "Suit only if they ask Western."
            )
            preferred = pick("sherwani", "prince")
            avoid = []
        elif "mehndi" in event or "mehendi" in event or "mehandi" in event:
            rule = "Mehndi → lighter festive options from catalog; less formal than Barat."
            preferred = pick("sherwani", "suit", "kurta")
            avoid = []
        else:
            rule = "Use wedding menswear judgment; only name categories from the live catalog."
            preferred = live_names[:3]
            avoid = []

        return rule, preferred, avoid

    def build_discovery_prompt_guidance(
        self,
        profile: CustomerProfileSchema,
        catalog_categories: list[dict[str, Any]] | None = None,
        event_type: str | None = None,
        category_variations: list[dict[str, Any]] | None = None,
    ) -> str:
        next_gap = self.get_next_missing_entity(profile, category_variations)
        live_cats = []
        for cat in catalog_categories or []:
            name = str(cat.get("name") or "").strip()
            count = int(cat.get("product_count") or 0)
            if name and count > 0:
                live_cats.append(f"- {name} ({count} products)")
        catalog_block = (
            "Live catalog (INTERNAL ONLY — do not invent names beyond tools):\n"
            + "\n".join(live_cats)
            + "\n"
            if live_cats
            else "Live catalog categories are unavailable this turn.\n"
        )
        event_label = event_type or profile.event_type or "the event they named"
        styling_rule, preferred, avoid = self._event_fit_categories(event_label, catalog_categories)
        variation_names = [
            str(v.get("name") or "").strip()
            for v in (category_variations or [])
            if str(v.get("name") or "").strip()
        ]
        variations_block = (
            f"Live variations for {profile.product_type or 'this category'} "
            f"(ONLY these — list grows/changes via API, never invent): {', '.join(variation_names)}.\n"
            if variation_names
            else "No live variations for this category this turn.\n"
        )
        styling_block = (
            "DISCOVERY SPEECH RULE: Do NOT invent category/variation names. "
            "Do NOT dump products. Do NOT say stock claims. "
            f"(Internal event_fit={preferred or 'n/a'}; avoid={avoid or 'n/a'}; {styling_rule})\n"
        )

        if not next_gap:
            return (
                f"\n\n[CONSULTATIVE DISCOVERY MODE ACTIVE]\n"
                f"{catalog_block}"
                f"{variations_block}"
                f"{styling_block}"
                f"Preferences look complete. In the customer's language (English or Roman Urdu), "
                f"ask once if they would like to see our curated options. "
                f"Do NOT search until they confirm. No stock claims. No 'Noted' echo.\n"
            )

        target_key, target_desc = next_gap

        timing_known = bool(profile.wedding_date or profile.derived_season)
        timing_label = profile.wedding_date or profile.derived_season or ""

        guidance_map = {
            "event_type": (
                "If they already named an event, do NOT repeat it back ('Noted – Nikkah' etc.). "
                "Go straight to the next useful question. "
                "If this is an opening hello/hi with no event yet: give one short warm welcome "
                "then ask which wedding event they are shopping for (Nikkah, Barat, Walima, or Mehndi), "
                "strictly matching the customer's language (English if they greeted in English, Roman Urdu if in Urdu)."
            ),
            "preference": (
                f"Customer named {event_label} (Timing/Month: {timing_label}). Do NOT search or dump products this turn! "
                "The customer is seeking guidance or has not yet provided color and budget preferences. "
                "Do NOT re-ask what month or season the event is in since timing is already known! "
                f"1. Acknowledge {event_label} in {timing_label} with expert menswear styling advice: "
                "Recommend appropriate fabrics and timeless color palettes (e.g. for winter Nikah: Worsted Wool or rich Velvet; timeless shades like Ivory, Off-White, Soft Gold). "
                "2. In ONE polite showroom question in the customer's language, ask for their preferences: "
                "what color palette they lean towards (classic light tones vs deep shades) and their approximate budget range, "
                "so you can curate the perfect bespoke options for them. "
                "Do NOT dump products or list prices. Keep it to 2-3 polite sentences."
            ) if timing_known else (
                f"Customer named {event_label}. Do NOT search or dump products this turn! "
                f"1. Acknowledge {event_label} with luxury styling taste and recommend the appropriate garment: "
                "(Walima/Reception -> sharp Suits / Tuxedos; Barat -> royal Sherwani or Prince Coat; Nikkah -> graceful Sherwani; Mehndi -> festive Kurta/Sherwani). "
                "2. Ask for their preferences in ONE polite showroom question in the customer's language covering: "
                "what month/season the event is in, any specific color preference, and approximate budget range. "
                "Do NOT dump products or list prices. Keep it to 2-3 polite sentences."
            ),
            "wedding_date": (
                "Do NOT pitch products. Do NOT search. "
                "Ask for event month/season, colour, or budget preference in one polite question matching the user's language. "
                "No product list. No 'Noted' echo."
            ),
            "product_type": (
                f"For {event_label}: ask which garment they prefer, without inventing types. "
                "Do NOT dump products. Do NOT claim stock. No emojis."
            ),
            "variation": (
                f"Customer wants {profile.product_type or 'this garment'}. "
                f"Suggest ONLY these live variations from the API: {', '.join(variation_names) or 'none'}. "
                "Ask which variation style they prefer (one short question in customer's language). "
                "Do NOT search products yet. Do NOT claim stock."
            ),
            "color": (
                "Ask one preference in customer's language: colour OR style tone. "
                "Do NOT search yet. Do NOT claim stock. Do NOT say 'Noted'."
            ),
            "budget": (
                "Ask ONE short budget question in customer's language. "
                "They may skip — accept and move on. Do NOT dump products."
            ),
        }

        directive = guidance_map.get(
            target_key,
            f"Ask politely for their {target_desc} to personalize recommendations.",
        )
        declined_note = ""
        if profile.declined_slots:
            declined_note = (
                f"Already declined (do not re-ask): {', '.join(profile.declined_slots)}.\n"
            )

        remaining = self.evaluate_entity_gaps(profile, category_variations)
        remaining_note = ""
        if remaining:
            labels = []
            for slot in remaining:
                for chain_key, chain_desc in DISCOVERY_PRIORITY_CHAIN:
                    if chain_key == slot:
                        labels.append(chain_desc)
                        break
            remaining_note = (
                f"Still to collect, one per turn: {', '.join(labels)}.\n"
                f"Ask ONLY the target missing detail this turn. The rest come later.\n"
            )

        return (
            f"\n\n[CONSULTATIVE DISCOVERY MODE ACTIVE]\n"
            f"{declined_note}"
            f"{remaining_note}"
            f"{catalog_block}"
            f"{variations_block}"
            f"{styling_block}"
            f"Target missing detail: '{target_key}' ({target_desc}).\n"
            f"Rule: Strictly match the customer's language (English if user writes in English, Roman Urdu if user writes in Roman Urdu/Urdu). Ask ONLY ONE structured, luxury question.\n"
            f"Directive: {directive}\n"
            f"Do NOT search or list products this turn. Do NOT claim stock/availability.\n"
            f"Do NOT repeat facts they already said (no 'Noted – Nikkah').\n"
            f"Ask ONLY the one target question. No emojis. No lovely/wonderful filler.\n"
        )


discovery_engine = DiscoveryEngine()
