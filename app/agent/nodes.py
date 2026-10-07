from __future__ import annotations

import json
import logging
import re
import functools
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.services.llm import build_chat_model
from app.agent.state import SalesAgentState
from app.agent.prompts import (
    PLANNER_PROMPT,
    SYSTEM_PROMPT,
    DISCOVERY_PLAYBOOK,
    RECOMMENDATION_PLAYBOOK,
    STYLING_PLAYBOOK,
    OBJECTION_PLAYBOOK,
    NEGOTIATION_PLAYBOOK,
    CROSS_SELL_PLAYBOOK,
    CLOSING_PLAYBOOK,
    CUSTOM_FABRIC_PLAYBOOK,
    CUSTOMIZATION_PLAYBOOK,
)
from app.core.logging_config import log_openai_call, safe_len, truncate_text
from app.services.backend_api import (
    BackendAPIError,
    backend_api,
    resolve_backend_asset_url,
    resolve_category_against_catalog,
)
from app.tools.product_tools import search_products, get_product_details
from app.tools.fabric_tools import search_fabrics, get_fabric_details
from app.tools.inventory_tools import check_inventory
from app.tools.cross_sell_tools import suggest_cross_sell
from app.tools.measurement_tools import validate_measurements
from app.tools.handover_tools import create_human_handover

from app.agent.discovery_engine import discovery_engine
from app.services.recommendation_service import select_products_for_display, MAX_PRODUCTS_TO_SHOW
from app.services.negotiation_engine import negotiation_engine
from app.services.styling_rules import derive_season_from_text, map_season_for_catalog_api
from app.services.accessories_service import (
    filter_accessories_for_product,
    filter_free_gift_candidates,
    free_gift_margin_budget,
    qualifies_for_free_accessory,
    resolve_garment_color_for_accessory,
    summarize_accessories_for_prompt,
)
from app.services.currency_service import apply_display_currency_list
from app.services.measurement_collection_service import (
    build_collection_prompt,
    chart_for_category,
    collect_from_message,
    handover_measurements_payload,
    infer_measurement_path,
    measurements_complete,
    product_available_sizes,
    save_measurements_json,
)
from app.services.variations_service import (
    apply_product_variation_to_details,
    filter_variations_for_category,
    needs_product_variation_choice,
    resolve_variation_against_catalog,
    summarize_product_variations_for_prompt,
    summarize_variations_for_prompt,
)
from app.agent.guardrails import guardrails
from app.schemas.profile import CustomerProfileSchema
from app.schemas.negotiation import NegotiationStateSchema
from app.services.custom_design_service import generate_bespoke_design
from app.services.checkout_service import build_checkout_request, is_custom_checkout
from app.services.price_quote_service import public_price_quote, resolve_sellable_price


logger = logging.getLogger(__name__)


async def _refresh_price_quote(state: dict[str, Any]) -> dict[str, Any]:
    """Resolve catalogue/calculator quote; never raise into the graph."""
    try:
        quote = await resolve_sellable_price(dict(state))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "price_quote refresh failed session_id=%s err=%s",
            state.get("session_id"),
            exc,
        )
        return {
            "price_quote": {
                "ok": False,
                "source": "error",
                "error": str(exc),
            },
            "customize_pricing_mode": None,
        }
    mode = quote.get("customize_mode") if isinstance(quote, dict) else None
    profile = dict(state.get("customer_profile") or {})
    emb = (quote or {}).get("embroidery_inference") if isinstance(quote, dict) else None
    if isinstance(emb, dict) and emb.get("embroidery_tier"):
        profile["embroidery_tier"] = emb["embroidery_tier"]
    out: dict[str, Any] = {
        "price_quote": quote,
        "customize_pricing_mode": mode,
    }
    if profile:
        out["customer_profile"] = profile
    return out


def _llm():
    """Fresh ChatOpenAI from the provider factory (no cached client across env switches)."""
    return build_chat_model(temperature=0.2, max_tokens=2048)


def _resolved_model_name() -> str:
    from app.services.ai.factory import AIProviderFactory

    provider = AIProviderFactory.make()
    if provider is None:
        return settings.OPENAI_MODEL
    return getattr(provider, "model_name", None) or settings.OPENAI_MODEL


# Back-compat for tests/monkeypatches that patch `app.agent.nodes.llm`.
llm = None


def _active_llm():
    return llm if llm is not None else _llm()

EXPLICIT_HANDOVER_KEYWORDS = [
    "consultant",
    "human",
    "representative",
    "handover",
    "speak to someone",
    "talk to someone",
    "real person",
    "insaan",
    "baat karwa",
    "baat karwa do",
    "connect kar do",
    "connect kar",
    "consultant se",
    "representative se",
    "agent se baat",
]

OFF_DOMAIN_KEYWORDS = [
    "weather",
    "mausam",
    "cricket",
    "football",
    "joke",
    "recipe",
    "python code",
    "write code",
    "politics",
    "siyasat",
]

PHONE_PATTERNS = [
    re.compile(r"\+92[\s-]?\d{10}"),
    re.compile(r"03\d{2}[\s-]?\d{7}"),
    re.compile(r"\b\d{10,15}\b"),
]

# Only explicit name cues. Never use bare "main" / "... hai" sentence patterns.
NAME_PATTERNS = [
    re.compile(
        r"(?:my name is|name is|mera naam(?:\s+hai)?)\s+"
        r"([A-Za-z][A-Za-z'.-]+(?:\s+[A-Za-z][A-Za-z'.-]+){0,2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:i am|i'm)\s+([A-Za-z][A-Za-z'.-]+(?:\s+[A-Za-z][A-Za-z'.-]+)?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^([A-Za-z][A-Za-z'.-]+(?:\s+[A-Za-z][A-Za-z'.-]+){0,2})\s*,\s*(?:\+?\d|0\d)",
        re.IGNORECASE,
    ),
    re.compile(r"name:\s*([A-Za-z][A-Za-z'.-]+(?:\s+[A-Za-z][A-Za-z'.-]+){0,2})", re.IGNORECASE),
]

KV_PHONE_PATTERN = re.compile(r"phone:\s*([\d\s+-]+)", re.IGNORECASE)
KV_WHATSAPP_PATTERN = re.compile(r"whatsapp:\s*([\d\s+-]+)", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Reject conversational / product words falsely captured as person names.
NAME_STOPWORDS = {
    "hai",
    "hain",
    "main",
    "mein",
    "daylight",
    "evening",
    "morning",
    "night",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "valima",
    "walima",
    "nikah",
    "nikkah",
    "barat",
    "baraat",
    "mehndi",
    "sherwani",
    "suit",
    "suits",
    "prince",
    "available",
    "consultant",
    "discount",
    "stock",
    "size",
    "small",
    "medium",
    "large",
    "price",
    "product",
    "budget",
    "help",
    "hello",
    "hi",
    "yes",
    "no",
    "thanks",
    "thank",
    "please",
    "kar",
    "kr",
    "kya",
    "nahi",
    "acha",
    "theek",
    "chahiye",
    "chahye",
    "day",
    "date",
    "wedding",
    "shadi",
    "preparing",
    "looking",
    "searching",
    "event",
    "for",
    "my",
    "the",
    "assalam",
    "assalamo",
    "asalam",
    "salaam",
    "alaikum",
    "walaikum",
    "walekum",
    "salam",
    "light",
    "color",
    "colour",
    "colors",
    "colours",
    "dark",
    "shade",
    "shades",
    "tone",
    "tones",
    "normal",
    "expensive",
    "urgent",
    "rate",
    "cost",
    "batao",
    "bataen",
    "batae",
    "dikhao",
    "dekhao",
    "zada",
    "zyada",
    "kam",
    "cheap",
    "sasta",
    "only",
    "dress",
    "dresses",
    "cloth",
    "clothes",
}


def _is_plausible_person_name(value: str | None) -> bool:
    """Accept only short personal names; reject sentence fragments."""
    if not value:
        return False
    cleaned = re.sub(r"\s+", " ", str(value)).strip(" .,'\"")
    if len(cleaned) < 2 or len(cleaned) > 40:
        return False
    words = cleaned.split()
    if not (1 <= len(words) <= 3):
        return False
    for word in words:
        lower = word.lower().strip(".'-")
        if not lower.isalpha():
            return False
        if lower in NAME_STOPWORDS:
            return False
        if len(lower) < 2:
            return False
    # Whole-string junk like "hai daylight main"
    if any(token in cleaned.lower().split() for token in ("hai", "main", "mein", "daylight")):
        return False
    return True


def _safe_json_loads(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise


def _log_state_summary(state: SalesAgentState) -> str:
    return (
        f"session_id={state.get('session_id')} intent={state.get('intent')} "
        f"event_type={state.get('event_type')} product_type={state.get('product_type')} "
        f"color={state.get('color')} size={state.get('size')} "
        f"quantity={state.get('quantity')} budget={state.get('budget')}"
    )


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "n/a"
    digits = re.sub(r"\D", "", phone)
    if len(digits) <= 4:
        return "****"
    return f"***{digits[-4:]}"


def _extract_contact_regex(text: str, is_handover: bool = False) -> dict[str, str | None]:
    phone: str | None = None
    whatsapp: str | None = None

    kv_phone = KV_PHONE_PATTERN.search(text)
    if kv_phone:
        phone = re.sub(r"[\s-]", "", kv_phone.group(1))

    kv_whatsapp = KV_WHATSAPP_PATTERN.search(text)
    if kv_whatsapp:
        whatsapp = re.sub(r"[\s-]", "", kv_whatsapp.group(1))

    if not phone:
        for pattern in PHONE_PATTERNS:
            match = pattern.search(text)
            if match:
                phone = re.sub(r"[\s-]", "", match.group())
                break

    name: str | None = None
    for pattern in NAME_PATTERNS:
        match = pattern.search(text.strip())
        if match:
            candidate = match.group(1).strip(" .,'")
            # Strip leading/trailing Urdu copulas if regex still grabbed them
            candidate = re.sub(r"^(hai|hain)\s+", "", candidate, flags=re.IGNORECASE).strip()
            candidate = re.sub(r"\s+(hai|hain|hoon|hun)$", "", candidate, flags=re.IGNORECASE).strip()
            if _is_plausible_person_name(candidate):
                name = candidate
                break

    # Bare name is ONLY acceptable in active handover context where assistant specifically asked for name
    if not name and is_handover:
        stripped = text.strip()
        words = stripped.split()
        if 1 <= len(words) <= 2 and all(word.replace("-", "").isalpha() for word in words):
            candidate = " ".join(words)
            if _is_plausible_person_name(candidate):
                name = candidate

    email_match = EMAIL_PATTERN.search(text)
    email = email_match.group(0) if email_match else None

    return {
        "name": name,
        "phone": phone,
        "whatsapp": whatsapp or phone,
        "email": email,
    }



def _merge_customer_contact(
    existing: dict[str, Any] | None,
    planner: dict[str, Any] | None,
    regex: dict[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(existing or {})
    # Drop previously stored garbage names (e.g. sentence fragments)
    if merged.get("name") and not _is_plausible_person_name(str(merged.get("name"))):
        merged.pop("name", None)

    for key in ("name", "phone", "whatsapp", "email"):
        regex_val = (regex or {}).get(key)
        if not regex_val or merged.get(key):
            continue
        if key == "name" and not _is_plausible_person_name(str(regex_val)):
            continue
        merged[key] = regex_val

    for key in ("name", "phone", "whatsapp", "email"):
        planner_val = (planner or {}).get(key)
        if not planner_val:
            continue
        if key == "name" and not _is_plausible_person_name(str(planner_val)):
            continue
        merged[key] = planner_val

    if merged.get("phone") and not merged.get("whatsapp"):
        merged["whatsapp"] = merged["phone"]
    elif merged.get("whatsapp") and not merged.get("phone"):
        merged["phone"] = merged["whatsapp"]

    return merged


def _missing_handover_fields(contact: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not contact.get("name"):
        missing.append("name")
    if not (contact.get("phone") or contact.get("whatsapp")):
        missing.append("phone")
    return missing


def _explicit_handover_requested(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in EXPLICIT_HANDOVER_KEYWORDS)


def _handover_requested_in_history(messages: list[dict[str, str]], limit: int = 10) -> bool:
    user_messages = [m for m in messages if m.get("role") == "user"][-limit:]
    return any(_explicit_handover_requested(m.get("content", "")) for m in user_messages)


def _summarize_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "product_id": product.get("product_id"),
            "name": product.get("name"),
            "color": product.get("color") or product.get("primary_color"),
        }
        for product in products
    ]


def _build_planner_input(
    state: SalesAgentState,
    catalog_categories: list[dict[str, Any]] | None = None,
    catalog_variations: list[dict[str, Any]] | None = None,
) -> str:
    messages = state.get("messages") or []
    recent_messages = messages[-8:]
    contact = state.get("customer_contact") or {}
    live_cats = [
        {"name": str(c.get("name") or "").strip(), "product_count": int(c.get("product_count") or 0)}
        for c in (catalog_categories or state.get("catalog_categories") or [])
        if str(c.get("name") or "").strip() and int(c.get("product_count") or 0) > 0
    ]
    product_type = state.get("product_type")
    category_variations = filter_variations_for_category(
        catalog_variations or state.get("catalog_variations") or [],
        product_type,
    )
    variation_summaries = summarize_variations_for_prompt(
        category_variations or (catalog_variations or [])[:20]
    )
    session_context = {
        "selected_product_id": state.get("selected_product_id"),
        "products": _summarize_products(state.get("products") or []),
        "event_type": state.get("event_type"),
        "product_type": product_type,
        "selected_variation_id": state.get("selected_variation_id"),
        "selected_variation_name": state.get("selected_variation_name"),
        "selected_product_variation_id": state.get("selected_product_variation_id"),
        "selected_product_variation_name": state.get("selected_product_variation_name"),
        "sales_stage": state.get("sales_stage"),
        "buying_intent": state.get("buying_intent"),
        "negotiation_round": (state.get("negotiation_state") or {}).get("round_number") if isinstance(state.get("negotiation_state"), dict) else None,
        "negotiation_last_action": (state.get("negotiation_state") or {}).get("last_action") if isinstance(state.get("negotiation_state"), dict) else None,
        "color": state.get("color"),
        "size": state.get("size"),
        "measurement_path": state.get("measurement_path"),
        "customization_stage": state.get("customization_stage"),
        "cut_style": state.get("cut_style"),
        "selected_fabric_catalog_code": state.get("selected_fabric_catalog_code"),
        "custom_image_url": bool(state.get("custom_image_url")),
        "handover_pending": bool(state.get("handover_pending")),
        "customer_contact": {
            "name_present": bool(contact.get("name")),
            "phone_present": bool(contact.get("phone") or contact.get("whatsapp")),
            "email_present": bool(contact.get("email")),
        },
        "catalog_categories": live_cats,
        "category_variations": summarize_variations_for_prompt(category_variations),
        "shown_product_ids": list(state.get("shown_product_ids") or []),
    }
    history_lines = [
        f"{message.get('role', 'unknown')}: {truncate_text(message.get('content', ''), max_len=300)}"
        for message in recent_messages
    ]
    cat_names = [c["name"] for c in live_cats]
    category_rule = (
        f"Live catalog category names (product_type MUST be one of these exact strings, or null): "
        f"{cat_names}. Map suit/tux/sherwani/prince coat etc. to the closest name. Do not invent."
        if cat_names
        else "catalog_categories unavailable this turn; leave product_type null unless already in session."
    )
    var_names = [str(v.get("name")) for v in variation_summaries if v.get("name")]
    variation_rule = (
        f"Live category variations (dynamic API — never invent): {var_names}. "
        f"If the user names a style (double breast, three piece, achkan, embroidered, tuxedo…), "
        f"set selected_variation_name to the closest EXACT name from this list (or null). "
        f"CRITICAL: When the customer asks to SEE options / dikhao / show / catalog pieces, "
        f"ALWAYS include search_products immediately — do NOT delay for variation choice. "
        f"Never narrate Achkan/Maharaja/Embroidered as separate products in text; cards show pieces. "
        f"If they ask for options BESIDES a garment (elawa / ilawa / besides / other than), "
        f"switch product_type to a complementary category (e.g. Sherwani elawa → Prince Coat) and search."
        if var_names
        else (
            "category_variations empty for current product_type (or product_type unknown). "
            "Do not invent variation types. On dikhao/options asks, still search_products."
        )
    )
    shown_ids = list(state.get("shown_product_ids") or [])
    shown_rule = (
        f"Already shown product IDs in this session: {shown_ids}. "
        "If customer asks for more options / 'aur dikhao' / 'show more', trigger search_products. "
        "If pieces in the current category may be exhausted, consider whether broadening to a complementary "
        "ceremonial category appropriate for the event (e.g. Prince Coat or Sherwani for Nikkah/Barat/Mehndi) makes sense. "
        "NEVER suggest inappropriate items (e.g. no business suits for Barat). "
        "Or keep product_type to show further unseen pieces or offer bespoke custom design.\n\n"
        if shown_ids
        else ""
    )
    return (
        f"Session context:\n{json.dumps(session_context, indent=2)}\n\n"
        f"{category_rule}\n\n"
        f"{variation_rule}\n\n"
        f"{shown_rule}"
        f"Recent conversation:\n" + "\n".join(history_lines) + "\n\n"
        f"Current user message:\n{state['user_message']}"
    )


def _resolve_product_image_url(state: SalesAgentState) -> str:
    if state.get("custom_image_url"):
        return state.get("custom_image_url")
    if isinstance(state.get("custom_design_result"), dict) and state.get("custom_design_result", {}).get("image_url"):
        return state.get("custom_design_result", {}).get("image_url")

    # In customization stage when no bespoke image was generated yet, do NOT attach catalog image
    if state.get("sales_stage") == "customization" and not state.get("custom_image_url"):
        return ""

    products = state.get("products") or []
    selected_product_id = state.get("selected_product_id")
    product_details = state.get("product_details") or {}
    image_path: str | None = None

    if selected_product_id and isinstance(product_details, dict):
        if str(product_details.get("product_id")) == str(selected_product_id):
            image_path = product_details.get("image_url")

    if not image_path and selected_product_id:
        for product in products:
            if str(product.get("product_id")) == str(selected_product_id):
                image_path = product.get("image_url")
                break

    if not image_path and products:
        image_path = products[0].get("image_url")

    return resolve_backend_asset_url(image_path)



def _handover_facts_for_llm(contact: dict[str, Any]) -> dict[str, Any]:
    """Facts only — final LLM writes confirmation in the customer's language."""
    name = (contact.get("name") or "").strip()
    phone = contact.get("phone") or contact.get("whatsapp") or ""
    return {
        "handover_created": True,
        "customer_name": name or None,
        "customer_phone": phone or None,
        "instruction": (
            "Confirm that a Style Consultant will contact them, using this exact name and phone. "
            "Write the entire confirmation in the SAME language as the current user message "
            "(Roman Urdu / Hinglish / English). Do not invent contact details."
        ),
    }


_NEGOTIATION_LLM_STRIP_KEYS = frozenset({"margin_budget", "floor_price", "wholesale_cost", "cost_price"})


def _public_negotiation_result_for_llm(neg: Any) -> Any:
    """Strip internal margin/floor fields so the final LLM never sees leakable tokens."""
    if not isinstance(neg, dict):
        return neg
    out: dict[str, Any] = {
        k: v for k, v in neg.items() if k not in _NEGOTIATION_LLM_STRIP_KEYS
    }
    strategy = out.get("strategy")
    if isinstance(strategy, dict):
        out["strategy"] = {
            k: v for k, v in strategy.items() if k not in _NEGOTIATION_LLM_STRIP_KEYS
        }
    return out


def _build_conversation_summary(
    state: SalesAgentState,
    messages: list[dict[str, str]],
) -> str:
    sentences: list[str] = []

    request_parts = [
        p
        for p in [
            state.get("event_type"),
            state.get("color"),
            state.get("product_type"),
            f"size {state.get('size')}" if state.get("size") else None,
        ]
        if p
    ]
    if request_parts:
        sentences.append(f"Customer inquiry: {', '.join(str(p) for p in request_parts)}.")
    elif state.get("intent"):
        sentences.append(f"Customer intent: {state.get('intent')}.")

    products = state.get("products") or []
    if products:
        sentences.append(f"Product search returned {len(products)} option(s).")

    inventory = state.get("inventory_result")
    if inventory is not None:
        available = inventory.get("available")
        sentences.append(
            f"Inventory check: {'available' if available else 'not available'}."
        )

    negotiation = state.get("negotiation_result")
    if negotiation is not None:
        approved = negotiation.get("approved")
        sentences.append(f"Negotiation: {'approved' if approved else 'not approved'}.")

    measurement = state.get("measurement_result")
    if measurement is not None:
        sentences.append(f"Measurement validation: {measurement.get('status', 'checked')}.")

    user_messages = [
        truncate_text(m.get("content", ""), max_len=100)
        for m in messages
        if m.get("role") == "user"
    ][-3:]
    if user_messages:
        sentences.append("Recent messages: " + " | ".join(user_messages))

    if state.get("handover_reason") == "cannot_proceed":
        sentences.append("AI could not proceed; handover requested.")
    else:
        sentences.append("Customer requested human consultant.")

    return " ".join(sentences)[:800]


def _normalize_event_type_label(value: str | None) -> str | None:
    """Canonical display labels for discovery/state (Nikah, Barat, …)."""
    if not value:
        return None
    key = str(value).strip().lower().replace(" ", "_")
    mapping = {
        "nikah": "Nikah",
        "nikkah": "Nikah",
        "barat": "Barat",
        "baraat": "Barat",
        "walima": "Walima",
        "valima": "Walima",
        "mehndi": "Mehndi",
        "mehendi": "Mehndi",
        "mehandi": "Mehndi",
        "engagement": "Engagement",
        "reception": "Reception",
    }
    if key in mapping:
        return mapping[key]
    cleaned = str(value).strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else None


def _default_category_for_event(event_type: str | None) -> str | None:
    """Event → default garment category for catalogue / fabric dress_category filter."""
    ev = str(event_type or "").lower()
    if not ev:
        return None
    if any(x in ev for x in ("barat", "baraat", "nikah", "nikkah", "mehndi", "mehendi", "mehandi")):
        return "Sherwani"
    if any(x in ev for x in ("walima", "valima", "reception")):
        return "Suits"
    if "engagement" in ev:
        return "Prince Coat"
    return None


def _normalize_wedding_date(value: str | None) -> str | None:
    """Collapse 'mid of june' / 'June 2026' → 'June' (or season word)."""
    if not value:
        return None
    text = str(value).strip().lower()
    months = (
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    )
    for month in months:
        if month in text:
            return month.title()
    for season in ("winter", "summer", "autumn", "spring", "fall"):
        if season in text:
            return "autumn" if season == "fall" else season
    return str(value).strip()


def _asks_besides_category(user_message: str) -> str | None:
    """If customer asks for options *besides* a named garment, return that excluded category."""
    text = (user_message or "").lower()
    if not text:
        return None
    besides_cues = (
        "elawa", "ilawa", "alaawa", "besides", "other than", "instead of",
        "ke badle", "ke bajaye", "ke siwa", "ke siva", "apart from",
    )
    if not any(c in text for c in besides_cues):
        return None
    if "sherwani" in text:
        return "Sherwani"
    if "prince coat" in text or "princecoat" in text or (
        "prince" in text and "coat" in text
    ):
        return "Prince Coat"
    if "suit" in text or "tuxedo" in text:
        return "Suits"
    return None


def _complementary_category_for_event(
    excluded: str,
    event_type: str | None = None,
) -> str:
    """Pick a sensible alternate ready-to-wear category when customer excludes one."""
    excluded_l = (excluded or "").lower()
    event_l = (event_type or "").lower()
    if "sherwani" in excluded_l:
        return "Prince Coat"
    if "prince" in excluded_l:
        return "Sherwani"
    if "suit" in excluded_l or "tux" in excluded_l:
        if any(e in event_l for e in ("nikah", "nikkah", "barat", "baraat", "mehndi")):
            return "Sherwani"
        return "Prince Coat"
    # Default ceremonial alternate
    if any(e in event_l for e in ("walima", "valima", "reception")):
        return "Suits"
    return "Prince Coat"


def _message_explicitly_names_garment(
    user_message: str,
    catalog_categories: list[dict[str, Any]] | None = None,
) -> str | None:
    """Return catalog category only if the user named a garment in this message.
    
    Note: "sherwani k elawa" exclusion is now handled by LLM planner (exclude_category field).
    """
    text = (user_message or "").lower()
    if not text:
        return None
    # Prefer live catalog names (allow plural: "prince coats" ↔ "Prince coat")
    for cat in catalog_categories or []:
        name = str(cat.get("name") or "").strip()
        if not name:
            continue
        low = name.lower()
        if low in text:
            return name
        # Plural / spacing: "prince coat" inside "prince coats"
        compact = re.sub(r"\s+", " ", low)
        if compact and re.search(rf"\b{re.escape(compact)}s?\b", text):
            return name
    if "sherwani" in text:
        return "Sherwani"
    if "prince coat" in text or "princecoat" in text or re.search(r"\bprince\s+coats?\b", text):
        return "Prince Coat"
    if "tuxedo" in text or re.search(r"\bsuits?\b", text):
        return "Suits"
    return None


def _is_fresh_category_browse(
    user_message: str,
    catalog_categories: list[dict[str, Any]] | None = None,
) -> bool:
    """True when customer asks to SEE a garment category (not a named SKU follow-up)."""
    if not discovery_engine.customer_asked_for_catalog(user_message):
        return False
    return bool(_message_explicitly_names_garment(user_message, catalog_categories))


def _looks_roman_urdu(text: str) -> bool:
    t = (text or "").lower()
    cues = (
        "hai", "hain", "dikhao", "dekhao", "mjhay", "mujhe", "k liye", "ke liye",
        "aur", "koi", "options", "yeh", "yah", "chahiye", "pasand", "janab",
    )
    return sum(1 for c in cues if c in t) >= 2


def _short_product_card_intro(user_message: str, category: str | None) -> str:
    cat = (category or "atelier").strip() or "atelier"
    if _looks_roman_urdu(user_message):
        return f"Yeh hain hamare {cat} options:"
    return f"Here are our {cat} pieces:"


def _force_short_product_reply(
    reply: str,
    user_message: str,
    recommendations: list[dict[str, Any]],
) -> str:
    """When product cards are present, never let the LLM ship a long story."""
    if not recommendations:
        return reply
    text = (reply or "").strip()
    if not text:
        cat = str(recommendations[0].get("category") or "atelier")
        return _short_product_card_intro(user_message, cat)

    words = text.split()
    # Price / fabric essays, multi-paragraph, or long replies → replace.
    verbose_markers = (
        "£", "$", "pkr", "fabric", "worsted", "season", "available in",
        "maujood", "mein available", "super 120", "achkan", "maharaja",
        "embroidered", "emboridered", "signature cut", "ji janab",
    )
    has_verbose = any(m in text.lower() for m in verbose_markers)
    multi_para = text.count("\n") >= 2 or text.count(".") >= 3
    if len(words) <= 22 and not has_verbose and not multi_para:
        return text

    cat = str(
        recommendations[0].get("category")
        or recommendations[0].get("category_name")
        or "atelier"
    )
    return _short_product_card_intro(user_message, cat)


_MEASUREMENT_ASK_MARKERS = (
    "measurement",
    "measurements",
    "standard size",
    "body measurements",
    "chest",
    "waist",
    "shoulder",
    "sleeve",
    "jacket length",
    "naap",
    "size chart",
    "inches mein",
    "size pasand",
    "aapki measurements",
    "body measurement",
)


def _asks_for_measurements(reply: str) -> bool:
    text = (reply or "").lower()
    return any(m in text for m in _MEASUREMENT_ASK_MARKERS)


def _force_checkout_handoff_reply(
    reply: str,
    *,
    user_message: str,
    checkout_url: str | None,
) -> str:
    """When checkout is ready, never let the LLM pivot back to chat measurements."""
    if not checkout_url:
        return reply
    if not _asks_for_measurements(reply):
        return reply
    urdu = _looks_roman_urdu(user_message)
    if urdu:
        return (
            "Zabardast, Janab! Yeh design aapke liye ready hai. "
            "Size aur measurements checkout page par fill ho jayengi — "
            f"ab secure checkout open karein: {checkout_url}"
        )
    return (
        "Wonderful — this bespoke design is ready for you. "
        "Size and measurements are collected on the checkout page — "
        f"please proceed to secure checkout: {checkout_url}"
    )


def _extract_explicit_color(text: str) -> str | None:
    """Only colours or tones the customer actually named — never invent from advice."""
    lowered = (text or "").lower()
    # Longest / most specific first
    palette = (
        ("midnight blue", "Midnight Blue"),
        ("champagne", "Champagne"),
        ("off white", "Off-White"),
        ("off-white", "Off-White"),
        ("light color", "Light"),
        ("light colour", "Light"),
        ("light colors", "Light"),
        ("light colours", "Light"),
        ("lights color", "Light"),
        ("lights colour", "Light"),
        ("light shade", "Light"),
        ("light tone", "Light"),
        ("light tones", "Light"),
        ("dark color", "Dark"),
        ("dark colour", "Dark"),
        ("dark shade", "Dark"),
        ("dark tone", "Dark"),
        ("dark tones", "Dark"),
        ("soft color", "Soft"),
        ("soft tone", "Soft"),
        ("halka rang", "Light"),
        ("halka color", "Light"),
        ("halka colour", "Light"),
        ("halka", "Light"),
        ("lights", "Light"),
        ("maroon", "Maroon"),
        ("burgundy", "Burgundy"),
        ("emerald green", "Emerald Green"),
        ("emerald", "Emerald"),
        ("bottle green", "Green"),
        ("mehendi green", "Green"),
        ("ivory", "Ivory"),
        ("cream", "Cream"),
        ("beige", "Beige"),
        ("navy blue", "Navy"),
        ("navy", "Navy"),
        ("royal blue", "Blue"),
        ("sky blue", "Blue"),
        ("rose gold", "Gold"),
        ("dull gold", "Gold"),
        ("golden", "Gold"),
        ("gold", "Gold"),
        ("silver", "Silver"),
        ("black", "Black"),
        ("white", "White"),
        ("green", "Green"),
        ("grey", "Grey"),
        ("gray", "Grey"),
        ("red", "Red"),
        ("blue", "Blue"),
        ("rust", "Rust"),
        ("peach", "Peach"),
        ("olive", "Olive"),
        ("charcoal", "Charcoal"),
        ("pastel", "Pastel"),
        ("earthy", "Earthy"),
        ("light", "Light"),
        ("dark", "Dark"),
    )
    for needle, label in palette:
        if re.search(rf"\b{re.escape(needle)}\b", lowered):
            return label
    return None



def _is_explicit_checkout_request(user_message: str) -> bool:
    """Checkout URL only at the end — explicit buy/pay/order/link ask."""
    text = (user_message or "").lower().strip()
    if not text:
        return False
    cues = (
        "checkout",
        "check out",
        "buy now",
        "purchase",
        "order kar",
        "order karo",
        "place order",
        "le lunga",
        "le leti",
        "le lo",
        "lena hai final",
        "finalize",
        "finalise",
        "final kar",
        "confirm order",
        "payment",
        "pay kar",
        "link bhejo",
        "link do",
        "url do",
        "product link",
        "checkout link",
        "khareed",
        "kharid",
        "book kar do",
        "reserve kar",
        "done order",
    )
    return any(cue in text for cue in cues)




def _is_product_detail_request(user_message: str) -> bool:
    """True when customer asks for details/price of a specific named piece (not browse)."""
    text = (user_message or "").lower().strip()
    if not text:
        return False
    # Explicit catalog browse must stay as search — not detail.
    if discovery_engine.customer_asked_for_catalog(user_message):
        return False
    if discovery_engine.customer_asked_for_guidance(user_message):
        return False
    strong = (
        "tell me more",
        "more details",
        "more about",
        "details about",
        "details for",
        "complete details",
        "full details",
        "variations of",
        "detail of",
        "details of",
    )
    if any(c in text for c in strong):
        return True
    price_cues = ("rate", "price", "kitna", "kitne", "cost", "kitni")
    if any(c in text for c in price_cues):
        return True
    if "detail" in text or "details" in text:
        return True
    return False

def _is_piece_or_colour_confirm(user_message: str) -> bool:
    """Customer showing interest in a piece/colour — select it; do NOT checkout yet."""
    if discovery_engine.customer_asked_availability(user_message):
        return False
    if discovery_engine.customer_asked_for_catalog(user_message):
        return False
    if _is_explicit_checkout_request(user_message):
        return False
    text = (user_message or "").lower().strip()
    if not text:
        return False
    interest_cues = (
        "okay",
        "ok",
        "theek",
        "chahiye",
        "pasand",
        "nice",
        "acha",
        "achha",
        "achhi",
        "like",
        "liked",
        "interested",
        "ye wala",
        "yehi",
        "wahi",
        "this one",
        "isko",
        "mujhe ye",
        "i like",
        "looks good",
        "dekhna hai",
        "details",
        "batao",
    )
    color = _extract_explicit_color(text)
    if color and any(cue in text for cue in interest_cues):
        return True
    if color and len(text.split()) <= 4:
        return True
    if any(cue in text for cue in ("ye wala", "yehi", "this one", "isko le", "ye le", "pasand", "nice", "i like")):
        return True
    return False


_LIKE_CONFIRM_CUES = (
    "ye wala",
    "yehi",
    "this one",
    "isko le",
    "ye le",
    "pasand",
    "nice",
    "i like",
    "liked",
    "looks good",
    "looks nice",
    "love it",
    "love this",
    "perfect",
    "mjhay ye",
    "mujhe ye",
    "mujhay ye",
    "ye pasand",
    "thek hai",
    "theek hai",
    "thek he",
    "theek he",
    "acha lag",
    "achha lag",
    "accha lag",
    "lag raha",
    "lag rahi",
    "lagraha",
    "lagrahi",
    "mjhay acha",
    "mujhe acha",
    "bohot acha",
    "zabardast",
)


def _is_like_confirm(user_message: str) -> bool:
    """Customer affirming the currently shown/selected piece — not a new catalog browse."""
    if discovery_engine.customer_asked_availability(user_message):
        return False
    if _is_explicit_checkout_request(user_message):
        return False
    text = (user_message or "").lower().strip()
    if not text:
        return False
    if discovery_engine.customer_asked_for_catalog(user_message):
        # "ye pasand hai, aur dikhao" is still a catalog ask.
        if any(cue in text for cue in ("dikhao", "dekhao", "show me", "show more", "aur options", "kuch aur")):
            return False
    return any(cue in text for cue in _LIKE_CONFIRM_CUES)


def _negotiation_last_action(state: SalesAgentState | dict[str, Any] | None) -> str:
    session = state or {}
    neg_state = session.get("negotiation_state") if isinstance(session.get("negotiation_state"), dict) else {}
    prev = session.get("negotiation_result") if isinstance(session.get("negotiation_result"), dict) else {}
    strategy = prev.get("strategy") if isinstance(prev.get("strategy"), dict) else {}
    return str(
        (neg_state or {}).get("last_action")
        or prev.get("action")
        or strategy.get("action")
        or ""
    ).lower()


def _negotiation_awaiting_color(state: SalesAgentState | dict[str, Any] | None) -> bool:
    return _negotiation_last_action(state) == "ask_color_preference"


def _in_active_negotiation(state: SalesAgentState | dict[str, Any] | None) -> bool:
    session = state or {}
    try:
        round_n = int((session.get("negotiation_state") or {}).get("round_number") or 0)
    except (TypeError, ValueError):
        round_n = 0
    return round_n >= 1 or session.get("sales_stage") == "negotiation"


def _is_product_variation_inquiry(text: str) -> bool:
    t = (text or "").lower()
    return any(
        kw in t
        for kw in (
            "variation",
            "variations",
            "variant",
            "variants",
            "is piece ki variation",
            "is product ki variation",
            "isme konsi variation",
            "isme kya variation",
            "is design mein",
            "options kya hain",
            "kya options hain",
            "aur options",
            "aur variations",
            "color options",
            "colour options",
        )
    )


def _has_customization_details(
    user_message: str,
    selected_product_id: str | None = None,
    selected_fabric: str | None = None,
    base_product: dict[str, Any] | None = None,
) -> bool:
    """
    Determines whether the user has provided concrete customization specifications
    (fabric swatch/URL, specific colors, fabrics, or styling changes) to generate
    a bespoke mockup, or if they are just inquiring and need their preferences gathered.
    """
    msg_l = (user_message or "").lower()

    # 1. Direct image/fabric URL or uploaded swatch
    if re.search(r'https?://[^\s<>"]+\b', user_message):
        return True
    if selected_fabric:
        return True

    # 2. Extract base product attributes to avoid false positive triggers from product title (e.g. "Blue" in "Blue Nawab")
    base_name = str(base_product.get("name") or "").lower().strip() if base_product else ""
    base_fabric = str(base_product.get("fabric") or "").lower().strip() if base_product else ""
    base_title_words = set(re.findall(r'[a-z\-]+', f"{base_name} {base_fabric}"))

    # Remove the full base product name from message to prevent matching its own words
    cleaned_msg = msg_l
    if base_name:
        cleaned_msg = cleaned_msg.replace(base_name, " ")

    # 3. Detect inquiry questions asking about customization feasibility
    inquiry_patterns = (
        r"\bcan (we|i|you)\b",
        r"\bcould (we|i|you)\b",
        r"\bwould it be possible\b",
        r"\bis it possible\b",
        r"\bis customization (possible|available)\b",
        r"\bcustomization (possible|ho sakti|available|hoti hai)\b",
        r"\bkya (yeh |ye |is(ka|ki|me) )?(customize|change|possible|ho sakti|kar sakte)\b",
        r"\bcustomize (kar sakte|ho sakta|ho sakti|hota hai)\b",
        r"\bchange (kar sakte|ho sakta|ho sakti)\b",
        r"\bpossible to (customize|change|modify)\b",
        r"\bhow to customize\b",
        r"\bdo you customize\b",
    )
    is_inquiry = any(re.search(pat, msg_l) for pat in inquiry_patterns)

    # 4. Specific fabrics (excluding base product fabric/words)
    specific_fabrics = (
        "velvet", "raw silk", "silk", "worsted", "wool", "brocade", "jamawar",
        "banarsi", "karandi", "linen", "cotton", "chiffon", "organza", "tissue", "satin"
    )
    available_fabrics = [f for f in specific_fabrics if f not in base_title_words]
    has_fabric = any(re.search(rf"\b{re.escape(f)}\b", cleaned_msg) for f in available_fabrics)

    # 5. Specific colors (excluding base product title colors, e.g. "blue" in "Blue Nawab")
    colors = (
        "navy", "blue", "black", "maroon", "green", "emerald", "white", "off-white",
        "ivory", "cream", "red", "gold", "golden", "silver", "grey", "gray", "burgundy",
        "peach", "pink", "brown", "charcoal", "teal", "lilac", "purple", "rust",
        "magenta", "fuchsia", "olive", "champagne", "bronze", "copper", "beige", "mustard"
    )
    available_colors = [c for c in colors if c not in base_title_words]
    words = set(re.findall(r'[a-z\-]+', cleaned_msg))
    has_color = any(c in words for c in available_colors)

    # 6. Specific styling / cuts / craftsmanship cues (excluding generic words like "embroidery" or "color")
    specific_style_cues = (
        "zardozi", "resham", "dabka", "gota", "tilla", "sequin", "threadwork",
        "lighter embroidery", "heavy embroidery", "halka kaam", "kam kaam", "heavy work",
        "kam embroidery", "without embroidery", "no embroidery", "sleeveless",
        "mandarin collar", "tuxedo style", "double breasted", "double breast",
        "peak lapel", "shawl lapel", "angrakha", "achkan cut", "bandhgala", "sherwani collar",
        "minimal", "minimalist", "less embroidery", "light embroidery", "not heavy", "not so heavy",
        "simple", "subtle", "plain", "less work", "halka embroidery"
    )
    has_style = any(s in cleaned_msg for s in specific_style_cues)

    # 7. Check if user specified concrete values or explicit customization details
    if "specifically:" in cleaned_msg:
        spec_part = cleaned_msg.split("specifically:", 1)[1].strip()
        if len(spec_part) > 2 and not spec_part.startswith("can we"):
            return True
    if is_inquiry:
        # If it's an inquiry, only trigger image generation if they provided specific replacement specs
        # e.g., "Can we customize this in emerald green with zardozi work?" -> True
        # but "Can we customize its color and embroidery?" -> False (inquiry about feasibility)
        return bool(has_color or has_fabric or has_style)

    # Modifying a selected piece
    if selected_product_id:
        if has_color or has_fabric or has_style:
            return True
        if has_color and any(w in words for w in ("change", "dusre", "dusra", "replace", "instead")):
            return True
        return False

    # Upfront bespoke (no base product selected yet)
    if has_fabric or (has_color and has_style):
        return True
    if "instead of" in cleaned_msg or ("chahiye" in cleaned_msg and (has_color or has_style)):
        return True
    if has_color and any(w in words for w in ("zardozi", "resham", "tilla", "cut", "collar", "lapel")):
        return True

    return False


def _extract_cut_style(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    pairs = (
        ("angrakha", "Angrakha"),
        ("bandhgala", "Bandhgala"),
        ("achkan", "Achkan"),
        ("prince coat", "Prince Coat"),
        ("double breast", "Double Breasted"),
        ("tuxedo", "Tuxedo"),
        ("peak lapel", "Peak Lapel"),
        ("shawl lapel", "Shawl Lapel"),
        ("mandarin", "Mandarin Collar"),
        ("sherwani collar", "Sherwani Collar"),
        ("three piece", "Three Piece"),
        ("3 piece", "Three Piece"),
        ("two piece", "Two Piece"),
        ("2 piece", "Two Piece"),
        ("sherwani", "Sherwani"),
        ("kurta", "Kurta"),
        ("waistcoat", "Waistcoat"),
        ("indo western", "Indo-Western"),
        ("indo-western", "Indo-Western"),
    )
    for needle, label in pairs:
        if needle in low:
            return label
    return None


def _match_fabric_from_message(
    message: str,
    fabrics: list[dict[str, Any]],
) -> dict[str, Any] | None:
    text = (message or "").lower().strip()
    if not text or not fabrics:
        return None
    # Explicit choose / catalog code from fabric card CTA
    code_m = re.search(
        r"(?:catalog(?:_code)?|code|#[\s]*)[:\s#]*([a-z0-9\-]{3,})",
        text,
        flags=re.IGNORECASE,
    )
    if code_m:
        want = code_m.group(1).lower()
        for fabric in fabrics:
            code = str(fabric.get("catalog_code") or "").lower().strip()
            if code and (code == want or code.endswith(want) or want in code):
                return fabric
    best: tuple[int, dict[str, Any]] | None = None
    for fabric in fabrics:
        name = str(fabric.get("name") or "").lower().strip()
        code = str(fabric.get("catalog_code") or "").lower().strip()
        if code and len(code) >= 3 and code in text:
            return fabric
        if not name or len(name) < 3:
            continue
        if name in text:
            score = len(name)
            if best is None or score > best[0]:
                best = (score, fabric)
            continue
        # Token overlap for longer fabric titles (e.g. "Parchment Solid Linen")
        tokens = [t for t in re.split(r"[^a-z0-9]+", name) if len(t) > 2]
        if not tokens:
            continue
        hits = sum(1 for t in tokens if t in text)
        if hits >= 2 or (hits == 1 and any(len(t) >= 6 and t in text for t in tokens)):
            score = hits * 10 + sum(len(t) for t in tokens if t in text)
            if best is None or score > best[0]:
                best = (score, fabric)
    return best[1] if best else None


def _is_fabric_more_or_browse_ask(message: str | None) -> bool:
    """True when customer asks to see / see more fabric swatches (not ready-made products)."""
    text = (message or "").lower().strip()
    if not text:
        return False
    fabric_words = ("fabric", "fabrics", "kapra", "kapray", "cloth", "swatch", "swatches")
    if not any(w in text for w in fabric_words):
        return False
    show_words = (
        "dikhao", "dekhao", "show", "more", "aur", "available", "options",
        "list", "kon", "kaun", "hai", "hain", "suggest",
    )
    return any(w in text for w in show_words)


def _is_fabric_choose_message(message: str | None) -> bool:
    text = (message or "").lower()
    return bool(
        re.search(r"\b(i choose|choose|chose|select|selected|pasand|yeh wala|ye wala)\b", text)
        and re.search(r"\bfabric\b|catalog", text)
    )


def _is_design_revision_request(message: str | None) -> bool:
    """True when customer wants the existing bespoke mockup changed / regenerated."""
    text = (message or "").lower().strip()
    if not text:
        return False
    revision_cues = (
        "minimal", "minimalist", "simple", "plain", "subtle",
        "no embroidery", "without embroidery", "embroidery na", "embroidery nahi",
        "extra embroidery na", "koi extra", "halka", "light embroidery", "lighter",
        "less embroidery", "kam embroidery", "kam kaam", "less work", "heavy nahi",
        "not heavy", "bilkul simple", "change", "badal", "dobara", "again",
        "regenerate", "re-generate", "regen", "naya design", "new design",
        "update design", "modify", "thoda change", "zyada simple", "clean look",
        "no gold", "bina embroidery", "embroidery mat", "embroidery hata",
    )
    if any(c in text for c in revision_cues):
        return True
    # Short Roman-Urdu tweak after a mockup exists, e.g. "minimal sa ho"
    if re.search(r"\b(ho|karo|kar do|krdo|kr do|banado|bana do)\b", text) and len(text.split()) <= 12:
        if any(w in text for w in ("minimal", "simple", "halka", "kam", "zyada", "embroidery", "kaam", "color", "colour")):
            return True
    return False


def _has_custom_image(state: SalesAgentState) -> bool:
    if state.get("custom_image_url"):
        return True
    result = state.get("custom_design_result")
    return bool(isinstance(result, dict) and result.get("image_url"))


def _insert_step(steps: list[str], step: str, *, before: str | None = None) -> list[str]:
    existing = list(steps)
    if step in existing:
        if before and before in existing and existing.index(step) > existing.index(before):
            existing = [s for s in existing if s != step]
        else:
            return list(dict.fromkeys(existing))
    if before and before in existing:
        idx = existing.index(before)
        return existing[:idx] + [step] + existing[idx:]
    return list(dict.fromkeys([*existing, step]))


def _apply_customization_stages(
    *,
    selected_product_id: str | None,
    has_details: bool,
    required_steps: list[str],
    state: SalesAgentState,
    event_type: str | None,
    color: str | None,
    product_type: str | None,
    cut_style: str | None,
    selected_fabric_code: str | None,
    fabrics: list[dict[str, Any]],
    wants_revision: bool = False,
) -> tuple[str | None, list[str], str | None]:
    """Path A sequential stages; Path B generates when specs exist. Returns (stage, steps, note)."""
    steps = list(required_steps)

    # Existing mockup + customer asked for a design tweak → regenerate (do not jump to measurements).
    if wants_revision and (_has_custom_image(state) or selected_fabric_code or selected_product_id):
        steps = _insert_step(steps, "generate_custom_design")
        return "generation", steps, (
            "Customer asked to revise the bespoke visual. Regenerate a NEW mockup with their latest "
            "instructions (e.g. minimal / no embroidery). Do NOT reuse or describe the old image as final."
        )

    if _has_custom_image(state):
        # Measurements live on the checkout page — never collect size/naap in chat after mockup.
        steps = [s for s in steps if s not in ("generate_custom_design", "collect_measurements", "validate_measurements")]
        # If they already confirmed this turn, schedule checkout immediately.
        # (Planner later also forces close_sale via likes_bespoke — this helps heuristics.)
        return "checkout", steps, (
            "Bespoke visual is already shown. Invite brief feedback. "
            "When they say they like it (pasand / perfect / I like this), run close_sale for checkout_url. "
            "Do NOT ask standard size or body measurements in chat — those fields are on the checkout page."
        )

    # Path B: customize a selected catalogue product
    if selected_product_id:
        if has_details:
            if "generate_custom_design" not in steps:
                steps = list(dict.fromkeys([*steps, "generate_custom_design"]))
            return "generation", steps, None
        return (
            state.get("customization_stage") or "preferences",
            [s for s in steps if s != "generate_custom_design"],
            None,
        )

    # Path A: preferences → fabric_selection → design preference → generation
    # Fabric alone is NOT enough to generate — ask how they want the outfit designed.
    has_design_pref = bool(cut_style or product_type)
    if selected_fabric_code and has_design_pref:
        steps = _insert_step(steps, "generate_custom_design")
        return "generation", steps, None

    if selected_fabric_code and not has_design_pref:
        steps = [s for s in steps if s != "generate_custom_design"]
        return "cut_style", steps, (
            "Customer already chose a fabric (see selected_fabric_catalog_code). "
            "Confirm the fabric briefly in ONE short line, then ask ONLY how they want the outfit designed "
            "(e.g. Sherwani, Angrakha, Bandhgala, Achkan, Prince Coat, Tuxedo / 3-piece suit, "
            "embroidery heavy or light). "
            "Do NOT list fabric colour variants, catalog codes, or other fabrics. "
            "Do NOT generate an image yet."
        )

    has_prefs = bool(event_type or color or product_type)
    if has_prefs and not selected_fabric_code:
        steps = [s for s in steps if s != "generate_custom_design"]
        if fabrics:
            return "fabric_selection", steps, (
                "Fabrics are shown as cards. Ask the customer to tap Choose on one fabric. "
                "Do NOT list fabric names or colours in text. Do NOT generate an image yet."
            )
        steps = _insert_step(steps, "search_fabrics")
        return "fabric_selection", steps, (
            "Show live fabrics and ask the customer to Choose one. Do NOT generate an image yet."
        )

    steps = [s for s in steps if s != "generate_custom_design"]
    return "preferences", steps, (
        "Ask event, colour, and garment so we can start the custom path. Do NOT generate yet."
    )


def _clean_product_search_query(user_message: str, target_query: str | None = None) -> str:
    """Extract clean product search terms by removing conversational fluff and filler words."""
    if target_query and len(target_query.strip()) >= 3:
        return target_query.strip()
    text = (user_message or "").strip()
    patterns = [
        r"\b(what\s+is\s+the\s+price\s+of|how\s+much\s+for|tell\s+me\s+about|show\s+me|details?\s+of|rate\s+of|price\s+of)\b",
        r"\b(complete\s+detail|details?|rate|price|cost|variations?|options?)\b",
        r"\b(batao|bataen|batae|btayein|btado|bata|bta|dikhao|dekhao|chahiye|chahye|kya|kitna|kitnay|kitne|hai|hain|hoon|hun)\b",
        r"\b(ka|ki|ke|ko|se|mein|main|par|pe)\b",
    ]
    cleaned = text
    for pat in patterns:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,?!'\"")
    return cleaned if len(cleaned) >= 2 else text


# Generic garment words — alone they mean "browse category", not "pick this SKU".
_GENERIC_PRODUCT_TOKENS = frozenset(
    {
        "suit",
        "suits",
        "sherwani",
        "shrwani",
        "tuxedo",
        "tux",
        "blazer",
        "jacket",
        "coat",
        "coats",
        "prince",
        "princes",
        "piece",
        "pieces",
        "outfit",
        "dress",
        "garment",
        "collection",
        "atelier",
        "menswear",
        "wedding",
        "cut",
        "classic",
        "embroidered",
        "embroidery",
    }
)


def _match_product_from_message(
    user_message: str,
    products: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match a listed product when the customer names it (e.g. Blue Nawab)."""
    text = (user_message or "").lower().strip()
    if not text or not products:
        return None

    best: tuple[int, dict[str, Any]] | None = None
    for item in products:
        name = str(item.get("name") or "").lower().strip()
        if not name:
            continue
        # Unique significant tokens (skip tiny + generic garment words like suit/sherwani)
        tokens = list(
            dict.fromkeys(
                t
                for t in re.split(r"[^a-z0-9]+", name)
                if len(t) > 2 and t not in _GENERIC_PRODUCT_TOKENS
            )
        )
        if not tokens:
            continue
        # Word-boundary hits — "suit" must not match inside "suits dikhao"
        hits = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", text))
        score = hits
        if name in text:
            score = max(score, len(tokens) + 1)
        if "sherwani" in text and "shrwani" in name:
            score = max(score, hits + 1)
        if "shrwani" in text and "sherwani" in name:
            score = max(score, hits + 1)
        distinctive = [t for t in tokens if len(t) >= 5]
        if hits >= 2 or (
            hits == 1 and any(re.search(rf"\b{re.escape(t)}\b", text) for t in distinctive)
        ):
            if best is None or score > best[0]:
                best = (score, item)
    return best[1] if best else None


def _collect_known_products(state: dict[str, Any] | SalesAgentState) -> list[dict[str, Any]]:
    """Union of current + history products for name→id rematch after empty search turns."""
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(items: list[Any] | None) -> None:
        for p in items or []:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("product_id") or p.get("id") or "")
            key = pid or str(p.get("name") or "").strip().lower()
            if not key or key in seen:
                continue
            if p.get("error"):
                continue
            seen.add(key)
            pool.append(p)

    _add(list(state.get("products") or []))
    details = state.get("product_details")
    if isinstance(details, dict) and not details.get("error"):
        _add([details])
    for msg in reversed(list(state.get("messages") or [])):
        if not isinstance(msg, dict):
            continue
        meta = msg.get("metadata") or {}
        _add(list(meta.get("products") or []))
        pd = meta.get("product_details")
        if isinstance(pd, dict) and not pd.get("error"):
            _add([pd])
    return pool


def _wants_unseen_only_options(user_message: str) -> bool:
    """True when customer asks for *new* options (exclude already-shown IDs)."""
    t = (user_message or "").lower()
    cues = (
        "other option", "other options", "aur option", "aur options",
        "show more", "kuch aur", "aur dikhao", "aur dikhao", "more option",
        "more options", "kisi aur", "anything else", "koi aur", "aur pieces",
        "aur piece", "different option", "different pieces",
        "elawa", "ilawa", "besides", "other than", "ke siwa", "ke siva",
        "aur nhi", "aur nahi", "yehi hain", "bas yehi", "bs yehi", "only these",
        "sirf yeh", "sirf ye", "is that all", "that's all", "no more",
    )
    return any(c in t for c in cues)


def _color_is_only_from_product_name(color: str | None, product: dict[str, Any] | None) -> bool:
    """Avoid treating 'Blue' in 'Blue Nawab Sherwani' as a colour preference."""
    if not color or not product:
        return False
    name = str(product.get("name") or "").lower()
    return color.lower() in name


def _pick_confirmed_product(
    products: list[dict[str, Any]],
    selected_product_id: str | None,
    color: str | None,
) -> dict[str, Any] | None:
    if selected_product_id:
        for item in products:
            if str(item.get("product_id")) == str(selected_product_id):
                return item
    if color:
        color_l = color.lower()
        for item in products:
            colors = [str(c).lower() for c in (item.get("available_colors") or [])]
            primary = str(item.get("color") or item.get("primary_color") or "").lower()
            if color_l in colors or color_l == primary:
                return item
    return products[0] if products else None


def _build_checkout_hand_off_note(
    *,
    checkout_url: str,
    product_name: str | None = None,
    variation_name: str | None = None,
    color: str | None = None,
    negotiated: bool = False,
) -> str:
    bits = []
    if product_name:
        bits.append(product_name)
    if variation_name:
        bits.append(f"variation {variation_name}")
    if color:
        bits.append(f"colour {color}")
    label = ", ".join(bits) if bits else "this piece"
    neg = " after negotiation" if negotiated else ""
    return (
        f"Customer is ready to checkout{neg} on {label}. "
        f"Reply briefly with name/price (use negotiated offered_price if present), "
        f"then give this exact checkout_url: {checkout_url}. "
        "They open that link, sign in, and complete order (size etc. there). "
        "Do NOT invent a cart URL, catalog product_url, or ask size, bespoke, measurements, or phone."
    )


def _build_product_interest_note(
    *,
    product_name: str | None,
    needs_variation: bool,
    variation_names: list[str] | None = None,
    advance_close: bool = False,
) -> str:
    name = product_name or "this piece"
    if needs_variation and variation_names:
        return (
            f"Customer liked {name}. List ONLY these product variations: "
            f"{', '.join(variation_names)}. Ask which one. "
            "Do NOT give product_url / checkout link yet. Negotiation may come next."
        )
    if advance_close:
        return (
            f"Customer confirmed they like {name} — it is already selected. "
            "Do NOT re-show catalog cards, do NOT say 'here are our options / yeh hain hamare pieces'. "
            "Advance toward closing in 1–2 short sentences: brief praise, confirm name + price, "
            "then ONE next step (checkout invite, or whether the price works). "
            "Do NOT ask for size or body measurements in chat. "
            "No product_url unless they explicitly said buy/checkout."
        )
    return (
        f"Customer liked {name}. Confirm briefly with name + price only. "
        "Do NOT give product_url / checkout link yet. "
        "Next: ask colour if unknown, or invite them to say if price works / wants a better deal "
        "(negotiation), or when ready they can say buy/checkout for the link."
    )


def _heuristic_plan(user_message: str, state: SalesAgentState | None = None) -> dict[str, Any]:
    """Fallback planner if LLM JSON extraction fails."""
    text = user_message.lower()
    steps: list[str] = []
    intent = "general"
    handover_reason: str | None = None
    session = state or {}

    # Bespoke mockup approved → checkout directly (size/naap on checkout page).
    if (
        _has_custom_image(session)  # type: ignore[arg-type]
        and _is_like_confirm(user_message)
        and not _is_design_revision_request(user_message)
    ):
        return {
            "intent": "closing",
            "sales_stage": "closing",
            "buying_intent": "ready_to_buy",
            "required_steps": ["close_sale"],
            "objection_type": None,
            "handover_reason": None,
            "event_type": session.get("event_type"),
            "product_type": session.get("product_type"),
            "color": session.get("color"),
            "size": session.get("size"),
            "quantity": session.get("quantity") or 1,
            "budget": session.get("budget"),
            "wedding_date": session.get("wedding_date"),
            "selected_product_id": session.get("selected_product_id"),
            "selected_fabric_catalog_code": session.get("selected_fabric_catalog_code"),
            "cut_style": session.get("cut_style"),
            "customization_stage": "checkout",
            "measurement_path": session.get("measurement_path"),
        }

    detail_keywords = [
        "detail", "details", "more about", "about it", "batao", "tell me more",
        "variation", "variations", "variant", "variants", "colors", "cuts",
    ]
    named_from_session = _match_product_from_message(
        user_message, _collect_known_products(session) if session else []
    )
    if (
        (any(keyword in text for keyword in detail_keywords) or _is_product_variation_inquiry(text) or _is_product_detail_request(user_message))
        and (session.get("selected_product_id") or named_from_session)
    ):
        steps.append("get_product_details")
        intent = "product_search"
        # Prefer details-only — do not also schedule a fresh catalog browse.
        steps = [s for s in steps if s != "search_products"]

    if any(w in text for w in ["stole", "khussa", "waistcoat", "turban", "pagri", "safa", "accessory", "accessories", "dupatta"]):
        # Accessories-only browse — never route to garment product_search alone.
        steps.append("search_accessories")
        intent = "accessories_search"
    if any(w in text for w in ["buy", "purchase", "reserve", "hold", "finalize", "finalise", "book", "order"]):
        steps.append("close_sale")
        intent = "closing"
    fabric_browse = False
    if any(w in text for w in [
        "custom", "bespoke", "fabric", "fabrics", "kapra", "kapray", "customize", "tailor made",
        "apna color", "apna colour", "halka kaam", "halka embroidery",
        "kam embroidery", "light work", "less embroidery", "simple kaam",
    ]):
        # Choosing an already-shown fabric is selection, not another catalogue browse.
        if _is_fabric_choose_message(user_message) or (
            session.get("selected_fabric_catalog_code")
            and any(w in text for w in ("choose", "chose", "pasand", "select"))
        ):
            intent = "custom_design"
            steps = [s for s in steps if s not in ("search_fabrics", "search_products")]
        else:
            fabric_browse = any(
                w in text
                for w in (
                    "fabric",
                    "fabrics",
                    "kapra",
                    "kapray",
                    "cloth",
                    "swatch",
                )
            ) and any(
                w in text
                for w in (
                    "available",
                    "availbale",
                    "dikhao",
                    "dekhao",
                    "show",
                    "kon",
                    "kaun",
                    "list",
                    "options",
                    "hai",
                    "hain",
                    "suggest",
                    "recommend",
                )
            )
            steps.append("search_fabrics")
            intent = "fabric_custom"
            # Pure fabric catalogue ask — do not also dump ready-made products.
            if fabric_browse:
                steps = [s for s in steps if s not in ("search_products", "get_product_details")]
                intent = "fabric_custom"
    guidance_words = [
        "suggest", "suggets", "recommend", "suggestion", "no idea", "dont know",
        "don't know", "what to wear", "kya pehnu", "kya pehno", "aap batao",
        "aap hi batao", "confuse", "confused", "samajh nahi", "samjh nahi",
    ]
    is_seeking_guidance = any(w in text for w in guidance_words)

    show_words = [
        "available", "stock", "availbale", "dikhao", "dekhao", "show me", "show options",
        "options dikhao", "kuch dikhao", "designs dikhao", "collection dikhao", "pieces dikhao",
    ]
    # Never overwrite a fabric-catalogue ask with ready-made product_search.
    # Category switch ("prince coats dikhao") must search even if a prior SKU is selected.
    fresh_category = _is_fresh_category_browse(
        user_message, session.get("catalog_categories") if session else None
    )
    if (
        not fabric_browse
        and intent != "fabric_custom"
        and any(w in text for w in show_words)
        and not is_seeking_guidance
        and (not session.get("selected_product_id") or fresh_category)
    ):
        steps.append("search_products")
        intent = "product_search"
        if fresh_category:
            steps = [s for s in steps if s != "get_product_details"]
    elif (
        not fabric_browse
        and intent != "fabric_custom"
        and session.get("sales_stage") == "discovery"
        and session.get("event_type")
        and not session.get("selected_product_id")
    ):
        color_words = ["blue", "black", "grey", "gray", "white", "ivory", "green", "maroon", "red", "brown", "navy", "charcoal", "cream", "gold"]
        has_color = any(w in text for w in color_words)
        has_budget = bool(re.search(r"\b\d{2,6}\b|budget|k\b", text))
        explicit_show = any(w in text for w in ["dikhao", "dekhao", "show me", "options dikhao", "designs dikhao", "pieces dikhao"])
        explicit_decline = any(w in text for w in ["koi bhi", "jo acha", "no budget", "koi preference nahi"])
        if explicit_show or explicit_decline or (has_color and has_budget) or (has_color and not is_seeking_guidance):
            steps.append("search_products")
            intent = "product_search"
        elif is_seeking_guidance:
            intent = "style_advice"
    elif session.get("selected_product_id") and re.search(
        r"\bsize\b|\bavailable\b|\bstock\b", text
    ):
        steps.append("check_inventory")
        intent = "inventory_check" if intent == "general" else "mixed"
    if any(w in text for w in [
        "discount", "kam karo", "kam hojayega", "kam ho", "thori kam", "thora kam",
        "price kam", "qeemat kam", "negotiate", "offer", "discount mil sakta",
    ]):
        if (
            session.get("selected_product_id")
            or session.get("custom_image_url")
            or (isinstance(session.get("price_quote"), dict) and session.get("price_quote", {}).get("ok"))
        ):
            steps.append("calculate_negotiation_offer")
            intent = "discount_request" if intent == "general" else "mixed"
            steps = [s for s in steps if s != "search_fabrics"]
    # Colour reply after we asked colour during negotiation → continue the ladder, do not search.
    if (
        session.get("selected_product_id")
        and _negotiation_awaiting_color(session)
        and _extract_explicit_color(text)
    ):
        steps.append("calculate_negotiation_offer")
        intent = "discount_request"
        steps = [s for s in steps if s != "search_products"]
    # Liked the already-selected piece → do not restart catalog search.
    if session.get("selected_product_id") and _is_like_confirm(user_message):
        steps = [s for s in steps if s != "search_products"]
        if "get_product_details" not in steps:
            steps.append("get_product_details")
    if any(w in text for w in [
        "height", "chest", "waist", "6'", "6ft", "40r", "38r", "36r", "42r", "44r",
        "naap", "measurement", "size chart", "shoulder", "sleeve",
    ]):
        if any(w in text for w in [
            "40r", "38r", "36r", "42r", "44r", "naap", "measurement", "size chart",
            "shoulder", "sleeve", "jacket",
        ]):
            steps.append("collect_measurements")
            intent = "measurement_check" if intent == "general" else "mixed"
        else:
            steps.append("validate_measurements")
            intent = "measurement_check" if intent == "general" else "mixed"

    if _explicit_handover_requested(user_message):
        steps.append("create_human_handover")
        intent = "handover" if intent == "general" else "mixed"
        handover_reason = "explicit_request"
    elif not steps and any(keyword in text for keyword in OFF_DOMAIN_KEYWORDS):
        # Weather / sports / jokes are off-domain: redirect, do not handover.
        intent = "general"
        handover_reason = None

    color = _extract_explicit_color(text)
    event_type = None
    for needle, label in (
        ("nikkah", "Nikah"),
        ("nikah", "Nikah"),
        ("baraat", "Barat"),
        ("barat", "Barat"),
        ("valima", "Walima"),
        ("walima", "Walima"),
        ("mehandi", "Mehndi"),
        ("mehendi", "Mehndi"),
        ("mehndi", "Mehndi"),
    ):
        if needle in text:
            event_type = label
            break
    product_type = None
    excluded_cat = _asks_besides_category(user_message)
    if excluded_cat:
        product_type = _complementary_category_for_event(excluded_cat, event_type)
    elif "sherwani" in text:
        product_type = "Sherwani"
    elif "prince coat" in text or "prine coat" in text or "princecoat" in text:
        product_type = "Prince Coat"
    elif "prince" in text:
        product_type = "Prince Coat"
    elif "tuxedo" in text or "double breast" in text or "three piece" in text or "two piece" in text:
        product_type = "Suits"
    elif "suit" in text:
        product_type = "Suits"
    size = "40" if "40" in text else "42" if "42" in text else None
    quantity = 5 if "5" in text else 6 if "6" in text else 1
    height = "6'4" if "6'4" in text or "6 ft 4" in text else None
    wedding_date = None
    for month in (
        "january", "february", "march", "april", "june", "july",
        "august", "september", "october", "november", "december",
    ):
        if month in text:
            wedding_date = month.title()
            break
    # Common Roman-Urdu / typo month stems (e.g. "decemebr") → full month for season filter.
    if not wedding_date:
        month_stems = (
            ("jan", "January"), ("feb", "February"), ("mar", "March"), ("apr", "April"),
            ("jun", "June"), ("jul", "July"), ("aug", "August"), ("sep", "September"),
            ("oct", "October"), ("nov", "November"), ("dec", "December"),
        )
        for stem, label in month_stems:
            if stem in text:
                wedding_date = label
                break
    if not wedding_date:
        for season in ("winter", "summer", "autumn", "spring"):
            if season in text:
                wedding_date = season
                break

    regex_contact = _extract_contact_regex(user_message)

    return {
        "intent": intent,
        "required_steps": list(dict.fromkeys(steps)),
        "handover_reason": handover_reason,
        "event_type": event_type,
        "product_type": product_type,
        "color": color,
        "size": size,
        "quantity": quantity,
        "budget": None,
        "wedding_date": wedding_date,
        "height": height,
        "chest": None,
        "waist": None,
        "shoulder": None,
        "sleeve": None,
        "jacket_length": None,
        "selected_product_id": (str((named_from_session or {}).get("product_id")) if named_from_session else None) or session.get("selected_product_id"),
        "customer_contact": {
            "name": regex_contact.get("name"),
            "phone": regex_contact.get("phone"),
            "email": regex_contact.get("email"),
        },
    }


def log_node_payload(node_name: str):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(state: SalesAgentState, *args: Any, **kwargs: Any) -> dict[str, Any]:
            from app.core.payload_log import print_payload

            try:
                print_payload(
                    f"NODE IN {node_name}",
                    {
                        "session_id": state.get("session_id"),
                        "user_message": state.get("user_message"),
                        "intent": state.get("intent"),
                        "sales_stage": state.get("sales_stage"),
                        "buying_intent": state.get("buying_intent"),
                        "required_steps": state.get("required_steps"),
                        "customization_stage": state.get("customization_stage"),
                        "selected_product_id": state.get("selected_product_id"),
                        "selected_fabric_catalog_code": state.get("selected_fabric_catalog_code"),
                        "cut_style": state.get("cut_style"),
                        "custom_image_url": bool(state.get("custom_image_url")),
                        "checkout_url": state.get("checkout_url"),
                        "products": state.get("products") or [],
                        "fabrics": state.get("fabrics") or [],
                    },
                )
            except Exception as e:
                print(f"📥 [NODE IN ERROR] {node_name}: {e}", flush=True)

            result = await fn(state, *args, **kwargs)

            try:
                brief = dict(result) if isinstance(result, dict) else {"result": result}
                print_payload(f"NODE OUT {node_name}", brief)
            except Exception as e:
                print(f"📤 [NODE OUT ERROR] {node_name}: {e}", flush=True)

            return result
        return wrapper
    return decorator


@log_node_payload("planner_node")
async def planner_node(state: SalesAgentState) -> dict[str, Any]:
    user_message = state["user_message"]
    logger.info("planner_node start %s message='%s'", _log_state_summary(state), user_message[:80])

    # Layer 5 Prompt Armor Check
    is_jailbreak, armor_reply = guardrails.check_prompt_armor(user_message)
    if is_jailbreak and armor_reply:
        return {
            "messages": [{"role": "user", "content": user_message}],
            "intent": "general",
            "required_steps": [],
            "current_step_index": 0,
            "final_response": armor_reply,
        }

    # Live categories first so the planner can map suit → exact catalog name.
    catalog_categories: list[dict[str, Any]] = list(state.get("catalog_categories") or [])
    try:
        catalog_categories = await backend_api.list_categories()
    except Exception:
        logger.exception(
            "planner_node list_categories failed session_id=%s",
            state.get("session_id"),
        )

    catalog_variations: list[dict[str, Any]] = list(state.get("catalog_variations") or [])
    try:
        catalog_variations = await backend_api.list_variations()
    except Exception:
        logger.exception(
            "planner_node list_variations failed session_id=%s",
            state.get("session_id"),
        )
        catalog_variations = list(state.get("catalog_variations") or [])

    planner_input = _build_planner_input(state, catalog_categories, catalog_variations)

    # Heuristic pre-detect for "X k/ka/ke elawa/ilawa" pattern (LLM sometimes misses this)
    wants_more_options = False
    exclude_category = None
    logger.info("🔍 HEURISTIC CHECK message='%s'", user_message[:60])
    elawa_match = re.search(
        r'(sherwani|suit|suits|prince\s*coat)\s*k[ea]?\s*(elawa|ilawa|alawa)',
        user_message.lower()
    )
    if not elawa_match:
        # Try "besides X" / "other than X" English patterns
        elawa_match = re.search(
            r'(besides|other\s+than|except)\s+(sherwani|suit|suits|prince\s*coat)',
            user_message.lower()
        )
        if elawa_match:
            exclude_category = elawa_match.group(2).strip().title()
    else:
        exclude_category = elawa_match.group(1).strip().title()
    
    if exclude_category:
        if exclude_category.lower() == "suit":
            exclude_category = "Suits"
        elif "prince" in exclude_category.lower():
            exclude_category = "Prince Coat"
        logger.info(
            "Heuristic detected exclude_category=%s from message session_id=%s",
            exclude_category,
            state.get("session_id"),
        )
    
    try:
        with log_openai_call(logger, operation="planner", model=_resolved_model_name()):
            response = await _active_llm().ainvoke([
                SystemMessage(content=PLANNER_PROMPT),
                HumanMessage(content=planner_input),
            ])
        plan = _safe_json_loads(response.content)
        # LLM-detected flags for intent (keep heuristic value if already set)
        wants_more_options = bool(plan.get("wants_more_options"))
        if not exclude_category:
            exclude_category = plan.get("exclude_category")
        logger.info(
            "planner LLM RAW output session_id=%s len=%d content_preview=%s",
            state.get("session_id"),
            len(str(response.content)),
            str(response.content)[:300],
        )
        logger.info(
            "planner LLM parsed session_id=%s wants_more=%s exclude=%s intent=%s",
            state.get("session_id"),
            wants_more_options,
            exclude_category,
            plan.get("intent"),
        )
    except Exception:
        logger.warning(
            "planner_node LLM failed, using heuristic fallback session_id=%s",
            state.get("session_id"),
            exc_info=True,
        )
        plan = _heuristic_plan(user_message, state)

    # LLM often misses nikkah/december — fill from the message if the plan left them empty.
    heuristic = _heuristic_plan(user_message, state)
    for field in ("event_type", "wedding_date", "product_type"):
        if not plan.get(field) and heuristic.get(field):
            plan[field] = heuristic[field]

    # Heuristic backup for "show more" / "aur dikhao" when LLM misses the flag.
    if not wants_more_options and discovery_engine.customer_asked_for_catalog(user_message):
        low = user_message.lower()
        if any(
            cue in low
            for cue in (
                "show more",
                "more options",
                "aur dikhao",
                "aur dekhao",
                "kuch aur",
                "aur options",
                "more designs",
                "more pieces",
                "aur fabrics",
                "more fabrics",
            )
        ):
            wants_more_options = True

    # Soft guidance ("suggest / no idea"): never lock product_type unless they named a garment.
    if discovery_engine.customer_asked_for_guidance(user_message):
        named_now = _message_explicitly_names_garment(user_message, catalog_categories)
        if named_now:
            plan["product_type"] = resolve_category_against_catalog(named_now, catalog_categories) or named_now
        else:
            # Keep prior session garment only; drop LLM pitch as confirmed type.
            plan["product_type"] = state.get("product_type")

    # Handle "sherwani k elawa" → complementary category
    if exclude_category:
        alt = _complementary_category_for_event(
            exclude_category,
            plan.get("event_type") or state.get("event_type"),
        )
        plan["product_type"] = resolve_category_against_catalog(alt, catalog_categories) or alt
        logger.info(
            "planner detected exclude_category=%s, switching to %s session_id=%s",
            exclude_category,
            plan["product_type"],
            state.get("session_id"),
        )

    # Canonical event + month labels
    if plan.get("event_type") or state.get("event_type"):
        plan["event_type"] = _normalize_event_type_label(
            plan.get("event_type") or state.get("event_type")
        )
    if plan.get("wedding_date") or state.get("wedding_date") or heuristic.get("wedding_date"):
        plan["wedding_date"] = _normalize_wedding_date(
            plan.get("wedding_date") or state.get("wedding_date") or heuristic.get("wedding_date")
        )

    # Colour: ONLY what the customer typed as a preference — never from a product title
    # (e.g. "Blue Nawab Signature Shrwani" must not lock session color=Blue).
    stated_color = _extract_explicit_color(user_message)
    named_for_color = _match_product_from_message(
        user_message, _collect_known_products(state)
    )
    if stated_color and named_for_color and _color_is_only_from_product_name(stated_color, named_for_color):
        stated_color = None
        plan["color"] = None
    elif stated_color:
        plan["color"] = stated_color
    elif plan.get("color"):
        llm_color = str(plan.get("color")).strip()
        if any(w.lower() in user_message.lower() for w in llm_color.split()):
            # Same guard for LLM-extracted colour that only appears inside a product name
            fake_prod = named_for_color or _match_product_from_message(
                user_message, _collect_known_products(state)
            )
            if fake_prod and _color_is_only_from_product_name(llm_color, fake_prod):
                plan["color"] = None
            else:
                plan["color"] = llm_color
        else:
            plan["color"] = None
    else:
        plan["color"] = None

    # Map free-form garment words onto live catalog names (LLM should already pick exact;
    # soft resolve is a safety net only — no hardcoded suit→Suits dictionary).
    if plan.get("product_type"):
        plan["product_type"] = resolve_category_against_catalog(
            plan.get("product_type"),
            catalog_categories,
        )
    elif heuristic.get("product_type"):
        plan["product_type"] = resolve_category_against_catalog(
            heuristic.get("product_type"),
            catalog_categories,
        )

    resolved_product_type = plan.get("product_type") or state.get("product_type")
    category_variations = filter_variations_for_category(
        catalog_variations,
        resolved_product_type,
    )
    # If product_type still unknown, try matching a variation from the message to infer category.
    matched_variation = resolve_variation_against_catalog(
        user_message,
        category_variations or catalog_variations,
    )
    if matched_variation and not resolved_product_type:
        inferred_cat = (
            (matched_variation.get("category") or {}).get("name")
            if isinstance(matched_variation.get("category"), dict)
            else matched_variation.get("category_name")
        )
        if inferred_cat:
            plan["product_type"] = resolve_category_against_catalog(
                inferred_cat,
                catalog_categories,
            ) or inferred_cat
            resolved_product_type = plan["product_type"]
            category_variations = filter_variations_for_category(
                catalog_variations,
                resolved_product_type,
            )
            matched_variation = resolve_variation_against_catalog(
                user_message,
                category_variations,
            ) or matched_variation

    selected_variation_id = state.get("selected_variation_id")
    selected_variation_name = state.get("selected_variation_name")
    # Prefer message/heuristic match; also accept LLM pick if it resolves against live list.
    if not matched_variation and plan.get("selected_variation_name"):
        matched_variation = resolve_variation_against_catalog(
            str(plan.get("selected_variation_name")),
            category_variations or catalog_variations,
        )
    if matched_variation:
        selected_variation_id = str(matched_variation.get("id") or selected_variation_id or "")
        selected_variation_name = str(
            matched_variation.get("name") or selected_variation_name or ""
        )

    regex_contact = _extract_contact_regex(user_message, is_handover=bool(state.get("handover_pending")))
    customer_contact = _merge_customer_contact(
        state.get("customer_contact"),
        plan.get("customer_contact"),
        regex_contact,
    )

    intent = plan.get("intent", "general")
    required_steps = plan.get("required_steps", [])
    handover_reason = plan.get("handover_reason")

    handover_pending = bool(state.get("handover_pending"))
    if "create_human_handover" in required_steps and _missing_handover_fields(customer_contact):
        handover_pending = True

    if (
        not _missing_handover_fields(customer_contact)
        and (handover_pending or _handover_requested_in_history(state.get("messages") or []))
    ):
        required_steps = ["create_human_handover"]
        intent = "handover"
        handover_reason = handover_reason or "explicit_request"

    # Build CustomerProfile to evaluate discovery triggers
    profile_dict = dict(state.get("customer_profile") or {})
    profile_dict["session_id"] = state.get("session_id", "default")
    for field, key in [
        ("event_type", "event_type"),
        ("product_type", "product_type"),
        ("budget", "budget"),
        ("wedding_date", "wedding_date"),
        ("size", "jacket_size"),
        ("height", "height"),
        ("chest", "chest"),
        ("waist", "waist"),
        ("skin_tone", "skin_tone"),
    ]:
        val = plan.get(field) if plan.get(field) is not None else state.get(field)
        if val:
            if key == "event_type":
                val = _normalize_event_type_label(str(val))
            elif key == "wedding_date":
                val = _normalize_wedding_date(str(val))
            profile_dict[key] = val

    if plan.get("color"):
        preferred = list(profile_dict.get("preferred_colors") or [])
        if plan["color"] not in preferred:
            preferred.append(plan["color"])
        profile_dict["preferred_colors"] = preferred

    if selected_variation_id:
        profile_dict["selected_variation_id"] = selected_variation_id
    if selected_variation_name:
        profile_dict["selected_variation_name"] = selected_variation_name
    # Keep product_type on profile in sync with resolved category.
    if resolved_product_type:
        profile_dict["product_type"] = resolved_product_type
        plan["product_type"] = resolved_product_type

    buying_intent = plan.get("buying_intent") or profile_dict.get("buying_intent") or "browsing"
    objection_type = plan.get("objection_type")
    if objection_type in (None, "none", ""):
        objection_type = None

    profile = CustomerProfileSchema(**profile_dict)
    if not profile.derived_season:
        profile.derived_season = derive_season_from_text(profile.wedding_date)

    profile.declined_slots = discovery_engine.detect_declined_slots(
        profile,
        user_message,
        category_variations,
    )
    profile.buying_intent = buying_intent  # type: ignore[assignment]

    session_products = list(state.get("products") or [])
    sales_stage = plan.get("sales_stage") or profile.sales_stage or "discovery"
    if objection_type:
        sales_stage = "objection" if objection_type != "price" else "negotiation"
    if intent == "discount_request" or "calculate_negotiation_offer" in required_steps:
        sales_stage = "negotiation"
        # CRITICAL: Clear products list when entering negotiation — focus on selected product only
        session_products = []

    # Price push with a selected product OR an active bespoke/custom quote must run the negotiation ladder.
    _discount_cues = (
        "discount", "negotiate", "expensive", "mehngi", "mehnga",
        "thora zyada", "zyada hai", "kam karo", "kam ho", "thori kam", "thora kam",
        "price kam", "qeemat kam", "offer",
    )
    _has_negotiable_piece = bool(
        state.get("selected_product_id")
        or plan.get("selected_product_id")
        or state.get("custom_image_url")
        or (
            isinstance(state.get("price_quote"), dict)
            and state.get("price_quote", {}).get("ok")
        )
        or (
            isinstance(state.get("custom_design_result"), dict)
            and state.get("custom_design_result", {}).get("image_url")
        )
    )
    if (
        _has_negotiable_piece
        and any(cue in user_message.lower() for cue in _discount_cues)
        and "calculate_negotiation_offer" not in required_steps
        and "create_human_handover" not in required_steps
    ):
        required_steps = list(dict.fromkeys([*required_steps, "calculate_negotiation_offer"]))
        intent = "discount_request" if intent == "general" else intent
        sales_stage = "negotiation"
        # CRITICAL: Clear products list — negotiation conversations should focus on selected product only, no carousel
        session_products = []
        # Don't browse fabrics during a price push on an existing design.
        required_steps = [s for s in required_steps if s != "search_fabrics"]

    # After we asked for a colour to match an accessory, a colour reply continues negotiation.
    named_color_now = _extract_explicit_color(user_message)
    awaiting_color = _negotiation_awaiting_color(state)
    if (
        awaiting_color
        and (state.get("selected_product_id") or plan.get("selected_product_id"))
        and named_color_now
        and "create_human_handover" not in required_steps
    ):
        if "calculate_negotiation_offer" not in required_steps:
            required_steps = list(dict.fromkeys([*required_steps, "calculate_negotiation_offer"]))
        intent = "discount_request"
        sales_stage = "negotiation"
        session_products = []
        if not plan.get("color"):
            plan["color"] = named_color_now
        # Colour answer is not a catalog browse or a custom-design request.
        required_steps = [
            s for s in required_steps
            if s not in ("search_products", "search_accessories", "generate_custom_design")
        ]

    # CRITICAL GUARD: Negotiation / discount needs a negotiable piece (catalog OR bespoke quote).
    # If none yet, price-conscious phrases are budget guidance for search — strip negotiation.
    _has_negotiable_piece = bool(
        state.get("selected_product_id")
        or plan.get("selected_product_id")
        or state.get("custom_image_url")
        or (
            isinstance(state.get("price_quote"), dict)
            and state.get("price_quote", {}).get("ok")
        )
        or (
            isinstance(state.get("custom_design_result"), dict)
            and state.get("custom_design_result", {}).get("image_url")
        )
    )
    if not _has_negotiable_piece:
        if intent == "discount_request":
            intent = "product_search" if (state.get("event_type") or plan.get("event_type")) else "general"
        if sales_stage == "negotiation":
            sales_stage = "recommendation" if (state.get("event_type") or plan.get("event_type")) else "discovery"
        required_steps = [s for s in required_steps if s != "calculate_negotiation_offer"]

    if intent == "fabric_custom" or "search_fabrics" in required_steps:
        sales_stage = "recommendation"

    if discovery_engine.should_trigger_discovery(
        profile, user_message, intent, category_variations
    ):
        sales_stage = "discovery"
        # Qualifying questions first — do not hit the catalog yet.
        if "create_human_handover" not in required_steps:
            skip_search = {"search_products"}
            if intent != "fabric_custom":
                skip_search.add("search_fabrics")
            required_steps = [s for s in required_steps if s not in skip_search]

    fabric_path = intent == "fabric_custom" or "search_fabrics" in required_steps

    # "aur fabrics dikhao" / show-more while already browsing swatches → stay on fabrics.
    in_fabric_session = bool(
        state.get("customization_stage") in ("fabric_selection", "cut_style")
        or state.get("fabrics")
        or state.get("selected_fabric_catalog_code")
    )
    if _is_fabric_more_or_browse_ask(user_message) or (
        wants_more_options
        and in_fabric_session
        and not _message_explicitly_names_garment(user_message, catalog_categories)
        and not re.search(r"\b(sherwani|suit|suits|prince\s*coat|ready[- ]?made)\b", user_message.lower())
    ):
        fabric_path = True
        intent = "fabric_custom"
        if "search_fabrics" not in required_steps:
            required_steps = list(dict.fromkeys([*required_steps, "search_fabrics"]))
        required_steps = [s for s in required_steps if s not in ("search_products", "get_product_details")]
        sales_stage = "recommendation"

    # Hard gate: search only on dikhao/availability AND discovery prerequisites met.
    if "search_products" in required_steps and not discovery_engine.can_run_product_search(
        profile, user_message, category_variations
    ):
        required_steps = [s for s in required_steps if s != "search_products"]
        if not required_steps:
            sales_stage = "discovery"
            intent = "general" if intent == "product_search" else intent

    # Explicit show / availability → search only when garment (+ variation) + colour/budget ready.
    # Fabric catalogue asks must not be rewritten into ready-made product_search.
    if discovery_engine.should_run_product_search(user_message) and not fabric_path:
        # Garment named in THIS message may update type; never invent from event alone.
        named_garment = _message_explicitly_names_garment(user_message, catalog_categories)
        if named_garment:
            resolved = resolve_category_against_catalog(named_garment, catalog_categories) or named_garment
            plan["product_type"] = resolved
            profile_dict["product_type"] = resolved
            resolved_product_type = resolved
            category_variations = filter_variations_for_category(
                catalog_variations,
                resolved_product_type,
            )
            profile = CustomerProfileSchema(**{**profile.model_dump(), "product_type": resolved})

        if discovery_engine.can_run_product_search(profile, user_message, category_variations):
            if "search_products" not in required_steps:
                required_steps = list(dict.fromkeys([*required_steps, "search_products"]))
            # Keep inventory when user explicitly asked stock/size; drop only soft show-me paths.
            if not re.search(r"\bsize\b|\bavailable\b|\bstock\b|\bin stock\b", user_message.lower()):
                required_steps = [s for s in required_steps if s != "check_inventory"]
            sales_stage = "recommendation"
            intent = "product_search"
        else:
            # Stay in discovery — ask next missing slot (garment / variation / colour / budget).
            required_steps = [s for s in required_steps if s != "search_products"]
            sales_stage = "discovery"
            intent = "general" if intent == "product_search" else intent
            profile.sales_stage = "discovery"  # type: ignore[assignment]

    # If customer was previously in discovery with event known, and now provided preference or explicit cue, trigger search
    is_seeking_guidance = discovery_engine.customer_asked_for_guidance(user_message)
    has_new_concrete_preference = bool(
        plan.get("color")
        or plan.get("budget")
        or _extract_explicit_color(user_message)
    )
    if (
        not fabric_path
        and state.get("event_type")
        and state.get("sales_stage") == "discovery"
        and (has_new_concrete_preference or is_seeking_guidance or discovery_engine.should_run_product_search(user_message))
        and discovery_engine.can_run_product_search(profile, user_message, category_variations)
    ):
        if "search_products" not in required_steps:
            required_steps = list(dict.fromkeys([*required_steps, "search_products"]))
        sales_stage = "recommendation"
        intent = "product_search"
    elif not fabric_path and is_seeking_guidance and not state.get("selected_product_id"):
        # Customer asking for guidance / suggestions: show top recommendations!
        if (state.get("event_type") or plan.get("event_type")):
            if "search_products" not in required_steps:
                required_steps = list(dict.fromkeys([*required_steps, "search_products"]))
            sales_stage = "recommendation"
            intent = "product_search"

    # Re-assert fabric path after product-search heuristics (available/suggest words collide).
    # Do NOT re-force search_fabrics when the customer is choosing an already-listed fabric.
    if fabric_path and not _is_fabric_choose_message(user_message):
        intent = "fabric_custom"
        if "search_fabrics" not in required_steps:
            required_steps = list(dict.fromkeys([*required_steps, "search_fabrics"]))
        required_steps = [s for s in required_steps if s not in ("search_products",)]
        sales_stage = "recommendation"
        # Nikah/Barat → Sherwani fabrics; Walima → Suits — never dump wrong dress_category.
        if not (plan.get("product_type") or state.get("product_type") or resolved_product_type):
            inferred = _default_category_for_event(
                plan.get("event_type") or state.get("event_type")
            )
            if inferred:
                resolved = resolve_category_against_catalog(inferred, catalog_categories) or inferred
                plan["product_type"] = resolved
                profile_dict["product_type"] = resolved
                resolved_product_type = resolved
    elif _is_fabric_choose_message(user_message):
        intent = "custom_design"
        required_steps = [s for s in required_steps if s not in ("search_fabrics", "search_products")]
        sales_stage = "customization"


    # Context lock: once a piece is selected, follow-ups (like / colour / discount)
    # must NOT restart a catalog carousel unless the customer explicitly asks to browse.
    explicit_catalog_ask = discovery_engine.customer_asked_for_catalog(user_message) and not _is_like_confirm(
        user_message
    )
    selected_already = bool(state.get("selected_product_id") or plan.get("selected_product_id"))
    if selected_already and not explicit_catalog_ask:
        if (
            awaiting_color
            or _in_active_negotiation(state)
            or _is_like_confirm(user_message)
            or sales_stage == "negotiation"
            or "calculate_negotiation_offer" in required_steps
        ):
            required_steps = [s for s in required_steps if s != "search_products"]
            if "calculate_negotiation_offer" in required_steps or awaiting_color:
                sales_stage = "negotiation"
                intent = "discount_request"
                session_products = []

    if "search_accessories" in required_steps or intent == "accessories_search":
        sales_stage = "recommendation"
        intent = "accessories_search"
        # Accessories browse does not need garment discovery gates.
        required_steps = [s for s in required_steps if s != "search_products"]
        if "search_accessories" not in required_steps:
            required_steps = list(dict.fromkeys([*required_steps, "search_accessories"]))

    if "suggest_cross_sell" in required_steps:
        sales_stage = "closing"
    if "close_sale" in required_steps or intent == "closing" or buying_intent == "ready_to_buy":
        sales_stage = "closing"
        # Contact already known → include handover after close when customer is ready
        if (
            "close_sale" in required_steps
            and not _missing_handover_fields(customer_contact)
            and "create_human_handover" not in required_steps
            and buying_intent == "ready_to_buy"
        ):
            required_steps = list(dict.fromkeys([*required_steps, "create_human_handover"]))

    profile.sales_stage = sales_stage  # type: ignore[assignment]

    next_gap = (
        discovery_engine.get_next_missing_entity(profile, category_variations)
        if sales_stage == "discovery"
        else None
    )

    variation_note = None
    if sales_stage == "discovery" and next_gap:
        slot = next_gap[0]
        if slot == "preference":
            variation_note = (
                f"Customer named {plan.get('event_type') or state.get('event_type') or 'event'}. "
                "Acknowledge the event with luxury styling taste. For Nikkah/Barat suggest 2–3 live "
                "traditional options from catalog_categories (Sherwani, Prince Coat, Waistcoat when stocked) — "
                "never Sherwani-only. For Valima/Reception suggest Suits/Tuxedo. "
                "Then ask preferences in ONE polite question: season/month, color preference, and approximate budget range. "
                "Do NOT search or dump products yet. Do NOT list prices."
            )
        elif slot == "variation" and category_variations:
            names = [str(v.get("name")) for v in category_variations if v.get("name")]
            variation_note = (
                f"Ask which variation they want for {resolved_product_type}. "
                f"Live options only: {', '.join(names)}. Do not invent others. Do not search yet."
            )
    elif (
        sales_stage == "discovery"
        and discovery_engine.should_run_product_search(user_message)
        and next_gap
    ):
        missing = discovery_engine.missing_search_prerequisites(profile, category_variations, user_message=user_message)
        slot = next_gap[0]
        if slot == "product_type":
            variation_note = (
                "Customer asked what is available, but garment is not confirmed yet. "
                "Do NOT search. Briefly suggest 2–3 event-fit options from catalog_categories "
                "(for Nikah/Barat: Sherwani, Prince Coat, Waistcoat when stocked — never Sherwani-only) "
                "and ask them to confirm the garment. No product dump."
            )
        elif slot == "variation" and category_variations:
            names = [str(v.get("name")) for v in category_variations if v.get("name")]
            variation_note = (
                "Customer asked availability, but style variation is still missing. "
                f"Ask which of these live variations for {resolved_product_type}: {', '.join(names)}. "
                "Do NOT search or dump products yet."
            )
        elif slot == "color":
            if not discovery_engine.customer_asked_availability(user_message):
                variation_note = (
                    "Customer asked availability, but colour preference is still missing. "
                    "Ask one short colour question (or allow skip). Do NOT search yet."
                )
        elif slot == "budget":
            if not discovery_engine.customer_asked_availability(user_message):
                variation_note = (
                    "Customer asked availability, but budget is still missing. "
                    "Ask one short budget question (or allow skip). Do NOT search yet."
                )
        elif missing:
            variation_note = (
                f"Customer asked availability early. Ask the next missing preference ({slot}) first. "
                "Do NOT search or dump products yet."
            )

    checkout_hand_off_note = None
    product_variation_note = None
    product_interest_note = None
    suppress_product_ui = False
    selected_product_id = plan.get("selected_product_id") or state.get("selected_product_id")
    # Matching pool = current + history; response products stay current turn only
    # (search_products overwrites when it runs).
    known_products = _collect_known_products(state)
    if sales_stage != "negotiation":
        session_products = list(state.get("products") or [])
    else:
        session_products = []
    # Category browse ("prince coats dikhao") must show cards — unlock sticky Sherwani SKU.
    # "prince" alone used to false-match "...Sherwani (prince Cut)" and skip search.
    fresh_category_browse = _is_fresh_category_browse(user_message, catalog_categories)
    if fresh_category_browse or (
        discovery_engine.customer_asked_for_catalog(user_message)
        and "search_products" in required_steps
        and not _match_product_from_message(user_message, known_products or session_products)
    ):
        if "search_products" not in required_steps and fresh_category_browse:
            required_steps = list(dict.fromkeys([*required_steps, "search_products"]))
        required_steps = [s for s in required_steps if s != "get_product_details"]
        selected_product_id = None
        plan["selected_product_id"] = None
        product_interest_note = None
        suppress_product_ui = False
        sales_stage = "recommendation"
        intent = "product_search"
        profile.sales_stage = "recommendation"  # type: ignore[assignment]
    confirm_color = plan.get("color") or stated_color
    product_details_state = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    if isinstance(product_details_state, dict) and product_details_state.get("error"):
        product_details_state = {}
    product_variations: list[dict[str, Any]] = list(
        state.get("product_variations")
        or (product_details_state or {}).get("variations")
        or []
    )
    profile_src = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    selected_product_variation_id = (
        None
        if fresh_category_browse
        else (
            state.get("selected_product_variation_id")
            or profile_src.get("selected_product_variation_id")
        )
    )
    selected_product_variation_name = (
        None
        if fresh_category_browse
        else (
            state.get("selected_product_variation_name")
            or profile_src.get("selected_product_variation_name")
        )
    )
    if not selected_variation_id:
        selected_variation_id = profile_src.get("selected_variation_id") or selected_variation_id
    if not selected_variation_name:
        selected_variation_name = profile_src.get("selected_variation_name") or selected_variation_name

    # Prefer naming a listed product ("Blue Nawab is nice") over colour/id alone.
    # Never SKU-lock on a fresh category browse ("prince coats dikhao").
    named_product = (
        None
        if fresh_category_browse
        else _match_product_from_message(user_message, known_products or session_products)
    )
    if named_product:
        selected_product_id = str(named_product.get("product_id") or selected_product_id or "")
        if _color_is_only_from_product_name(confirm_color, named_product):
            confirm_color = None
            plan["color"] = None

    # Case 1: match a product-level variation from THIS message only.
    # Do not re-treat a leftover selected_variation_name as a new pick — that
    # was restarting the catalog card on "pasand" / colour follow-ups.
    matched_product_variation = resolve_variation_against_catalog(
        user_message,
        product_variations,
    )
    variation_picked_this_turn = bool(matched_product_variation)
    if not matched_product_variation and (
        selected_product_variation_name or selected_variation_name
    ):
        matched_product_variation = resolve_variation_against_catalog(
            str(selected_product_variation_name or selected_variation_name),
            product_variations,
        )
    if matched_product_variation:
        selected_product_variation_id = str(
            matched_product_variation.get("id") or selected_product_variation_id or ""
        )
        selected_product_variation_name = str(
            matched_product_variation.get("name") or selected_product_variation_name or ""
        )

    if selected_product_variation_id:
        profile_dict["selected_product_variation_id"] = selected_product_variation_id
        profile.selected_product_variation_id = selected_product_variation_id
    if selected_product_variation_name:
        profile_dict["selected_product_variation_name"] = selected_product_variation_name
        profile.selected_product_variation_name = selected_product_variation_name

    wants_checkout = _is_explicit_checkout_request(user_message) or (
        intent == "closing" and "close_sale" in required_steps
    )
    asks_variation = _is_product_variation_inquiry(user_message)
    shows_interest = _is_piece_or_colour_confirm(user_message) or bool(named_product) or asks_variation

    # PRIORITY: Check if customer is selecting a product-level variation FIRST (before general product logic)
    logger.info(
        "🔍 VAR CHECK: msg='%s' sel_prod=%s matched_var=%s vars=%d needs=%s sel_var_id=%s sel_var_name=%s",
        user_message[:30],
        bool(selected_product_id),
        bool(matched_product_variation),
        len(product_variations),
        needs_product_variation_choice(product_variations, selected_product_variation_id, selected_product_variation_name) if product_variations else "?",
        bool(selected_product_variation_id),
        selected_product_variation_name,
    )
    if (
        selected_product_id
        and variation_picked_this_turn
        and matched_product_variation
        and product_variations
        and not awaiting_color
        and "calculate_negotiation_offer" not in required_steps
        and not needs_product_variation_choice(
            product_variations,
            selected_product_variation_id,
            selected_product_variation_name,
        )
    ):
        # CRITICAL: Customer is selecting a variation (e.g. "Achkan") for an already-selected product.
        # Do NOT run search_products — just confirm the variation details.
        required_steps = [s for s in required_steps if s != "search_products"]
        logger.info(
            "✅ VARIATION SELECTED: %s for product %s, skipping search session_id=%s",
            matched_product_variation.get("name"),
            selected_product_id[:20] if selected_product_id else "?",
            state.get("session_id"),
        )
        if "get_product_details" not in required_steps:
            required_steps = list(dict.fromkeys([*required_steps, "get_product_details"]))
        sales_stage = "recommendation"
        buying_intent = "considering"
        profile.buying_intent = "considering"  # type: ignore[assignment]
        profile.sales_stage = "recommendation"  # type: ignore[assignment]
        # Clear products list so variation details are shown, not old product list
        session_products = []
        # Guide LLM to confirm variation selection with details
        product_interest_note = (
            f"Customer chose variation {selected_product_variation_name}. "
            "Confirm briefly with name/price. Do NOT re-show catalog options. "
            "Do NOT give product_url yet. Invite price discussion or checkout when they say buy."
        )
        if wants_checkout:
            buying_intent = "ready_to_buy"
            profile.buying_intent = "ready_to_buy"  # type: ignore[assignment]
            checkout_hand_off_note = None
            product_interest_note = None
            if "close_sale" not in required_steps:
                required_steps = list(dict.fromkeys([*required_steps, "close_sale"]))
        else:
            checkout_hand_off_note = None

    elif (
        not fresh_category_browse
        and not awaiting_color
        and "calculate_negotiation_offer" not in required_steps
        and (shows_interest or wants_checkout or asks_variation or _is_like_confirm(user_message))
        and (known_products or session_products or selected_product_id)
    ):
        chosen = named_product or _pick_confirmed_product(
            session_products or known_products, selected_product_id, confirm_color
        )
        if chosen:
            selected_product_id = str(chosen.get("product_id") or selected_product_id or "")
            if chosen.get("variations"):
                product_variations = list(chosen.get("variations") or product_variations)
            if "get_product_details" not in required_steps:
                required_steps = list(dict.fromkeys([*required_steps, "get_product_details"]))
            # Soft interest/checkout: skip inventory/size interrogation mid-funnel —
            # but keep check_inventory when user explicitly asked stock/size.
            stock_ask = bool(
                re.search(r"\bsize\b|\bavailable\b|\bstock\b|\bin stock\b", user_message.lower())
            )
            if not stock_ask:
                required_steps = [
                    s for s in required_steps if s not in ("check_inventory", "validate_measurements")
                ]
            elif "check_inventory" not in required_steps and selected_product_id:
                required_steps = list(dict.fromkeys([*required_steps, "check_inventory"]))
            sales_stage = "recommendation"
            buying_intent = "considering" if not wants_checkout else "ready_to_buy"
            profile.buying_intent = buying_intent  # type: ignore[assignment]
            profile.sales_stage = "recommendation"  # type: ignore[assignment]

            needs_var = needs_product_variation_choice(
                product_variations,
                selected_product_variation_id,
                selected_product_variation_name,
            )
            var_names = [str(v.get("name")) for v in product_variations if v.get("name")]

            if asks_variation:
                pname = str(chosen.get("name") or "this piece")
                if var_names:
                    product_variation_note = (
                        f"Customer asked for variations of {pname}. "
                        f"Present ONLY these product variations: {', '.join(var_names)}. "
                        "Do NOT suggest category variations or unrelated pieces."
                    )
                else:
                    colors = [str(c) for c in (chosen.get("available_colors") or []) if str(c).strip()]
                    sizes = [str(s) for s in (chosen.get("available_sizes") or []) if str(s).strip()]
                    product_variation_note = (
                        f"Customer asked for variations of {pname}. "
                        f"This specific design comes in colors: {', '.join(colors) if colors else 'standard tone'} "
                        f"and sizes: {', '.join(sizes) if sizes else 'custom sizes'}. "
                        "It has no other sub-style cut variations. Mention these color/size options and bespoke tailoring. "
                        "CRITICAL: Do NOT list category variations (like Achkan, Angrakha, Prince Coat, Tuxedo) as variations of this piece!"
                    )
                checkout_hand_off_note = None
            elif needs_var:
                product_variation_note = _build_product_interest_note(
                    product_name=str(chosen.get("name") or ""),
                    needs_variation=True,
                    variation_names=var_names,
                )
                checkout_hand_off_note = None
            elif wants_checkout:
                checkout_hand_off_note = None
                if "close_sale" not in required_steps:
                    required_steps = list(dict.fromkeys([*required_steps, "close_sale"]))
            else:
                # Liked / colour-confirmed the selected piece — talk, do not re-render cards.
                liked_already = _is_like_confirm(user_message) or bool(named_product)
                product_interest_note = _build_product_interest_note(
                    product_name=str(chosen.get("name") or ""),
                    needs_variation=False,
                    advance_close=bool(liked_already and not needs_var),
                )
                checkout_hand_off_note = None
                suppress_product_ui = True
                # Keep stage on detail — "closing" makes the widget flash a checkout CTA too early.
                sales_stage = "detail"
                buying_intent = "considering" if not wants_checkout else "ready_to_buy"
                profile.buying_intent = buying_intent  # type: ignore[assignment]
                profile.sales_stage = "detail"  # type: ignore[assignment]
                details_ready = (
                    isinstance(product_details_state, dict)
                    and product_details_state
                    and not product_details_state.get("error")
                    and str(product_details_state.get("product_id") or "") == str(selected_product_id)
                )
                if details_ready:
                    required_steps = [s for s in required_steps if s != "get_product_details"]
            
            # CRITICAL: Clear products list when customer shows interest in ONE specific product
            # (prevents showing old product carousel when only selected product details should appear)
            session_products = []
            required_steps = [s for s in required_steps if s != "search_products"]

    # Customization Flow Detection
    # Customization Flow Detection
    _customization_cues = (
        "custom", "customize", "customise", "apna banwana", "bespoke", "dusre color", "doosre color",
        "kisi aur", "change kar", "change", "ban sakti", "ban sakta", "banwa", "banwana", "fabric",
        "instead of", "chahiye with", "main chahiye", "mein chahiye", "silver embroidery",
        "gold embroidery", "kaam", "embroidery", "silver work", "gold work"
    )
    # Check if a specific product is named across current + history products / catalog
    matched_p = (
        None
        if fresh_category_browse
        else _match_product_from_message(user_message, _collect_known_products(state))
    )
    if not matched_p and not fresh_category_browse:
        try:
            clean_q = _clean_product_search_query(user_message, plan.get("target_product_query"))
            candidate_matches = await backend_api.search_products({"query": clean_q, "limit": 10})
            matched_p = _match_product_from_message(user_message, candidate_matches or [])
            if not matched_p and candidate_matches and len(candidate_matches) == 1:
                cand = candidate_matches[0]
                cand_name = str(cand.get("name") or "").lower()
                clean_words = [w for w in clean_q.lower().split() if len(w) > 2]
                if clean_words and any(w in cand_name for w in clean_words):
                    matched_p = cand
        except Exception:
            pass
    if matched_p:
        selected_product_id = str(matched_p.get("product_id") or matched_p.get("id") or "")
        detail_cues = ("rate", "price", "kitna", "kitne", "detail", "details", "batao", "cost", "range", "wali", "wala")
        if any(cue in user_message.lower() for cue in detail_cues) or intent in ("product_search", "general", "style_advice"):
            if "get_product_details" not in required_steps:
                required_steps = list(dict.fromkeys([*required_steps, "get_product_details"]))

    base_prod = matched_p
    if not base_prod and selected_product_id:
        p_details = state.get("product_details")
        if isinstance(p_details, dict) and str(p_details.get("product_id")) == str(selected_product_id):
            base_prod = p_details
        else:
            for p in state.get("products") or []:
                if str(p.get("product_id")) == str(selected_product_id):
                    base_prod = p
                    break

    has_details = _has_customization_details(
        user_message,
        selected_product_id,
        state.get("selected_fabric_catalog_code"),
        base_product=base_prod,
    )

    cut_style = plan.get("cut_style") or state.get("cut_style") or _extract_cut_style(user_message)
    # Map common design preferences onto catalogue product_type when missing.
    if cut_style and not (plan.get("product_type") or state.get("product_type")):
        cut_to_type = {
            "Sherwani": "Sherwani",
            "Angrakha": "Sherwani",
            "Achkan": "Sherwani",
            "Bandhgala": "Prince Coat",
            "Prince Coat": "Prince Coat",
            "Tuxedo": "Suits",
            "Three Piece": "Suits",
            "Two Piece": "Suits",
            "Double Breasted": "Suits",
            "Kurta": "Kurta",
        }
        mapped = cut_to_type.get(cut_style)
        if mapped:
            plan["product_type"] = mapped
    selected_fabric_code = state.get("selected_fabric_catalog_code")
    matched_fabric = _match_fabric_from_message(user_message, state.get("fabrics") or [])
    if matched_fabric:
        selected_fabric_code = matched_fabric.get("catalog_code") or selected_fabric_code
    
    # Check if user wants customization
    fabric_just_chosen = bool(matched_fabric) or _is_fabric_choose_message(user_message)
    in_custom_flow = (
        any(cue in user_message.lower() for cue in _customization_cues)
        or intent in ("custom_design", "custom_product_variation")
        or bool(state.get("customization_stage"))
        or bool(state.get("selected_fabric_catalog_code"))
        or fabric_just_chosen
        or _has_custom_image(state)
    )
    # Colour answers during negotiation are NOT bespoke redesigns.
    if awaiting_color or "calculate_negotiation_offer" in required_steps:
        in_custom_flow = False
    # Short like/colour confirm of a selected piece is not a custom request.
    if selected_product_id and _is_like_confirm(user_message) and not any(
        cue in user_message.lower() for cue in _customization_cues
    ) and not fabric_just_chosen:
        in_custom_flow = False
    
    # CRITICAL: When customizing a specific product ("customize RIVIERA using this fabric"),
    # extract fabric FROM that product if not already selected
    if in_custom_flow and base_prod and not selected_fabric_code:
        product_fabric_id = base_prod.get("fabric_id")
        if product_fabric_id:
            selected_fabric_code = str(product_fabric_id)
            logger.info(
                "Extracted fabric from base product for customization: %s from %s session_id=%s",
                selected_fabric_code,
                base_prod.get("name", "?")[:30],
                state.get("session_id"),
            )
    
    # Path B (selected product): fabric alone can count as concrete specs.
    # Path A (fabric-first, no product): fabric choose is NOT enough — still need design preference.
    if in_custom_flow and selected_product_id and selected_fabric_code:
        has_details = _has_customization_details(
            user_message,
            selected_product_id,
            selected_fabric_code,
            base_product=base_prod,
        )
    elif in_custom_flow and not selected_product_id:
        # Path A: ready to generate only when fabric + design preference (cut / garment) exist.
        has_details = bool(selected_fabric_code and (cut_style or plan.get("product_type") or state.get("product_type")))
    
    customization_stage = state.get("customization_stage")
    customization_note = None
    wants_revision = _is_design_revision_request(user_message)

    if in_custom_flow:
        if selected_product_id:
            intent = "custom_product_variation"
        else:
            intent = "custom_design"
        sales_stage = "customization"
        # CRITICAL: Clear products list — customization conversations should never show product carousels
        session_products = []
        # Remove search_products from required_steps — we're customizing, not browsing
        required_steps = [s for s in required_steps if s != "search_products"]
        # Fabric card Choose is a selection, not another fabric browse.
        if fabric_just_chosen or selected_fabric_code:
            required_steps = [s for s in required_steps if s != "search_fabrics"]

        if has_details or wants_revision:
            if "generate_custom_design" not in required_steps:
                if selected_product_id and "get_product_details" not in required_steps and not wants_revision:
                    required_steps = ["get_product_details", "generate_custom_design"]
                else:
                    required_steps = list(dict.fromkeys([*required_steps, "generate_custom_design"]))
        else:
            required_steps = [s for s in required_steps if s != "generate_custom_design"]
            if selected_product_id and "get_product_details" not in required_steps:
                required_steps = list(dict.fromkeys([*required_steps, "get_product_details"]))

        customization_stage, required_steps, customization_note = _apply_customization_stages(
            selected_product_id=selected_product_id,
            has_details=has_details,
            required_steps=required_steps,
            state=state,
            event_type=plan.get("event_type") or state.get("event_type"),
            color=plan.get("color") or state.get("color"),
            product_type=plan.get("product_type") or state.get("product_type"),
            cut_style=cut_style,
            selected_fabric_code=selected_fabric_code,
            fabrics=list(state.get("fabrics") or []),
            wants_revision=wants_revision,
        )
        if customization_note:
            variation_note = customization_note
            product_variation_note = customization_note

    if not has_details and not wants_revision and "generate_custom_design" in required_steps:
        required_steps = [s for s in required_steps if s != "generate_custom_design"]

    already_measured = measurements_complete(state)
    pending_measurement = (state.get("measurement_result") or {}).get("status") == "pending"
    likes_bespoke = bool(
        _has_custom_image(state)
        and _is_like_confirm(user_message)
        and not wants_revision
        and not _is_design_revision_request(user_message)
    )
    # Bespoke mockup approved → checkout directly (size/measurements are on checkout page).
    if likes_bespoke or (
        _has_custom_image(state)
        and _is_explicit_checkout_request(user_message)
        and not wants_revision
    ):
        required_steps = [
            s
            for s in required_steps
            if s
            not in (
                "collect_measurements",
                "validate_measurements",
                "generate_custom_design",
                "search_fabrics",
                "search_products",
            )
        ]
        if "close_sale" not in required_steps:
            required_steps = list(dict.fromkeys([*required_steps, "close_sale"]))
        intent = "closing"
        sales_stage = "closing"
        buying_intent = "ready_to_buy"
        customization_stage = "checkout"
        variation_note = (
            "Customer approved the bespoke design. Hand them to secure checkout now. "
            "Do NOT ask for size or body measurements in chat."
        )
        product_variation_note = variation_note
    else:
        wants_measurement = bool(
            infer_measurement_path(user_message, state.get("size_chart"))
            or pending_measurement
        )
        if not already_measured:
            # Design revision must regenerate first — do not divert to measurements this turn.
            if wants_revision and "generate_custom_design" in required_steps:
                required_steps = [s for s in required_steps if s != "collect_measurements"]
            elif _has_custom_image(state) and not wants_revision:
                # Never auto-open measurement collection after a bespoke image —
                # checkout page owns size/naap. Only collect if they typed measurements.
                required_steps = [
                    s for s in required_steps if s not in ("collect_measurements", "validate_measurements")
                ]
                if wants_measurement and infer_measurement_path(user_message, state.get("size_chart")):
                    required_steps = _insert_step(required_steps, "collect_measurements", before="close_sale")
            elif buying_intent == "ready_to_buy" and selected_product_id:
                required_steps = _insert_step(required_steps, "collect_measurements", before="close_sale")
            elif wants_measurement:
                required_steps = _insert_step(required_steps, "collect_measurements", before="close_sale")

    # Named / selected piece detail ask: never re-run catalog search.
    # FE expects product_details + empty products (no carousel under the detail card).
    if selected_product_id and _is_product_detail_request(user_message):
        required_steps = [s for s in required_steps if s != "search_products"]
        if "get_product_details" not in required_steps:
            required_steps = ["get_product_details", *required_steps]
        keep = {
            "get_product_details",
            "generate_custom_design",
            "close_sale",
            "calculate_negotiation_offer",
            "check_inventory",
            "collect_measurements",
            "validate_measurements",
        }
        # Soft detail-only turns: drop unrelated browse steps.
        if not any(s in required_steps for s in keep - {"get_product_details"}):
            required_steps = ["get_product_details"]
        else:
            required_steps = [s for s in required_steps if s in keep]
            if "get_product_details" not in required_steps:
                required_steps = ["get_product_details", *required_steps]
        sales_stage = "detail"
        profile.sales_stage = "detail"  # type: ignore[assignment]
        session_products = []
        if not product_interest_note and not product_variation_note:
            product_interest_note = (
                "Customer asked for details of the selected piece. "
                "Present that piece only (name, price, fabric, colors, sizes, product variations). "
                "Do NOT list other catalog options or say 'here are our options'."
            )

    if selected_product_id:
        profile.selected_product_id = selected_product_id
        profile_dict["selected_product_id"] = selected_product_id

    if selected_fabric_code:
        profile.selected_fabric_catalog_code = selected_fabric_code
        profile_dict["selected_fabric_catalog_code"] = selected_fabric_code
    if cut_style:
        profile_dict["cut_style"] = cut_style
    if customization_stage:
        profile_dict["customization_stage"] = customization_stage
    profile_dict["sales_stage"] = sales_stage
    # Refresh profile model after fabric/cut updates
    try:
        profile = CustomerProfileSchema(**{**profile.model_dump(), **{
            k: profile_dict[k]
            for k in (
                "selected_product_id",
                "selected_fabric_catalog_code",
                "sales_stage",
            )
            if k in profile_dict
        }})
    except Exception:
        pass

    result = {
        "messages": [{"role": "user", "content": user_message}],
        "intent": intent,
        "sales_stage": sales_stage,
        "buying_intent": buying_intent,
        "objection_type": objection_type,
        "customer_profile": profile.model_dump(),
        "required_steps": required_steps,
        "current_step_index": 0,
        "handover_reason": handover_reason,
        "event_type": _normalize_event_type_label(
            plan.get("event_type") or state.get("event_type")
        ),
        "product_type": resolve_category_against_catalog(
            plan.get("product_type") or state.get("product_type"),
            catalog_categories,
        ),
        "selected_variation_id": selected_variation_id or state.get("selected_variation_id"),
        "selected_variation_name": selected_variation_name or state.get("selected_variation_name"),
        "selected_product_variation_id": selected_product_variation_id,
        "selected_product_variation_name": selected_product_variation_name,
        "product_variations": summarize_product_variations_for_prompt(product_variations),
        "product_variation_note": product_variation_note,
        "product_interest_note": product_interest_note,
        "target_product_query": plan.get("target_product_query"),
        "color": plan.get("color") if plan.get("color") is not None else state.get("color"),
        "size": plan.get("size") or state.get("size"),
        "quantity": plan.get("quantity") or state.get("quantity") or 1,
        "budget": plan.get("budget") if plan.get("budget") is not None else state.get("budget"),
        "wedding_date": _normalize_wedding_date(
            plan.get("wedding_date") or state.get("wedding_date")
        ),
        "height": plan.get("height") or state.get("height"),
        "chest": plan.get("chest") or state.get("chest"),
        "waist": plan.get("waist") or state.get("waist"),
        "shoulder": plan.get("shoulder") or state.get("shoulder"),
        "sleeve": plan.get("sleeve") or state.get("sleeve"),
        "jacket_length": plan.get("jacket_length") or state.get("jacket_length"),
        "measurement_path": plan.get("measurement_path") or state.get("measurement_path"),
        "size_chart": state.get("size_chart"),
        "body_measurements": state.get("body_measurements"),
        "customization_stage": customization_stage,
        "cut_style": cut_style,
        "selected_fabric_catalog_code": selected_fabric_code,
        "selected_product_id": selected_product_id,
        "customer_contact": customer_contact,
        "handover_pending": handover_pending,
        "products": session_products,
        "product_details": state.get("product_details"),
        "inventory_result": state.get("inventory_result"),
        "negotiation_state": state.get("negotiation_state"),
        "fabrics": state.get("fabrics") or [],
        "catalog_categories": catalog_categories,
        "catalog_variations": catalog_variations,
        "category_variations": summarize_variations_for_prompt(category_variations),
        "discovery_next_slot": next_gap[0] if next_gap else None,
        "checkout_hand_off_note": checkout_hand_off_note,
        "product_interest_note": product_interest_note,
        "suppress_product_ui": suppress_product_ui,
        "catalog_search_note": variation_note or state.get("catalog_search_note"),
        "wants_more_options": wants_more_options,
    }
    logger.info(
        "planner_node finish session_id=%s intent=%s sales_stage=%s buying_intent=%s "
        "objection_type=%s required_steps=%s wants_more=%s declined_slots=%s",
        state.get("session_id"),
        result["intent"],
        result["sales_stage"],
        buying_intent,
        objection_type,
        result["required_steps"],
        wants_more_options,
        profile.declined_slots,
    )
    return result


def next_empty_search_widen(filters: dict[str, Any]) -> dict[str, Any] | None:
    """Return next relaxed filter set when current search returned 0 hits.

    Only drops one constraint per step. Never invents categories.
    Order: color → season → event → budget → minimal (product_type only).
    """
    color = filters.get("color")
    season = filters.get("season")
    event_type = filters.get("event_type")
    budget = filters.get("budget")
    size = filters.get("size")
    fabric = filters.get("fabric")
    product_type = filters.get("product_type")
    if not product_type:
        return None

    base = {
        "product_type": product_type,
        "color": color,
        "season": season,
        "event_type": event_type,
        "budget": budget,
        "size": size,
        "fabric": fabric,
    }
    if color:
        out = {**base, "color": None, "dropped": "color"}
        return out
    if season:
        out = {**base, "season": None, "dropped": "season"}
        return out
    if event_type:
        out = {**base, "event_type": None, "dropped": "event"}
        return out
    if budget is not None or size or fabric:
        out = {
            **base,
            "budget": None,
            "size": None,
            "fabric": None,
            "dropped": "budget_size_fabric",
        }
        return out
    # Already minimal (product_type only) — no further widen.
    if color is None and season is None and event_type is None and budget is None and not size and not fabric:
        return None
    return None


async def _ainvoke_product_search(
    *,
    product_type: str,
    color: str | None,
    event_type: str | None,
    budget: Any,
    size: Any,
    fabric: Any,
    season: Any,
    catalog_categories: list[Any],
    limit: int = 50,
) -> list[dict[str, Any]]:
    return await search_products.ainvoke({
        "query": "",
        "product_type": product_type,
        "color": color,
        "occasion": event_type,
        "event_type": event_type,
        "budget_max": budget,
        "budget": budget,
        "size": size,
        "fabric": fabric,
        "season": season,
        "in_stock": True,
        "limit": limit,
        "offset": 0,
        "catalog_categories": catalog_categories,
    })


@log_node_payload("search_products_node")
async def search_products_node(state: SalesAgentState) -> dict[str, Any]:
    logger.info("search_products_node start %s", _log_state_summary(state))
    user_message = state.get("user_message") or ""
    # Colour filter only if they named a colour — not for "which colours available?".
    requested_color = _extract_explicit_color(user_message)
    if discovery_engine.should_run_product_search(user_message) and not requested_color:
        requested_color = None
    elif not requested_color:
        requested_color = state.get("color")

    product_type = state.get("product_type")
    if not product_type:
        product_type = _message_explicitly_names_garment(
            user_message,
            state.get("catalog_categories") or [],
        )
    if not product_type:
        product_type = _default_category_for_event(state.get("event_type"))

    product_type = resolve_category_against_catalog(
        product_type,
        state.get("catalog_categories") or [],
    ) or product_type
    if not product_type:
        logger.info(
            "search_products_node finish session_id=%s skipped_no_product_type",
            state.get("session_id"),
        )
        return {
            "products": [],
            "scoring_reasons": [],
            "catalog_search_note": (
                "No garment confirmed yet — ask product_type before searching."
            ),
        }

    # Live API: category + colour + event + budget from session preferences.
    profile_dict = dict(state.get("customer_profile") or {})
    profile_dict["session_id"] = state.get("session_id", "default")
    event_type = state.get("event_type")
    season = map_season_for_catalog_api(
        profile_dict.get("derived_season")
        or state.get("season")
        or derive_season_from_text(user_message)
    )
    fabric = state.get("selected_fabric_catalog_code") or state.get("fabric")
    catalog_categories = state.get("catalog_categories") or []
    budget = state.get("budget")
    size = state.get("size")
    widened_drops: list[str] = []
    search_limit = 100 if state.get("wants_more_options") else 50

    # Catalogue currency only — no market FX detection.
    products: list[dict[str, Any]] = []
    backend_unavailable = False
    try:
        products = await _ainvoke_product_search(
            product_type=product_type,
            color=requested_color,
            event_type=event_type,
            budget=budget,
            size=size,
            fabric=fabric,
            season=season,
            catalog_categories=catalog_categories,
            limit=search_limit,
        )
    except BackendAPIError:
        logger.exception(
            "search_products_node backend error session_id=%s — retrying once without event filter",
            state.get("session_id"),
        )
        backend_unavailable = True
        try:
            products = await _ainvoke_product_search(
                product_type=product_type,
                color=requested_color,
                event_type=None,
                budget=budget,
                size=size,
                fabric=fabric,
                season=None,
                catalog_categories=catalog_categories,
                limit=search_limit,
            )
            if products:
                backend_unavailable = False
                widened_drops.append("backend_retry_no_event")
        except Exception:
            logger.exception("search_products_node backend retry failed")
            products = []
    except Exception:
        logger.exception("search_products_node failed session_id=%s", state.get("session_id"))
        raise

    if backend_unavailable and not products:
        return {
            "products": [],
            "scoring_reasons": [],
            "catalog_search_note": (
                "SHORT REPLY ONLY. Catalogue briefly unavailable. "
                "One short line: apologise and offer to retry — do not invent products."
            ),
        }

    # Empty-only widen: drop one filter at a time until hits or ladder exhausted.
    # Session prefs stay in state; only this API call is relaxed.
    # For initial searches ("dikhao"), aim for at least 3 products; otherwise accept any hit.
    is_initial_dikhao = discovery_engine.should_run_product_search(user_message)
    min_products_threshold = 3 if is_initial_dikhao else 1
    
    search_color = requested_color
    search_season = season
    search_event = event_type
    search_budget = budget
    search_size = size
    search_fabric = fabric
    while len(products) < min_products_threshold:
        nxt = next_empty_search_widen({
            "product_type": product_type,
            "color": search_color,
            "season": search_season,
            "event_type": search_event,
            "budget": search_budget,
            "size": search_size,
            "fabric": search_fabric,
        })
        if not nxt:
            break
        dropped = str(nxt.get("dropped") or "")
        search_color = nxt.get("color")
        search_season = nxt.get("season")
        search_event = nxt.get("event_type")
        search_budget = nxt.get("budget")
        search_size = nxt.get("size")
        search_fabric = nxt.get("fabric")
        logger.info(
            "search_products_node 0 hits, widening drop=%s session_id=%s",
            dropped,
            state.get("session_id"),
        )
        try:
            products = await _ainvoke_product_search(
                product_type=product_type,
                color=search_color,
                event_type=search_event,
                budget=search_budget,
                size=search_size,
                fabric=search_fabric,
                season=search_season,
                catalog_categories=catalog_categories,
                limit=search_limit,
            )
        except Exception:
            logger.exception("search_products widen failed drop=%s", dropped)
            products = []
        if products:
            widened_drops.append(dropped)
            # Keep widening if we haven't hit the minimum threshold yet
            if len(products) >= min_products_threshold:
                break
        else:
            widened_drops.append(dropped)

    # Prince Coat alias fallback if exact category miss.
    if not products and product_type and "prince" in product_type.lower():
        for alias in ("Prince Coat", "Prince Suit", "Jackets"):
            try:
                products = await _ainvoke_product_search(
                    product_type=alias,
                    color=search_color,
                    event_type=search_event,
                    budget=search_budget,
                    size=search_size,
                    fabric=search_fabric,
                    season=search_season,
                    catalog_categories=catalog_categories,
                )
            except Exception:
                logger.exception("prince alias search failed alias=%s", alias)
                products = []
            if products:
                product_type = alias
                widened_drops.append(f"prince_alias:{alias}")
                break

    # LLM-detected "aur dikhao" / "aur nhi hain" → broaden to full category when no new options.
    wants_more = state.get("wants_more_options") or False
    if wants_more:
        seen_early = set(str(x) for x in (state.get("shown_product_ids") or []))
        unseen_early = [
            p for p in products
            if str(p.get("product_id") or p.get("id") or "") not in seen_early
        ]
        # If nothing new with current filters, drop event/size/fabric and show full category.
        if not unseen_early:
            logger.info(
                "wants_more_options: no unseen in current=%s, broadening to full category session_id=%s",
                len(products),
                state.get("session_id"),
            )
            try:
                broader = await _ainvoke_product_search(
                    product_type=product_type,
                    color=requested_color,
                    event_type=None,
                    budget=budget,
                    size=None,
                    fabric=None,
                    season=None,
                    catalog_categories=catalog_categories,
                )
            except Exception:
                logger.exception("broader category search failed")
                broader = []
            if broader:
                products = broader
                widened_drops.append("event_for_more_options")
                logger.info(
                    "broadened to full category: now have %s products session_id=%s",
                    len(products),
                    state.get("session_id"),
                )

    profile = CustomerProfileSchema(**profile_dict)
    if not profile.derived_season:
        profile.derived_season = derive_season_from_text(profile.wedding_date) or profile_dict.get(
            "derived_season"
        )

    # Deduplicate against products already shown — but only when the customer
    # explicitly asks for *new* / other options. Filter refinements (light colors,
    # budget change, etc.) must re-present matching rows even if seen before.
    seen_ids = set(str(x) for x in (state.get("shown_product_ids") or []))
    unseen_products = [
        p for p in products
        if str(p.get("product_id") or p.get("id") or "") not in seen_ids
    ]

    wants_more_final = state.get("wants_more_options") or False
    all_in_cat_shown = bool(products and not unseen_products and wants_more_final)
    if wants_more_final:
        if unseen_products:
            candidate_pool = unseen_products
        elif not seen_ids:
            candidate_pool = products
        else:
            candidate_pool = []
    else:
        # Prefer unseen when available; otherwise re-show current API matches
        candidate_pool = unseen_products if unseen_products else list(products or [])

    shown = select_products_for_display(candidate_pool, limit=MAX_PRODUCTS_TO_SHOW, event_text=None)
    shown = apply_display_currency_list(shown, None)

    selected_product_id = state.get("selected_product_id")
    target_piece = None
    if selected_product_id:
        for p in (shown or products):
            if str(p.get("product_id")) == str(selected_product_id):
                target_piece = p
                break
        if not target_piece and isinstance(state.get("product_details"), dict):
            if str(state.get("product_details", {}).get("product_id")) == str(selected_product_id):
                target_piece = state.get("product_details")

    # Check if user message explicitly targets the selected piece
    lower_msg = (user_message or "").lower()
    this_piece_cues = (
        "is sherwani", "is suit", "is piece", "is dress", "is product", "is mein", "ismein",
        "iski", "iska", "iss mein", "is ka", "is ki", "this piece", "this sherwani", "this suit",
        "this outfit", "this one", "yeh sherwani", "ye sherwani", "yeh suit", "ye suit", "yeh piece", "ye piece"
    )
    is_asking_selected_piece = bool(target_piece and (selected_product_id or any(c in lower_msg for c in this_piece_cues)))

    available_colors: list[str] = []
    for item in shown:
        for c in item.get("available_colors") or []:
            label = str(c).strip()
            if label and label not in available_colors:
                available_colors.append(label)
        primary = item.get("color") or item.get("primary_color")
        if primary:
            label = str(primary).strip()
            if label and label not in available_colors:
                available_colors.append(label)

    catalog_search_note = None
    if all_in_cat_shown:
        budget_str = f" within £{state.get('budget'):,.0f}" if state.get("budget") else ""
        catalog_search_note = (
            f"All available signature ready-to-wear pieces in '{product_type}'{budget_str} have already been presented to the customer in this session. "
            "Politely, warmly, and naturally explain that these are our curated signature pieces currently available in this range. "
            "Invite them warmly: they can select one of the pieces already shown, customize any of those pieces to their liking (changing color, fabric, or embroidery), "
            "or have our master artisans craft a completely bespoke piece tailored to their exact measurements and style within their budget. "
            "NEVER say robotic or negative phrases like 'aur koi ready-made piece nahi hai' or 'sirf yeh shamil hai'. Keep the tone luxurious, welcoming, and helpful."
        )
    elif shown and widened_drops:
        drop_labels = ", ".join(widened_drops)
        catalog_search_note = (
            f"Exact filter match was empty; widened search by relaxing: {drop_labels}. "
            f"SHORT REPLY: ONE line — here are the closest {len(shown)} '{product_type}' piece(s) "
            "(cards show details). Do NOT invent products. Do NOT write fabric/price essays."
        )
    elif requested_color and not shown:
        catalog_search_note = (
            f"No products found for colour '{requested_color}'"
            + (f" in {product_type}" if product_type else "")
            + ". Tell them that colour is not available and ask another colour."
        )
    elif not shown:
        if state.get("budget"):
            # Budget-too-low ladder: acknowledge → adjacent/cheapest → lighter line → accessories → handover
            cheaper: list[dict[str, Any]] = []
            try:
                cheaper = await _ainvoke_product_search(
                    product_type=product_type,
                    color=None,
                    event_type=event_type,
                    budget=None,
                    size=None,
                    fabric=None,
                    season=season,
                    catalog_categories=catalog_categories,
                    limit=20,
                )
                cheaper = sorted(
                    [p for p in cheaper if float(p.get("price") or 0) > 0],
                    key=lambda p: float(p.get("price") or 0),
                )[:5]
            except Exception:
                logger.exception("budget ladder adjacent search failed")
                cheaper = []
            if cheaper:
                shown = apply_display_currency_list(cheaper, None)
                available_colors = []
                for item in shown:
                    for c in item.get("available_colors") or []:
                        label = str(c).strip()
                        if label and label not in available_colors:
                            available_colors.append(label)
                lowest = float(cheaper[0].get("price") or 0)
                gap = max(0.0, lowest - float(state.get("budget") or 0))
                catalog_search_note = (
                    f"BUDGET_TOO_LOW ladder (status=budget_too_low). Customer budget={state.get('budget')}. "
                    f"No pieces in '{product_type}' fit that budget. "
                    f"Acknowledge warmly, then present the nearest affordable alternatives shown "
                    f"(cheapest starts around {int(lowest):,}). "
                    f"If helpful, mention increasing budget by ~{int(gap):,} unlocks this tier, "
                    "or offer lighter Mehndi/festive wear, accessories-only options, or a Style Consultant. "
                    "Do NOT invent prices. Stay in the customer's language. "
                    "Only hand over if they ask for a consultant — always leave a next step."
                )
            else:
                catalog_search_note = (
                    f"BUDGET_TOO_LOW (status=budget_too_low). No products found for '{product_type or 'this category'}' "
                    f"within budget {state.get('budget')} and no adjacent priced pieces available. "
                    "Politely explain, suggest accessories-only / lighter event wear / bespoke lighter work, "
                    "or offer a Style Consultant. Stay in language register. Do not invent catalogue prices."
                )
        else:
            widen_bit = (
                f" Already relaxed filters: {', '.join(widened_drops)}."
                if widened_drops
                else ""
            )
            catalog_search_note = (
                f"No rows for category '{product_type or 'this category'}' with current filters.{widen_bit} "
                "Explain that this specific style/color is currently out of stock or not in the ready-made catalog. "
                "Proactively suggest similar available pieces or our bespoke customization service. "
                "Do NOT invent product names or prices. Offer Style Consultant as one clear next step."
            )
    elif discovery_engine.customer_asked_availability(user_message):
        if is_asking_selected_piece and target_piece:
            piece_name = target_piece.get("name") or "this piece"
            piece_colors: list[str] = []
            for c in target_piece.get("available_colors") or []:
                lbl = str(c).strip()
                if lbl and lbl not in piece_colors:
                    piece_colors.append(lbl)
            prim = target_piece.get("color") or target_piece.get("primary_color")
            if prim and str(prim).strip() and str(prim).strip() not in piece_colors:
                piece_colors.append(str(prim).strip())

            if piece_colors:
                available_colors = piece_colors
                catalog_search_note = (
                    f"Customer asked what colours are available for '{piece_name}'. "
                    f"STRICT API CATALOG GROUNDING: According to the catalog data, '{piece_name}' is available ONLY in: {', '.join(piece_colors)}. "
                    "State these exact colours faithfully and ask which shade they would love to see or try. Do NOT invent any other colours."
                )
            else:
                available_colors = []
                catalog_search_note = (
                    f"Customer asked what colours are available for '{piece_name}'. "
                    f"STRICT API CATALOG GROUNDING: In our database, '{piece_name}' has NO other color options listed (available_colors is empty). "
                    "It is crafted and available exclusively in its signature design/colour as displayed in the catalog. "
                    "DO NOT invent colours (do NOT say black, navy, maroon, golden, white, sand, etc.) and DO NOT attribute colours of other pieces to this item! "
                    f"Politely and honestly inform the customer that '{piece_name}' is crafted in its signature tone as shown. "
                    "If they are looking for other specific colours, let them know that different designs in our collection feature other shades "
                    "(for example, SHEHANSHAH is available in Black, Navy, Maroon, Golden; Osiria is available in White), "
                    "or we can custom-tailor their desired colour and fabric through our bespoke atelier service."
                )
        else:
            colors_bit = (
                f" Available colours across shown pieces: {', '.join(available_colors)}."
                if available_colors
                else " Mention the specific pieces and colours shown."
            )
            catalog_search_note = (
                f"Customer asked what colours/options are available in {product_type or 'this collection'}. "
                f"We have {len(shown)} option(s) in this collection.{colors_bit} "
                "Clarify that colours vary by design (e.g. Shehanshah offers Black/Navy/Maroon/Golden, Osiria in White), "
                "and ask which shade or style they would like to explore!"
            )
    else:
        # Browse search: category variations OK. Never invent product-level cuts here.
        selected_pid = state.get("selected_product_id")
        if selected_pid:
            variations_mention = (
                " Customer already selected a piece — speak only about that product's own "
                "variations/colours/sizes from product_details; do NOT list category cuts "
                "(Achkan/Angrakha/Tuxedo) as if they were options of this SKU."
            )
        else:
            # Do NOT push category cut names into the spoken reply — cards carry the catalogue.
            variations_mention = ""
        catalog_search_note = (
            f"SHORT REPLY ONLY. recommendations has {len(shown)} '{product_type or 'catalogue'}' piece(s) — "
            "frontend cards will display name/image/price. "
            "Your text must be ONE short line introducing that category only "
            f"(e.g. English: 'Here are our {product_type or 'atelier'} pieces:' / "
            f"Roman Urdu: 'Yeh hain hamare {product_type or 'atelier'} options:'). "
            "Do NOT list product names, fabrics, seasons, prices, colours, or category cuts in text. "
            "Do NOT write stories or multi-paragraph replies. Optional: one short question which piece to open. "
            "If price_unavailable or currency_needs_consultant is true on a row, cards handle display — "
            "do not invent prices in text."
            f"{variations_mention}"
        )

    shown_ids = list(state.get("shown_product_ids") or [])
    for item in shown:
        pid = str(item.get("product_id") or item.get("id") or "")
        if pid and pid not in shown_ids:
            shown_ids.append(pid)
    profile.products_shown_ids = shown_ids
    profile.selected_product_id = str(selected_product_id) if selected_product_id else None
    profile.sales_stage = "recommendation"

    logger.info(
        "search_products_node finish session_id=%s api_count=%s shown=%s color=%s colors=%s note=%s",
        state.get("session_id"),
        len(products),
        len(shown),
        requested_color,
        available_colors,
        catalog_search_note,
    )
    return {
        "products": shown,
        "shown_product_ids": shown_ids,
        "selected_product_id": selected_product_id,
        "recommendations": shown,
        "scoring_reasons": [],
        "sales_stage": "recommendation",
        "customer_profile": profile.model_dump(),
        "style_context": [],
        "color": requested_color,
        "product_type": product_type,
        "catalog_search_note": catalog_search_note,
        "available_colors_summary": available_colors,
    }


def _fabric_matches_category(fabric: dict[str, Any], want: str) -> bool:
    want_l = want.lower()
    cats = fabric.get("dress_category") or fabric.get("category")
    if isinstance(cats, list) and cats:
        return any(want_l in str(c).lower() for c in cats)
    if cats and want_l in str(cats).lower():
        return True
    return False


def _fabric_season_list(fabric: dict[str, Any]) -> list[str]:
    seasons = fabric.get("seasons") if isinstance(fabric.get("seasons"), list) else None
    if seasons:
        return [str(s).strip() for s in seasons if str(s).strip()]
    raw = str(fabric.get("season") or "").strip()
    if not raw:
        return []
    return [part.strip() for part in re.split(r"\s*,\s*", raw) if part.strip()]


def _fabric_matches_season(fabric: dict[str, Any], season_label: str | None) -> bool:
    """True only if fabric is tagged for the requested catalogue season (or All Season)."""
    if not season_label:
        return True
    want = season_label.lower()
    labels = [s.lower() for s in _fabric_season_list(fabric)]
    if not labels:
        return False  # unknown season — do not guess into a summer/winter ask
    if any("all season" in s for s in labels):
        return True
    if any(want == s or want in s or s in want for s in labels):
        return True
    if "summer" in want or "spring" in want:
        return any("summer" in s or "spring" in s for s in labels)
    if "winter" in want or "autumn" in want:
        return any("winter" in s or "autumn" in s for s in labels)
    return False


def _align_fabric_season_display(
    fabric: dict[str, Any],
    season_label: str | None,
) -> dict[str, Any]:
    """
    Dual-season rows often list Autumn/Winter first; FE badges use the first tag.
    For a summer ask, put Spring / Summer (or All Season) first so the card matches the request.
    """
    if not season_label or not isinstance(fabric, dict):
        return fabric
    labels = _fabric_season_list(fabric)
    if not labels:
        return fabric
    want = season_label.lower()
    preferred: list[str] = []
    rest: list[str] = []
    for label in labels:
        low = label.lower()
        hit = (
            want == low
            or want in low
            or low in want
            or ("summer" in want and "summer" in low)
            or ("spring" in want and "spring" in low)
            or ("winter" in want and "winter" in low)
            or ("autumn" in want and "autumn" in low)
            or "all season" in low
        )
        (preferred if hit else rest).append(label)
    if not preferred:
        return fabric
    ordered = preferred + rest
    out = dict(fabric)
    out["seasons"] = ordered
    out["season"] = ordered[0]  # FE badge — show the season the customer asked for
    return out


def _fabric_season_sort_key(fabric: dict[str, Any], season_label: str | None) -> tuple[int, str]:
    """Prefer rows whose primary season matches the ask, then All Season, then dual."""
    name = str(fabric.get("name") or "")
    if not season_label:
        return (2, name)
    labels = [s.lower() for s in _fabric_season_list(fabric)]
    want = season_label.lower()
    if labels and (want in labels[0] or labels[0] in want):
        return (0, name)
    if any("all season" in s for s in labels):
        return (1, name)
    return (2, name)


def _fabric_matches_color(fabric: dict[str, Any], color: str | None) -> bool:
    if not color:
        return True
    want = color.lower().strip()
    colors = [str(c).lower() for c in (fabric.get("available_colors") or []) if str(c).strip()]
    primary = str(fabric.get("color") or fabric.get("primary_color") or "").lower()
    name = str(fabric.get("name") or "").lower()
    if want in colors or want == primary or want in name:
        return True
    # soft: "grey" ↔ "gray", partial token
    return any(want in c or c in want for c in colors)


@log_node_payload("search_fabrics_node")
async def search_fabrics_node(state: SalesAgentState) -> dict[str, Any]:
    logger.info("search_fabrics_node start session_id=%s", state.get("session_id"))
    profile_dict = dict(state.get("customer_profile") or {})
    user_message = state.get("user_message") or ""
    # Season from month/date already on profile, or derive from wedding_date / message.
    derived = profile_dict.get("derived_season") or derive_season_from_text(
        state.get("wedding_date") or profile_dict.get("wedding_date")
    )
    if not derived:
        derived = derive_season_from_text(user_message)
    if derived and not profile_dict.get("derived_season"):
        profile_dict["derived_season"] = derived
    season_filter = map_season_for_catalog_api(derived)

    # Colour: explicit in this message, else session/profile (same idea as products).
    color_filter = _extract_explicit_color(user_message) or state.get("color") or profile_dict.get("color")
    if color_filter:
        color_filter = str(color_filter).strip().title()
        profile_dict["color"] = color_filter

    preferred = profile_dict.get("preferred_fabrics") or []
    fabric_type = preferred[0] if isinstance(preferred, list) and preferred else None

    # Filter fabrics by garment category for the event (Nikah → Sherwani, not Suits).
    event_type = state.get("event_type") or profile_dict.get("event_type")
    product_type = state.get("product_type") or profile_dict.get("product_type")
    if not product_type:
        product_type = _message_explicitly_names_garment(
            user_message,
            state.get("catalog_categories") or [],
        )
    if not product_type:
        product_type = _default_category_for_event(event_type)
    product_type = resolve_category_against_catalog(
        product_type,
        state.get("catalog_categories") or [],
    ) or product_type
    if product_type:
        profile_dict["product_type"] = product_type

    async def _fetch(**overrides: Any) -> list[dict[str, Any]]:
        payload = {
            "fabric_type": fabric_type,
            "color": color_filter,
            "category": product_type,
            "season": season_filter,
            "limit": 100,
            "offset": 0,
        }
        payload.update(overrides)
        return list(await search_fabrics.ainvoke(payload) or [])

    widened_drops: list[str] = []
    catalog_search_note: str | None = None
    prior_fabrics = [
        f for f in (state.get("fabrics") or []) if isinstance(f, dict)
    ]
    wants_more_fabrics = bool(state.get("wants_more_options")) and bool(prior_fabrics)
    try:
        if wants_more_fabrics:
            # Next page after what was already shown in-session.
            fabrics = await _fetch(offset=len(prior_fabrics), limit=100)
            if fabrics:
                seen = {
                    str(f.get("catalog_code") or f.get("id") or "")
                    for f in prior_fabrics
                }
                fresh = [
                    f
                    for f in fabrics
                    if str(f.get("catalog_code") or f.get("id") or "") not in seen
                ]
                fabrics = prior_fabrics + fresh if fresh else prior_fabrics + fabrics
            else:
                # No next page — re-show full prior set (frontend scrolls all).
                fabrics = prior_fabrics
                catalog_search_note = (
                    "SHORT REPLY: All available fabric swatches for this garment are already on screen. "
                    "Ask them to scroll the fabric cards and Choose one — do not invent more cloths."
                )
        else:
            fabrics = await _fetch()
    except Exception:
        logger.exception("search_fabrics_node failed session_id=%s", state.get("session_id"))
        raise

    # Empty-only widen (same ladder idea as products): relax one filter at a time.
    if not fabrics and season_filter:
        try:
            fabrics = await _fetch(season=None)
            if fabrics:
                widened_drops.append("season")
                fabrics = [f for f in fabrics if isinstance(f, dict) and _fabric_matches_season(f, season_filter)]
        except Exception:
            logger.exception("search_fabrics_node season widen failed")
    if not fabrics and color_filter:
        try:
            fabrics = await _fetch(color=None, season=season_filter)
            if fabrics:
                widened_drops.append("color")
                fabrics = [f for f in fabrics if isinstance(f, dict) and _fabric_matches_color(f, color_filter)]
        except Exception:
            logger.exception("search_fabrics_node color widen failed")
    if not fabrics and product_type:
        try:
            fabrics = await _fetch(category=None, season=season_filter, color=color_filter)
            if fabrics:
                widened_drops.append("category")
        except Exception:
            logger.exception("search_fabrics_node category widen failed")

    # Hard filter: when we know the garment/event category, NEVER keep wrong-category rows
    # (unless we deliberately widened past category).
    if product_type and fabrics and "category" not in widened_drops:
        before = len(fabrics)
        fabrics = [
            f for f in fabrics
            if isinstance(f, dict) and _fabric_matches_category(f, product_type)
        ]
        logger.info(
            "search_fabrics_node category hard-filter %s → kept=%s dropped=%s",
            product_type,
            len(fabrics),
            before - len(fabrics),
        )
    if season_filter and fabrics:
        # Always enforce season client-side — API dual-tags can still include winter-only noise.
        before = len(fabrics)
        fabrics = [
            f for f in fabrics if isinstance(f, dict) and _fabric_matches_season(f, season_filter)
        ]
        logger.info(
            "search_fabrics_node season hard-filter %s → kept=%s dropped=%s",
            season_filter,
            len(fabrics),
            before - len(fabrics),
        )
    if color_filter and fabrics and "color" not in widened_drops:
        fabrics = [f for f in fabrics if isinstance(f, dict) and _fabric_matches_color(f, color_filter)]

    if season_filter and fabrics:
        fabrics = [_align_fabric_season_display(f, season_filter) for f in fabrics]
        fabrics.sort(key=lambda f: _fabric_season_sort_key(f, season_filter))

    if catalog_search_note is None and fabrics and widened_drops:
        catalog_search_note = (
            f"Exact fabric filter was empty; widened by relaxing: {', '.join(widened_drops)}. "
            f"SHORT REPLY: ONE line — here are {len(fabrics)} fabric swatch(es) "
            f"(season={season_filter or 'any'}, colour={color_filter or 'any'}). "
            "Frontend cards show the swatches. Do NOT invent fabric names."
        )
    elif catalog_search_note is None and not fabrics:
        event_label = event_type or "this event"
        cat_bit = f" for '{product_type}'" if product_type else ""
        season_bit = f", season {season_filter}" if season_filter else ""
        color_bit = f", colour {color_filter}" if color_filter else ""
        catalog_search_note = (
            f"No live fabric swatches matched{cat_bit}{season_bit}{color_bit} "
            f"(event: {event_label}). "
            "Honestly tell the customer those swatches are not listed yet. "
            "Offer: (1) browse ready-made catalogue pieces, "
            "(2) share a fabric photo / preference for Style Consultant sourcing, "
            "or (3) try another season/colour. Stay in the customer's language. "
            "One short polite reply — no invented fabric names."
        )
        logger.info(
            "search_fabrics_node empty category=%s event=%s season=%s color=%s",
            product_type,
            event_type,
            season_filter,
            color_filter,
        )

    # Never auto-pick a fabric on browse — customer / frontend chooses.
    selected_code = state.get("selected_fabric_catalog_code")
    profile_dict["session_id"] = state.get("session_id", "default")
    if selected_code:
        profile_dict["selected_fabric_catalog_code"] = selected_code
    # Pure fabric browse stays on recommendation so product UI suppress does not hide swatches.
    in_custom_flow = (
        state.get("sales_stage") == "customization"
        or state.get("customization_stage")
        or bool(state.get("selected_product_id"))
    )
    next_stage = "customization" if in_custom_flow else "recommendation"
    profile_dict["sales_stage"] = next_stage

    logger.info(
        "search_fabrics_node finish session_id=%s fabric_count=%s season=%s color=%s "
        "category=%s selected=%s",
        state.get("session_id"),
        len(fabrics),
        season_filter,
        color_filter,
        product_type,
        selected_code,
    )
    out: dict[str, Any] = {
        "fabrics": fabrics,
        "selected_fabric_catalog_code": selected_code,
        "sales_stage": next_stage,
        "customization_stage": "fabric_selection",
        "product_type": product_type,
        "wedding_date": state.get("wedding_date") or profile_dict.get("wedding_date"),
        "color": color_filter,
        "catalog_search_note": catalog_search_note or state.get("catalog_search_note"),
        "customer_profile": profile_dict,
        "style_context": [
            {
                "fabric_custom": True,
                "rule": (
                    "Never invent fabric names, prices, meters, or weight. "
                    "Only discuss fabrics returned in context.fabrics. "
                    "If fabrics is empty, follow catalog_search_note — do not offer Suit fabrics for Nikah/Sherwani. "
                    "When context.price_quote.ok and source=calculator, quote list_price/unit_price in GBP only — "
                    "never floor_price, markup, or invented figures."
                ),
            }
        ],
    }
    if selected_code:
        quote_patch = await _refresh_price_quote({**dict(state), **out})
        out.update(quote_patch)
        if quote_patch.get("customer_profile"):
            out["customer_profile"] = {
                **profile_dict,
                **quote_patch["customer_profile"],
            }
    return out


@log_node_payload("get_product_details_node")
async def get_product_details_node(state: SalesAgentState) -> dict[str, Any]:
    user_msg = state.get("user_message") or ""
    known = _collect_known_products(state)
    named_match = _match_product_from_message(user_msg, known)
    if not named_match:
        try:
            clean_q = _clean_product_search_query(user_msg, state.get("target_product_query"))
            candidates = await backend_api.search_products({"query": clean_q, "limit": 10})
            named_match = _match_product_from_message(user_msg, candidates or [])
            if not named_match and candidates and len(candidates) == 1:
                cand = candidates[0]
                cand_name = str(cand.get("name") or "").lower()
                clean_words = [w for w in clean_q.lower().split() if len(w) > 2]
                if clean_words and any(w in cand_name for w in clean_words):
                    named_match = cand
        except Exception:
            pass

    if named_match:
        product_id = str(named_match.get("product_id") or named_match.get("id") or "")
    else:
        product_id = state.get("selected_product_id")
        if not product_id and known:
            # Prefer a name hit already attempted; else do not guess first of list
            product_id = None
        if not product_id and state.get("products"):
            # Only fall back to products[0] when there is a single candidate
            if len(state["products"]) == 1:
                product_id = state["products"][0].get("product_id")
    if not product_id:
        existing_details = state.get("product_details") or {}
        if isinstance(existing_details, dict) and not existing_details.get("error"):
            product_id = existing_details.get("product_id")
    if not product_id:
        logger.info(
            "get_product_details_node finish session_id=%s no_product_selected",
            state.get("session_id"),
        )
        # Return empty details (not an error stub) so FE does not render a blank card
        return {"product_details": None, "selected_product_id": state.get("selected_product_id")}
    try:
        details = await get_product_details.ainvoke({"product_id": product_id})
    except Exception:
        logger.exception(
            "get_product_details_node failed session_id=%s product_id=%s",
            state.get("session_id"),
            product_id,
        )
        raise
    if not isinstance(details, dict):
        details = {}

    product_variations = list(details.get("variations") or [])
    user_message = state.get("user_message") or ""
    selected_pv_id = state.get("selected_product_variation_id")
    selected_pv_name = state.get("selected_product_variation_name")

    matched = resolve_variation_against_catalog(user_message, product_variations)
    if not matched and state.get("selected_variation_name"):
        matched = resolve_variation_against_catalog(
            str(state.get("selected_variation_name")),
            product_variations,
        )
    if not matched and selected_pv_name:
        matched = resolve_variation_against_catalog(str(selected_pv_name), product_variations)
    if not matched and selected_pv_id:
        for row in product_variations:
            if str(row.get("id")) == str(selected_pv_id):
                matched = row
                break
    # Single style option → auto-select.
    if not matched and len(product_variations) == 1:
        matched = product_variations[0]

    out: dict[str, Any] = {
        "product_details": details,
        "selected_product_id": product_id,
        "product_variations": summarize_product_variations_for_prompt(product_variations),
    }
    # Detail / confirm / negotiation: never append a carousel row under the selected piece.
    stage = (state.get("sales_stage") or "").lower()
    confirm_turn = (
        stage in ("detail", "closing", "negotiation")
        or _is_product_detail_request(user_msg)
        or _is_like_confirm(user_msg)
        or _is_piece_or_colour_confirm(user_msg)
        or _negotiation_awaiting_color(state)
        or bool(state.get("product_interest_note"))
    )
    if confirm_turn:
        out["products"] = []
        if stage != "negotiation":
            out["sales_stage"] = "detail" if stage != "closing" else stage
    else:
        cur_products = list(state.get("products") or [])
        if details and not any(str(p.get("product_id") or p.get("id")) == str(product_id) for p in cur_products):
            cur_products.append(details)
            out["products"] = cur_products

    if matched:
        selected_pv_id = str(matched.get("id") or "")
        selected_pv_name = str(matched.get("name") or "")
        details = apply_product_variation_to_details(details, matched)
        out["product_details"] = details
        out["selected_product_variation_id"] = selected_pv_id
        out["selected_product_variation_name"] = selected_pv_name
        # CRITICAL: Clear product_variations so frontend shows detail card, NOT variation picker
        # Customer has chosen a variation — no need to show selector again
        out["product_variations"] = []
        out["sales_stage"] = "detail"
        # Checkout URL only from close_sale_node — never catalog product_url.
        if state.get("checkout_hand_off_note") or _is_explicit_checkout_request(user_message):
            out["product_variation_note"] = None
            out["product_interest_note"] = None
        elif state.get("product_interest_note") or _is_piece_or_colour_confirm(user_message):
            out["product_interest_note"] = state.get("product_interest_note") or _build_product_interest_note(
                product_name=str(details.get("name") or ""),
                needs_variation=False,
            )
    if _is_product_variation_inquiry(user_message):
        pname = str(details.get("name") or "this piece")
        out["checkout_hand_off_note"] = None
        if product_variations:
            names = [str(v.get("name")) for v in product_variations if v.get("name")]
            out["product_variation_note"] = (
                f"Customer asked for variations of '{pname}'. "
                f"List ONLY this product's actual variations: {', '.join(names)}. "
                "Do NOT list category-level styles or other garments."
            )
        else:
            colors = [str(c) for c in (details.get("available_colors") or []) if str(c).strip()]
            sizes = [str(s) for s in (details.get("available_sizes") or []) if str(s).strip()]
            fabric = details.get("fabric") or ""
            out["product_variation_note"] = (
                f"Customer asked for variations of '{pname}'. "
                f"This specific piece is available in colors: {', '.join(colors) if colors else 'its signature tone'}, "
                f"sizes: {', '.join(sizes) if sizes else 'standard & custom tailoring'}, and fabric: {fabric}. "
                "It does not have separate sub-style cut variations. Present its color/size options and bespoke tailoring. "
                "CRITICAL: Do NOT list category variations (like Achkan, Angrakha, Prince Coat, Tuxedo) as variations of this piece!"
            )
    elif needs_product_variation_choice(product_variations, selected_pv_id, selected_pv_name):
        names = [str(v.get("name")) for v in product_variations if v.get("name")]
        out["checkout_hand_off_note"] = None
        out["product_variation_note"] = (
            f"This product has style variations. List ONLY: {', '.join(names)}. "
            "Ask which variation they want. Do NOT give product_url / checkout yet."
        )

    if discovery_engine.customer_asked_availability(user_message):
        details_colors = [
            str(c).strip()
            for c in (details.get("available_colors") or [])
            if str(c).strip()
        ]
        prim = details.get("color") or details.get("primary_color")
        if prim and str(prim).strip() and str(prim).strip() not in details_colors:
            details_colors.append(str(prim).strip())

        pname = details.get("name") or "this piece"
        if details_colors:
            out["available_colors_summary"] = details_colors
            out["catalog_search_note"] = (
                f"Customer asked what colours are available for '{pname}'. "
                f"STRICT API CATALOG GROUNDING: According to the catalog data, '{pname}' is available ONLY in: {', '.join(details_colors)}. "
                "State these exact colours faithfully and ask which shade they would love to see or try. Do NOT invent any other colours."
            )
        else:
            out["available_colors_summary"] = []
            out["catalog_search_note"] = (
                f"Customer asked what colours are available for '{pname}'. "
                f"STRICT API CATALOG GROUNDING: In our database, '{pname}' has NO other color options listed (available_colors is empty). "
                "It is crafted and available exclusively in its signature design/colour as displayed in the catalog. "
                "DO NOT invent colours (do NOT say black, navy, maroon, golden, white, sand, etc.) and DO NOT attribute colours of other pieces to this item! "
                f"Politely and honestly inform the customer that '{pname}' is crafted in its signature tone as shown. "
                "If they are looking for other specific colours, let them know that different designs in our collection feature other shades "
                "(for example, SHEHANSHAH is available in Black, Navy, Maroon, Golden; Osiria is available in White), "
                "or we can custom-tailor their desired colour and fabric through our bespoke atelier service."
            )

    logger.info(
        "get_product_details_node finish session_id=%s product_id=%s variations=%s "
        "selected_product_variation=%s",
        state.get("session_id"),
        product_id,
        len(product_variations),
        selected_pv_name,
    )
    return out


@log_node_payload("check_inventory_node")
async def check_inventory_node(state: SalesAgentState) -> dict[str, Any]:
    logger.info("check_inventory_node start %s", _log_state_summary(state))
    product_id = state.get("selected_product_id")
    details = state.get("product_details") or {}
    if not product_id and details.get("product_id"):
        product_id = details["product_id"]
    if not product_id:
        logger.info(
            "check_inventory_node finish session_id=%s no_product_selected",
            state.get("session_id"),
        )
        return {"inventory_result": {"available": False, "reason": "No product selected."}}

    color = state.get("color") or details.get("color")
    if not color:
        colors = details.get("available_colors") or []
        if colors:
            color = colors[0]
        else:
            for product in state.get("products") or []:
                if str(product.get("product_id")) == str(product_id):
                    colors = product.get("available_colors") or []
                    color = product.get("color") or (colors[0] if colors else None)
                    break
    try:
        result = await check_inventory.ainvoke({
            "product_id": str(product_id),
            "size": state.get("size"),
            "color": color,
            "quantity": state.get("quantity") or 1,
        })
    except Exception:
        logger.exception(
            "check_inventory_node failed session_id=%s product_id=%s",
            state.get("session_id"),
            product_id,
        )
        raise
    logger.info(
        "check_inventory_node finish session_id=%s product_id=%s available=%s",
        state.get("session_id"),
        product_id,
        result.get("available"),
    )
    # Honest scarcity signal only when backend reports a concrete quantity
    stock_qty = result.get("stock_quantity")
    try:
        stock_qty_num = int(stock_qty) if stock_qty is not None else None
    except (TypeError, ValueError):
        stock_qty_num = None
    if stock_qty_num is not None:
        result["low_stock"] = bool(result.get("available")) and stock_qty_num > 0 and stock_qty_num <= 3
    else:
        result["low_stock"] = False

    profile_dict = dict(state.get("customer_profile") or {})
    profile_dict["session_id"] = state.get("session_id", "default")
    profile_dict["sales_stage"] = "availability"

    return {
        "inventory_result": result,
        "sales_stage": "availability",
        "customer_profile": profile_dict,
    }


@log_node_payload("search_accessories_node")
async def search_accessories_node(state: SalesAgentState) -> dict[str, Any]:
    """Accessories-only browse — never requires a garment product_type."""
    logger.info("search_accessories_node start %s", _log_state_summary(state))
    user_message = (state.get("user_message") or "").lower()
    accessory_type = None
    for label, cues in (
        ("Stole", ("stole", "shawl", "dupatta")),
        ("Khussa", ("khussa", "jutti", "shoe")),
        ("Turban", ("turban", "pagri", "safa", "pagh")),
        ("Brooch", ("brooch",)),
        ("Tie", ("tie", "necktie")),
        ("Bow Tie", ("bow tie", "bowtie")),
    ):
        if any(c in user_message for c in cues):
            accessory_type = label
            break

    filters: dict[str, Any] = {
        "event_type": state.get("event_type"),
        "color": state.get("color") or _extract_explicit_color(state.get("user_message") or ""),
        "accessory_type": accessory_type,
        "category": state.get("product_type"),
        "query": state.get("user_message") or "",
        "limit": 12,
        "page": 1,
    }
    try:
        items = await backend_api.search_accessories(filters)
    except BackendAPIError:
        logger.exception("search_accessories_node backend error")
        return {
            "cross_sell_items": [],
            "accessories": [],
            "catalog_search_note": (
                "Accessories catalogue is momentarily unavailable. Apologise and offer a "
                "Style Consultant — do NOT say we do not carry accessories."
            ),
            "sales_stage": "recommendation",
            "intent": "accessories_search",
        }
    except Exception:
        logger.exception("search_accessories_node failed")
        raise

    if not items:
        # Broaden: drop event then type
        try:
            broad = dict(filters)
            broad.pop("event_type", None)
            items = await backend_api.search_accessories(broad)
        except Exception:
            items = []

    note = None
    if items:
        note = (
            f"Present these {len(items)} accessory option(s) (stole/khussa/turban etc.). "
            "Do NOT push a sherwani unless the customer asks. Quote only API prices; "
            "if price_unavailable, say a consultant will confirm."
        )
    else:
        note = (
            "No accessories matched these filters. Do NOT claim accessories are not part of "
            "the collection. Say stock may be limited online and offer a Style Consultant to "
            "confirm stoles, khussa, turbans, and other add-ons."
        )

    logger.info(
        "search_accessories_node finish session_id=%s count=%s type=%s",
        state.get("session_id"),
        len(items),
        accessory_type,
    )
    return {
        "cross_sell_items": items,
        "accessories": items,
        "catalog_search_note": note,
        "sales_stage": "recommendation",
        "intent": "accessories_search",
    }


@log_node_payload("suggest_cross_sell_node")
async def suggest_cross_sell_node(state: SalesAgentState) -> dict[str, Any]:
    logger.info("suggest_cross_sell_node start %s", _log_state_summary(state))
    details = state.get("product_details") or {}
    selected = None
    for product in state.get("products") or []:
        if str(product.get("product_id")) == str(state.get("selected_product_id")):
            selected = product
            break
    selected = selected or details

    color = state.get("color")
    if not color and isinstance(selected, dict):
        colors = selected.get("available_colors") or []
        color = colors[0] if colors else None

    try:
        items = await suggest_cross_sell.ainvoke({
            "product_type": state.get("product_type")
            or (selected.get("category") if isinstance(selected, dict) else None),
            "color": color,
            "occasion": state.get("event_type")
            or (selected.get("occasion") if isinstance(selected, dict) else None),
            "budget_max": state.get("budget"),
        })
    except Exception:
        logger.exception("suggest_cross_sell_node failed session_id=%s", state.get("session_id"))
        raise

    # Accessories API rows — do not run product display scoring.
    items = list(items or [])[:MAX_PRODUCTS_TO_SHOW]
    profile_dict = dict(state.get("customer_profile") or {})
    profile_dict["session_id"] = state.get("session_id", "default")
    profile_dict["sales_stage"] = "closing"

    logger.info(
        "suggest_cross_sell_node finish session_id=%s count=%s",
        state.get("session_id"),
        len(items),
    )
    return {
        "cross_sell_items": items,
        "sales_stage": "closing",
        "customer_profile": profile_dict,
    }


@log_node_payload("close_sale_node")
async def close_sale_node(state: SalesAgentState) -> dict[str, Any]:
    """
    Final close: POST sales-agent checkout and share the returned checkout_url.
    """
    logger.info("close_sale_node start %s", _log_state_summary(state))
    contact = state.get("customer_contact") or {}
    missing = _missing_handover_fields(contact)
    inventory = state.get("inventory_result") or {}
    product_id = state.get("selected_product_id")
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    products = state.get("products") or []
    if not product_id and products:
        product_id = (products[0] or {}).get("product_id")

    product_name = None
    if isinstance(details, dict):
        product_name = details.get("name")
    if not product_name:
        for p in products:
            if str(p.get("product_id")) == str(product_id) and p.get("name"):
                product_name = p.get("name")
                break
    custom = state.get("custom_design_result") if isinstance(state.get("custom_design_result"), dict) else {}
    product_name = (
        (custom.get("generated_product_name") or custom.get("base_product_name") or product_name)
        if custom
        else product_name
    )

    negotiation = state.get("negotiation_result") if isinstance(state.get("negotiation_result"), dict) else {}
    offered = negotiation.get("offered_price")

    close_result: dict[str, Any] = {
        "ready_to_close": True,
        "selected_product_id": product_id,
        "offered_price": offered,
        "inventory_available": inventory.get("available"),
        "low_stock": bool(inventory.get("low_stock")),
        "cta": "self_checkout_link",
        "missing_contact_fields": missing,
        "note": "Share the exact checkout_url from the checkout API. Do not invent a cart or catalog product_url.",
    }

    profile_dict = dict(state.get("customer_profile") or {})
    profile_dict["session_id"] = state.get("session_id", "default")
    profile_dict["sales_stage"] = "closing"
    profile_dict["buying_intent"] = "ready_to_buy"

    checkout_url = None
    checkout_session_id = None
    checkout_note = None
    # Always refresh calculator quote before building checkout items.
    quote_patch = await _refresh_price_quote(dict(state))
    state_for_checkout = {**dict(state), **quote_patch}
    if quote_patch.get("customer_profile"):
        profile_dict = {
            **profile_dict,
            **quote_patch["customer_profile"],
        }
        state_for_checkout["customer_profile"] = profile_dict
    # Ensure custom checkout has a unit_price — cart shows £0 without it.
    quote = state_for_checkout.get("price_quote") if isinstance(state_for_checkout.get("price_quote"), dict) else {}
    custom_checkout = is_custom_checkout(state_for_checkout)
    unit_price_ok = False
    try:
        unit_price_ok = bool(quote.get("ok")) and float(quote.get("unit_price") or 0) > 0
    except (TypeError, ValueError):
        unit_price_ok = False
    if unit_price_ok:
        logger.info(
            "close_sale_node calculator unit_price=%s list_price=%s mode=%s",
            quote.get("unit_price"),
            quote.get("list_price"),
            quote.get("customize_mode"),
        )
    elif custom_checkout:
        logger.warning(
            "close_sale_node CUSTOM checkout missing calculator price session_id=%s quote=%s",
            state.get("session_id"),
            {k: quote.get(k) for k in ("ok", "error", "source", "unit_price")} if quote else None,
        )

    payload = build_checkout_request(state_for_checkout)
    close_result["checkout_payload"] = payload
    if isinstance(quote, dict) and quote.get("ok"):
        close_result["list_price"] = quote.get("list_price")
        close_result["unit_price"] = quote.get("unit_price")
        close_result["currency"] = quote.get("currency") or "GBP"

    # Never create a CUSTOM cart session without a positive unit_price (frontend would show £0).
    payload_unit = None
    if payload and isinstance(payload.get("items"), list) and payload["items"]:
        try:
            payload_unit = float((payload["items"][0] or {}).get("unit_price") or 0)
        except (TypeError, ValueError):
            payload_unit = None
    if custom_checkout and (not payload or not payload_unit or payload_unit <= 0):
        logger.error(
            "close_sale_node blocking CUSTOM checkout — no unit_price session_id=%s payload_unit=%s",
            state.get("session_id"),
            payload_unit,
        )
        close_result["status"] = "checkout_link_failed"
        close_result["cta"] = None
        close_result["note"] = (
            "Bespoke price could not be calculated yet. Apologise briefly, ask them to confirm "
            "the fabric and design again, then retry checkout — or connect a Style Consultant. "
            "Do NOT invent a cart URL, catalogue product_url, or a price."
        )
        payload = None

    if payload:
        try:
            api_result = await backend_api.create_checkout_session(payload)
        except BackendAPIError:
            logger.exception(
                "close_sale_node checkout API failed session_id=%s",
                state.get("session_id"),
            )
            api_result = {"success": False}
        except Exception:
            logger.exception(
                "close_sale_node checkout unexpected error session_id=%s",
                state.get("session_id"),
            )
            api_result = {"success": False}
        checkout_url = api_result.get("checkout_url") if isinstance(api_result, dict) else None
        checkout_session_id = api_result.get("session_id") if isinstance(api_result, dict) else None
        if checkout_url:
            close_result["status"] = "ready_for_checkout_link"
            close_result["checkout_url"] = checkout_url
            close_result["checkout_session_id"] = checkout_session_id
            checkout_note = _build_checkout_hand_off_note(
                checkout_url=str(checkout_url),
                product_name=str(product_name) if product_name else None,
                variation_name=state.get("selected_product_variation_name"),
                color=state.get("color"),
                negotiated=bool(offered),
            )
        else:
            close_result["status"] = "checkout_link_failed"
            close_result["cta"] = None
            close_result["note"] = (
                "Checkout link could not be generated. Apologise briefly and offer to try again "
                "or connect a Style Consultant. Do NOT invent a cart URL or catalog product_url."
            )
    elif close_result.get("status") == "checkout_link_failed":
        # Already failed (e.g. CUSTOM without calculator unit_price) — keep that status.
        pass
    elif missing:
        close_result["status"] = "needs_contact"
        close_result["cta"] = None
    else:
        close_result["status"] = "checkout_link_failed"
        close_result["cta"] = None
        close_result["note"] = (
            "No checkout items were ready. Do NOT invent a cart or product_url. "
            "Offer to confirm the piece again or connect a Style Consultant."
        )

    logger.info(
        "close_sale_node finish session_id=%s status=%s has_url=%s",
        state.get("session_id"),
        close_result.get("status"),
        bool(checkout_url),
    )
    return {
        "close_result": close_result,
        "sales_stage": "closing",
        "buying_intent": "ready_to_buy",
        "customer_profile": profile_dict,
        "checkout_hand_off_note": checkout_note,
        "checkout_url": checkout_url,
        "checkout_session_id": checkout_session_id,
        "handover_pending": bool(missing) and not checkout_url,
        "price_quote": quote_patch.get("price_quote"),
        "customize_pricing_mode": quote_patch.get("customize_pricing_mode"),
    }


@log_node_payload("calculate_negotiation_offer_node")
async def calculate_negotiation_offer_node(state: SalesAgentState) -> dict[str, Any]:
    logger.info("calculate_negotiation_offer_node start %s", _log_state_summary(state))
    details = state.get("product_details") or {}
    if not details and state.get("products"):
        details = state["products"][0]

    quote_patch = await _refresh_price_quote(dict(state))
    price_quote = quote_patch.get("price_quote") if isinstance(quote_patch.get("price_quote"), dict) else {}
    if not details:
        details = {}

    # Prefer calculator list/floor for customize / price-missing; else catalogue.
    price = None
    floor_override = None
    currency = "GBP"
    if isinstance(details, dict) and details.get("price") is not None and price_quote.get("source") != "calculator":
        price = details.get("price")
        floor_override = details.get("floor_price")
        currency = details.get("currency") or "PKR"
    elif price_quote.get("ok"):
        price = price_quote.get("list_price") or price_quote.get("unit_price")
        floor_override = price_quote.get("floor_price")
        currency = price_quote.get("currency") or "GBP"
        # Synthetic details so rest of node can run for Path A customize
        if not details.get("product_id"):
            details = {
                **details,
                "name": details.get("name")
                or ((state.get("custom_design_result") or {}).get("generated_product_name") if isinstance(state.get("custom_design_result"), dict) else None)
                or "Bespoke commission",
                "category": state.get("product_type") or details.get("category"),
                "currency": currency,
                "price": price,
                "floor_price": floor_override,
            }

    if price is None:
        logger.info(
            "calculate_negotiation_offer_node finish session_id=%s no_price",
            state.get("session_id"),
        )
        return {
            "negotiation_result": {
                "approved": False,
                "message": "No product selected for negotiation."
                if not details
                else "Product price unavailable for negotiation.",
            },
            **{k: v for k, v in quote_patch.items() if k != "customer_profile"},
        }

    current_neg_dict = dict(state.get("negotiation_state") or {})
    current_neg_state = NegotiationStateSchema(**current_neg_dict)

    # Extract customer's offered price from message or budget
    customer_offer = state.get("budget")
    user_msg = (state.get("user_message") or "").lower()
    list_price_f = float(price)

    import re
    price_match = re.search(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\s*k\b)?", user_msg.replace(",", ""))
    if price_match:
        extracted = price_match.group(1).replace(",", "")
        if extracted.isdigit():
            extracted_price = int(extracted)
            rest = user_msg[price_match.end() : price_match.end() + 2].lower()
            has_k = "k" in rest
            # PKR shorthand (70k / 70 meaning 70,000) only when list price is in tens of thousands
            if has_k or (extracted_price < 1000 and list_price_f >= 10000):
                extracted_price *= 1000
            if 50 <= extracted_price <= 10_000_000:
                # Ignore tiny numbers that are not a real counter-offer (e.g. "size 42")
                if extracted_price >= list_price_f * 0.2 or extracted_price >= 10000:
                    customer_offer = float(extracted_price)
                    logger.info(
                        "Extracted customer offer from message: %s -> %s",
                        user_msg[:50],
                        customer_offer,
                    )

    product_category = (
        state.get("product_type")
        or details.get("category")
        or details.get("product_type")
    )
    product_category = resolve_category_against_catalog(
        product_category,
        state.get("catalog_categories") or [],
    ) or product_category
    event_type = state.get("event_type")

    product_available_colors = [
        str(c).strip()
        for c in (details.get("available_colors") or [])
        if str(c).strip()
    ]
    session_color = state.get("color") or state.get("selected_color") or _extract_explicit_color(
        state.get("user_message") or ""
    )
    product_color, product_available_colors, color_pending = resolve_garment_color_for_accessory(
        session_color,
        product_available_colors,
    )
    next_round = int((state.get("negotiation_state") or {}).get("round_number") or 0) + 1
    # Round 1 is value defence — colour ask only starts when we would gift (round 2+).
    ask_color_before_gift = bool(color_pending and next_round >= 2)

    accessory_rows: list[dict[str, Any]] = []
    bundle_offer: dict[str, Any] | None = None
    # Prefetch matching accessories for Round 2 (and later rounds reuse in result).
    if product_category and next_round >= 2 and not ask_color_before_gift:
        try:
            recommended = await backend_api.recommend_accessories(
                category=str(product_category),
                product_id=str(details.get("product_id") or "") or None,
                event_type=str(event_type) if event_type else None,
                main_product_price=float(price),
                limit=6,
            )
            raw_acc = list(recommended.get("accessories") or [])
            bundle_offer = recommended.get("bundle_offer")
            product_currency = details.get("currency") or currency
            floor_for_margin = floor_override if floor_override is not None else details.get("floor_price")
            is_free_qual = qualifies_for_free_accessory(
                float(price),
                currency=product_currency,
                bundle_offer=bundle_offer,
            )
            if is_free_qual:
                accessory_rows = filter_free_gift_candidates(
                    raw_acc,
                    list_price=float(price),
                    floor_price=floor_for_margin,
                    product_category=product_category,
                    event_type=str(event_type) if event_type else None,
                    bundle_offer=bundle_offer,
                    currency=product_currency,
                    product_color=product_color,
                    limit=1,
                )
            else:
                accessory_rows = filter_accessories_for_product(
                    raw_acc,
                    product_category=product_category,
                    event_type=str(event_type) if event_type else None,
                    product_color=product_color,
                    limit=1,
                )
            # If recommend returned poorly matched rows, fall back to search + filter.
            if not accessory_rows:
                searched = await backend_api.search_accessories(
                    {
                        "category": product_category,
                        "event_type": event_type,
                        "limit": 8,
                    }
                )
                if is_free_qual:
                    accessory_rows = filter_free_gift_candidates(
                        searched,
                        list_price=float(price),
                        floor_price=floor_for_margin,
                        product_category=product_category,
                        event_type=str(event_type) if event_type else None,
                        bundle_offer=bundle_offer,
                        currency=product_currency,
                        product_color=product_color,
                        limit=1,
                    )
                else:
                    accessory_rows = filter_accessories_for_product(
                        searched,
                        product_category=product_category,
                        event_type=str(event_type) if event_type else None,
                        product_color=product_color,
                        limit=1,
                    )
            # Empty-only broaden: retry accessories without event_type (keep margin filter).
            if not accessory_rows:
                searched_broad = await backend_api.search_accessories(
                    {
                        "category": product_category,
                        "limit": 8,
                    }
                )
                if is_free_qual:
                    accessory_rows = filter_free_gift_candidates(
                        searched_broad,
                        list_price=float(price),
                        floor_price=floor_for_margin,
                        product_category=product_category,
                        event_type=None,
                        bundle_offer=bundle_offer,
                        currency=product_currency,
                        product_color=product_color,
                        limit=1,
                    )
                else:
                    accessory_rows = filter_accessories_for_product(
                        searched_broad,
                        product_category=product_category,
                        event_type=None,
                        product_color=product_color,
                        limit=1,
                    )
                if accessory_rows:
                    logger.info(
                        "negotiation accessories broadened without event session_id=%s count=%s",
                        state.get("session_id"),
                        len(accessory_rows),
                    )
            # Second empty-only broaden: drop category too (still margin-filtered).
            if not accessory_rows:
                searched_any = await backend_api.search_accessories({"limit": 8})
                if is_free_qual:
                    accessory_rows = filter_free_gift_candidates(
                        searched_any,
                        list_price=float(price),
                        floor_price=floor_for_margin,
                        product_category=product_category,
                        event_type=None,
                        bundle_offer=bundle_offer,
                        currency=product_currency,
                        product_color=product_color,
                        limit=1,
                    )
                else:
                    accessory_rows = filter_accessories_for_product(
                        searched_any,
                        product_category=product_category,
                        event_type=None,
                        product_color=product_color,
                        limit=1,
                    )
                if accessory_rows:
                    logger.info(
                        "negotiation accessories broadened without category session_id=%s count=%s",
                        state.get("session_id"),
                        len(accessory_rows),
                    )
        except Exception:
            logger.exception(
                "negotiation accessories fetch failed session_id=%s category=%s",
                state.get("session_id"),
                product_category,
            )
            accessory_rows = []
            bundle_offer = None

    margin_budget = free_gift_margin_budget(
        float(price),
        floor_override if floor_override is not None else details.get("floor_price"),
        bundle_offer,
    )
    accessory_rows = summarize_accessories_for_prompt(accessory_rows)

    updated_neg_state, strategy = negotiation_engine.process_negotiation_round(
        current_state=current_neg_state,
        list_price=float(price),
        floor_price_override=floor_override if floor_override is not None else details.get("floor_price"),
        customer_offered_price=customer_offer,
        product_type=product_category,
        product_name=details.get("name"),
        backend_promo_code=None,  # only set when discount/validate API exists
        accessories=accessory_rows,
        bundle_offer=bundle_offer,
        currency=details.get("currency") or currency,
        margin_budget=margin_budget,
        product_color=product_color,
        product_colors=product_available_colors,
        color_pending=ask_color_before_gift,
    )

    alternative_products: list[dict[str, Any]] = []
    # Get the customer's offered price or stated budget for finding alternatives
    customer_budget = strategy.get("customer_offer") or state.get("budget")
    
    if strategy.get("pivot_needed") and customer_budget is not None:
        try:
            # Search for products WITHIN the customer's budget
            alternative_products = await search_products.ainvoke({
                "query": state.get("user_message") or "",
                "product_type": state.get("product_type") or details.get("category"),
                "color": state.get("color"),
                "budget_max": float(customer_budget),  # Use customer's offer as max budget
                "occasion": state.get("event_type"),
                "event_type": state.get("event_type"),
                "size": state.get("size"),
                "season": (state.get("customer_profile") or {}).get("derived_season"),
                "in_stock": True,
                "limit": MAX_PRODUCTS_TO_SHOW or 50,
                "offset": 0,
            })
            # Exclude the current product from pivot options
            current_id = str(details.get("product_id") or "")
            alternative_products = [
                p for p in alternative_products
                if str(p.get("product_id")) != current_id
            ]
            if MAX_PRODUCTS_TO_SHOW:
                alternative_products = alternative_products[:MAX_PRODUCTS_TO_SHOW]
        except Exception:
            logger.exception(
                "negotiation pivot search failed session_id=%s",
                state.get("session_id"),
            )
            alternative_products = []

    # For Round 1 defense, approved=false prevents frontend from showing "EXCLUSIVE ATELIER OFFER" card
    action = strategy.get("action")
    is_round_1_defense = (action == "defend_value" and updated_neg_state.round_number == 1)
    
    # Format accessories as strings for frontend display (e.g., "Black shawl")
    # Frontend expects array of strings, not objects
    raw_accessories = strategy.get("accessories") or accessory_rows or []
    accessories_for_display = []
    for acc in raw_accessories:
        if isinstance(acc, str):
            accessories_for_display.append(acc)
        elif isinstance(acc, dict):
            name = acc.get("name") or acc.get("accessory_type") or "Accessory"
            acc_colors = [str(c) for c in (acc.get("available_colors") or []) if str(c).strip()]
            if product_color and acc_colors:
                accessories_for_display.append(f"{name} ({product_color})")
            else:
                accessories_for_display.append(str(name))
    
    result = {
        "approved": action not in (
            "cutoff_and_pivot",
            "budget_pivot",
            "defend_value",
            "ask_color_preference",
        ),
        "cash_discount_offered": False,
        "round_number": updated_neg_state.round_number,
        "strategy": strategy,
        "action": action,
        "offered_price": strategy.get("offered_price"),
        "original_price": float(price) if price else None,
        "currency": details.get("currency") or currency,
        "discount_percent": strategy.get("discount_percent", 0.0),
        "bundles": updated_neg_state.offered_bundles,
        "accessories": accessories_for_display,
        "accessories_full": raw_accessories,  # Keep full objects for agent prompt
        "bundle_offer": strategy.get("bundle_offer") or bundle_offer,
        "free_accessory": bool(strategy.get("free_accessory")),
        "margin_budget": strategy.get("margin_budget"),
        "is_final_offer": strategy.get("is_final_offer", False),
        "prompt_directive": strategy.get("prompt_directive"),
        "promo_code": strategy.get("promo_code"),
        "requires_backend_discount": strategy.get("requires_backend_discount", False),
        "alternative_products": alternative_products,
        "status_note": (
            "Round 1: hold list price and explain value. Not a rejection."
            if is_round_1_defense
            else strategy.get("prompt_directive")
        ),
    }

    logger.info(
        "calculate_negotiation_offer_node finish session_id=%s round=%s action=%s offered_price=%s pivot_count=%s",
        state.get("session_id"),
        updated_neg_state.round_number,
        action,
        strategy.get("offered_price"),
        len(alternative_products),
    )
    out: dict[str, Any] = {
        "negotiation_result": result,
        "negotiation_state": updated_neg_state.model_dump(),
        "sales_stage": "negotiation",
        "products": alternative_products or [],
    }
    if product_color:
        out["color"] = product_color
    if quote_patch.get("price_quote") is not None:
        out["price_quote"] = quote_patch["price_quote"]
    if quote_patch.get("customize_pricing_mode") is not None:
        out["customize_pricing_mode"] = quote_patch["customize_pricing_mode"]
    if quote_patch.get("customer_profile"):
        out["customer_profile"] = {
            **dict(state.get("customer_profile") or {}),
            **quote_patch["customer_profile"],
        }
    return out



@log_node_payload("validate_measurements_node")
async def validate_measurements_node(state: SalesAgentState) -> dict[str, Any]:
    import asyncio

    logger.info("validate_measurements_node start %s", _log_state_summary(state))
    chart = state.get("size_chart")
    if not chart:
        try:
            charts = await backend_api.list_size_charts()
            chart = chart_for_category(charts, state.get("product_type"))
        except Exception:
            logger.exception("validate_measurements_node list_size_charts failed")
            chart = None
    try:
        result = await asyncio.to_thread(
            validate_measurements.invoke,
            {
                "selected_size": state.get("size"),
                "height": state.get("height"),
                "chest": state.get("chest"),
                "waist": state.get("waist"),
                "product_type": state.get("product_type"),
                "size_chart": chart,
                "body_measurements": state.get("body_measurements"),
                "available_sizes": product_available_sizes(state),
                "shoulder": state.get("shoulder"),
                "sleeve": state.get("sleeve"),
                "jacket_length": state.get("jacket_length"),
            },
        )
    except Exception:
        logger.exception(
            "validate_measurements_node failed session_id=%s",
            state.get("session_id"),
        )
        raise
    logger.info(
        "validate_measurements_node finish session_id=%s status=%s tailor_review=%s",
        state.get("session_id"),
        result.get("status"),
        result.get("requires_tailor_review"),
    )
    return {"measurement_result": result, "size_chart": chart}


@log_node_payload("collect_measurements_node")
async def collect_measurements_node(state: SalesAgentState) -> dict[str, Any]:
    """
    Two chart-driven paths after product select or custom image:
    1) standard_size — pick a chart row / product available_sizes
    2) body_measurements — collect only live chart columns (inches)
    No chart (Sherwani etc.) → available_sizes + consultant; never fake Suits numbers.
    """
    logger.info("collect_measurements_node start %s", _log_state_summary(state))
    try:
        charts = await backend_api.list_size_charts()
    except Exception:
        logger.exception("collect_measurements_node list_size_charts failed")
        charts = []

    product_type = state.get("product_type")
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    category_id = None
    category = details.get("category")
    if isinstance(category, dict):
        category_id = category.get("id")
        product_type = product_type or category.get("name")
    elif category:
        product_type = product_type or str(category)

    chart = chart_for_category(
        charts,
        str(product_type) if product_type else None,
        category_id=str(category_id) if category_id else None,
    )
    available = product_available_sizes(state)
    collected = collect_from_message(
        user_message=state.get("user_message") or "",
        chart=chart,
        state=dict(state),
        available_sizes=available,
    )
    prompt = None
    if collected.get("status") not in ("collected", "ok"):
        prompt = build_collection_prompt(
            chart=chart,
            available_sizes=available,
            missing=collected.get("missing_fields"),
            path=collected.get("measurement_path"),
            roman_urdu=False,
        )
        collected["prompt"] = prompt
        collected["how_to_measure"] = (chart or {}).get("how_to_measure") or []
        collected["available_sizes"] = available
        collected["chart_sizes"] = [
            r.get("Size") for r in (chart or {}).get("rows") or [] if isinstance(r, dict) and r.get("Size")
        ]
    else:
        payload = {
            "session_id": state.get("session_id"),
            "path": collected.get("measurement_path"),
            "size": collected.get("size"),
            "size_chart_id": (chart or {}).get("id"),
            "chest": collected.get("chest"),
            "waist": collected.get("waist"),
            "shoulder": collected.get("shoulder"),
            "sleeve": collected.get("sleeve"),
            "jacket_length": collected.get("jacket_length"),
            "body_measurements": collected.get("body_measurements"),
            "requires_tailor_review": collected.get("requires_tailor_review"),
            "product_type": product_type,
            "selected_product_id": state.get("selected_product_id"),
        }
        try:
            save_measurements_json(str(state.get("session_id") or "unknown"), payload)
            collected["saved"] = True
        except Exception:
            logger.exception("collect_measurements_node save failed")
            collected["saved"] = False

    logger.info(
        "collect_measurements_node finish session_id=%s status=%s path=%s has_chart=%s",
        state.get("session_id"),
        collected.get("status"),
        collected.get("measurement_path"),
        bool(chart),
    )
    return {
        "measurement_result": collected,
        "measurement_path": collected.get("measurement_path") or state.get("measurement_path"),
        "size": collected.get("size") or state.get("size"),
        "chest": collected.get("chest") or state.get("chest"),
        "waist": collected.get("waist") or state.get("waist"),
        "shoulder": collected.get("shoulder") or state.get("shoulder"),
        "sleeve": collected.get("sleeve") or state.get("sleeve"),
        "jacket_length": collected.get("jacket_length") or state.get("jacket_length"),
        "body_measurements": collected.get("body_measurements") or state.get("body_measurements"),
        "size_chart": chart,
        "measurement_prompt": prompt,
        "customization_stage": (
            "measurements" if _has_custom_image(state) else state.get("customization_stage")
        ),
        "catalog_search_note": prompt or state.get("catalog_search_note"),
    }


@log_node_payload("create_human_handover_node")
async def create_human_handover_node(state: SalesAgentState) -> dict[str, Any]:
    logger.info("create_human_handover_node start %s", _log_state_summary(state))
    contact = state.get("customer_contact") or {}
    missing_fields = _missing_handover_fields(contact)

    if missing_fields:
        logger.info(
            "create_human_handover_node pending_contact session_id=%s missing_fields=%s phone_masked=%s",
            state.get("session_id"),
            missing_fields,
            _mask_phone(contact.get("phone")),
        )
        return {
            "handover_result": {
                "handover_created": False,
                "status": "pending_contact",
                "missing_fields": missing_fields,
            },
            "customer_contact": contact,
            "handover_pending": True,
        }

    preferences = {
        "event_type": state.get("event_type"),
        "product_type": state.get("product_type"),
        "color": state.get("color"),
        "size": state.get("size"),
        "quantity": state.get("quantity"),
        "budget": state.get("budget"),
        "wedding_date": state.get("wedding_date"),
        "height": state.get("height"),
        "products": state.get("products", []),
        "measurement_result": state.get("measurement_result"),
        "measurements": handover_measurements_payload(state),
        "custom_image_url": state.get("custom_image_url"),
        "custom_instructions": state.get("custom_instructions"),
        "cut_style": state.get("cut_style"),
    }
    conversation_summary = _build_conversation_summary(state, state.get("messages", []))
    reason = state.get("handover_reason") or "Customer requested consultant."

    try:
        result = await create_human_handover.ainvoke({
            "session_id": state["session_id"],
            "reason": reason,
            "customer_preferences": preferences,
            "customer_contact": contact,
            "conversation_summary": conversation_summary,
            "handover_reason": state.get("handover_reason"),
        })
    except Exception:
        logger.exception(
            "create_human_handover_node failed session_id=%s",
            state.get("session_id"),
        )
        raise

    logger.info(
        "create_human_handover_node finish session_id=%s ticket_id=%s summary_length=%s phone_masked=%s",
        state.get("session_id"),
        result.get("ticket_id"),
        safe_len(conversation_summary),
        _mask_phone(contact.get("phone")),
    )
    return {
        "handover_result": result,
        "customer_contact": contact,
        "handover_pending": False,
    }


@log_node_payload("generate_custom_design_node")
async def generate_custom_design_node(state: SalesAgentState) -> dict[str, Any]:
    logger.info("generate_custom_design_node start %s", _log_state_summary(state))
    
    user_instructions = state.get("user_message", "")
    prior_instructions = (state.get("custom_instructions") or "").strip()
    wants_revision = _is_design_revision_request(user_instructions)
    base_product = None
    fabric_details = None
    
    # Fabric-first Path A: do NOT auto-attach a random catalogue product from the message
    # (e.g. "Tuxedo" matching "Solid Matte Charcoal") — that steals the fabric reference.
    selected_product_id = state.get("selected_product_id")
    fabric_first = bool(state.get("selected_fabric_catalog_code")) and not selected_product_id

    if selected_product_id and not fabric_first:
        product_details = state.get("product_details") or {}
        if not product_details or str(product_details.get("product_id")) != str(selected_product_id):
            try:
                product_details = await backend_api.get_product_details(selected_product_id)
            except Exception as e:
                logger.error("Failed to get product details: %s", e)
        base_product = product_details if isinstance(product_details, dict) else None
    elif not fabric_first and not selected_product_id:
        matched = _match_product_from_message(user_instructions, state.get("products") or [])
        if matched:
            selected_product_id = matched.get("product_id") or matched.get("id")
            base_product = matched

    # Resolve chosen fabric FIRST — never let a product image displace the swatch reference.
    selected_fabric_code = state.get("selected_fabric_catalog_code")
    if not selected_fabric_code:
        matched_fabric = _match_fabric_from_message(
            user_instructions, state.get("fabrics") or []
        )
        if matched_fabric:
            selected_fabric_code = matched_fabric.get("catalog_code")

    session_fabrics = [
        f for f in (state.get("fabrics") or []) if isinstance(f, dict)
    ]

    def _fabric_from_session(code: str | None) -> dict[str, Any] | None:
        if not code:
            return None
        for f in session_fabrics:
            if str(f.get("catalog_code") or "") == str(code):
                return dict(f)
        return None

    if selected_fabric_code:
        try:
            fabric_details = await backend_api.get_fabric_details(str(selected_fabric_code))
        except Exception as e:
            logger.error(
                "Failed to get fabric details by selected_fabric_catalog_code=%s: %s",
                selected_fabric_code,
                e,
            )
            fabric_details = None
        session_hit = _fabric_from_session(selected_fabric_code)
        if session_hit:
            if not fabric_details:
                fabric_details = session_hit
            else:
                # Keep API fields but never drop a working swatch image from session.
                if not fabric_details.get("image_url") and session_hit.get("image_url"):
                    fabric_details["image_url"] = session_hit["image_url"]
                if not fabric_details.get("name") and session_hit.get("name"):
                    fabric_details["name"] = session_hit["name"]
        logger.info(
            "generate_custom_design_node selected fabric code=%s has_image=%s name=%s revision=%s",
            selected_fabric_code,
            bool((fabric_details or {}).get("image_url")),
            (fabric_details or {}).get("name"),
            wants_revision,
        )

    # Defensive guardrail: Never generate image if user has not provided concrete customization specifications
    if not wants_revision and not _has_customization_details(
        user_instructions,
        selected_product_id,
        selected_fabric_code or state.get("selected_fabric_catalog_code"),
        base_product=base_product,
    ) and not (
        selected_fabric_code and (state.get("cut_style") or state.get("product_type"))
    ):
        logger.info(
            "generate_custom_design_node: skipping image generation because user has not provided concrete customization specifications."
        )
        return {
            "custom_image_url": None,
            "custom_design_result": None,
            "sales_stage": "customization",
            "custom_instructions": user_instructions,
            "selected_fabric_catalog_code": selected_fabric_code,
        }

    # Check if a specific variation was selected or mentioned
    selected_pv_name = state.get("selected_product_variation_name")
    selected_pv_id = state.get("selected_product_variation_id")
    variation_piece = None
    if base_product and isinstance(base_product, dict):
        for v in base_product.get("variations") or []:
            if selected_pv_id and str(v.get("id")) == str(selected_pv_id):
                variation_piece = v
                break
            if selected_pv_name and str(v.get("name", "")).lower() == str(selected_pv_name).lower():
                variation_piece = v
                break
        if not variation_piece and base_product.get("variations"):
            msg_lower = user_instructions.lower()
            for v in base_product.get("variations") or []:
                v_name = str(v.get("name") or "").lower()
                if v_name and v_name in msg_lower:
                    variation_piece = v
                    break

    # Path B: product fabric only when customer has NOT already chosen a fabric swatch.
    if not fabric_details and base_product and isinstance(base_product, dict):
        fabric_id = (
            (variation_piece.get("fabric_id") if variation_piece else None)
            or base_product.get("fabric_id")
            or base_product.get("fabric_catalog_code")
        )
        if fabric_id:
            try:
                fabric_details = await backend_api.get_fabric_details(fabric_id)
                logger.info(
                    "generate_custom_design_node Path B fabric_id=%s found=%s",
                    fabric_id,
                    bool(fabric_details),
                )
            except Exception as e:
                logger.error("Failed to get fabric details: %s", e)

    # Product image is silhouette reference only — never overwrite chosen fabric swatch.
    prod_img = None
    if variation_piece and (variation_piece.get("image_url") or variation_piece.get("images")):
        prod_img = variation_piece.get("image_url") or (
            variation_piece.get("images")[0] if variation_piece.get("images") else None
        )
    if not prod_img and base_product and isinstance(base_product, dict):
        prod_img = base_product.get("image_url")
        if not prod_img and isinstance(base_product.get("images"), list) and base_product.get("images"):
            prod_img = base_product["images"][0]

    if not fabric_details:
        url_match = re.search(r'https?://[^\s<>"]+\b', user_instructions)
        if url_match:
            extracted_url = url_match.group(0)
            logger.info("Extracted custom fabric image URL from message: %s", extracted_url)
            fabric_details = {
                "name": "Custom Uploaded Fabric",
                "image_url": extracted_url,
            }

    ceremony = state.get("event_type")
    colors = state.get("color")
    cut_style = state.get("cut_style")
    dress_category = (
        state.get("product_type")
        or (base_product.get("category") if base_product else None)
        or cut_style
    )
    # Keep Prince Coat / Bandhgala cut from being lost when product_type is still Sherwani/etc.
    from app.services.garment_silhouette import infer_garment_cut

    cut_key = infer_garment_cut(
        dress_category,
        " ".join(
            p
            for p in (
                state.get("custom_instructions"),
                state.get("user_message"),
                cut_style,
            )
            if p
        ),
    )
    if cut_key == "prince_coat":
        dress_category = "Prince Coat"
    elif cut_key == "sherwani" and not dress_category:
        dress_category = "Sherwani"
    session_id = state.get("session_id")
    variation_name = (variation_piece.get("name") if variation_piece else None) or selected_pv_name

    # Merge prior brief + new revision so fal gets full intent.
    instruction_parts = [p for p in (prior_instructions, user_instructions) if p]
    if cut_style and cut_style.lower() not in " ".join(instruction_parts).lower():
        instruction_parts.append(f"Design preference / cut style: {cut_style}.")
    if wants_revision:
        instruction_parts.append(
            "IMPORTANT REVISION: Apply the latest customer change to a NEW image. "
            "Do not repeat the previous mockup."
        )
    merged_instructions = " ".join(instruction_parts).strip() or user_instructions

    # Attach product image separately for silhouette when fabric swatch is already set.
    base_for_gen = base_product
    if isinstance(base_for_gen, dict) and prod_img and not base_for_gen.get("image_url"):
        base_for_gen = {**base_for_gen, "image_url": prod_img}
    
    try:
        image_url, result_data = await generate_bespoke_design(
            base_product=base_for_gen,
            fabric_details=fabric_details,
            user_instructions=merged_instructions,
            ceremony=ceremony,
            colors=colors,
            dress_category=dress_category,
            session_id=session_id,
            variation_name=variation_name,
        )
        if isinstance(result_data, dict) and result_data.get("error") == "quota_exceeded":
            from app.services.quota_service import MSG_IMAGE_EXCEEDED

            soft = (result_data.get("message") or MSG_IMAGE_EXCEEDED).strip()
            return {
                "custom_image_url": None,
                "custom_design_result": result_data,
                "final_response": soft,
                # Empty steps so final_response_node short-circuits (no LLM rewrite / price reveal).
                "required_steps": [],
                "sales_stage": "customization",
                "custom_instructions": merged_instructions,
                "selected_fabric_catalog_code": selected_fabric_code,
                "product_details": None,
                "products": [],
            }
        out: dict[str, Any] = {
            "custom_image_url": image_url,
            "custom_design_result": result_data,
            "sales_stage": "customization",
            "customization_stage": "measurements",
            "custom_instructions": merged_instructions,
            "selected_fabric_catalog_code": selected_fabric_code,
            "product_details": None,
            "products": [],
        }
        # Keep product_id for Path B pricing mode detection while clearing catalogue UI.
        quote_state = {
            **dict(state),
            **out,
            "selected_product_id": selected_product_id or state.get("selected_product_id"),
            "product_type": dress_category or state.get("product_type"),
            "fabrics": state.get("fabrics") or ([fabric_details] if fabric_details else []),
        }
        if isinstance(base_product, dict):
            # Path B needs product fabric_id even after product_details cleared for UI
            quote_state["product_details"] = {
                "product_id": base_product.get("product_id") or selected_product_id,
                "category": base_product.get("category"),
                "fabric_id": base_product.get("fabric_id") or base_product.get("fabric_catalog_code"),
                "fabric_catalog_code": base_product.get("fabric_catalog_code") or base_product.get("fabric_id"),
                "name": base_product.get("name"),
            }
        if fabric_details and isinstance(fabric_details, dict):
            quote_state["selected_fabric_catalog_code"] = (
                selected_fabric_code
                or fabric_details.get("catalog_code")
                or fabric_details.get("fabric_id")
            )
            # Ensure grade available even if fabrics list empty
            quote_state["fabrics"] = [fabric_details] + [
                f for f in (state.get("fabrics") or []) if isinstance(f, dict)
            ]
        quote_patch = await _refresh_price_quote(quote_state)
        out.update(quote_patch)
        return out
    except Exception as e:
        logger.exception("generate_custom_design_node failed")
        return {
            "custom_image_url": None,
            "custom_design_result": {"error": str(e)},
            "sales_stage": "customization",
            "custom_instructions": merged_instructions,
            "selected_fabric_catalog_code": selected_fabric_code,
            "product_details": None,
            "products": [],
        }


@log_node_payload("final_response_node")
async def final_response_node(state: SalesAgentState) -> dict[str, Any]:
    logger.info("final_response_node start %s", _log_state_summary(state))
    # Jailbreak / prompt-armor / quota soft-block — do not overwrite fixed reply.
    preexisting = (state.get("final_response") or "").strip()
    design_err = state.get("custom_design_result") if isinstance(state.get("custom_design_result"), dict) else {}
    quota_blocked = design_err.get("error") == "quota_exceeded"
    if preexisting and (quota_blocked or not (state.get("required_steps") or [])):
        logger.info(
            "final_response_node short-circuit preexisting reply session_id=%s quota_blocked=%s",
            state.get("session_id"),
            quota_blocked,
        )
        return {
            "final_response": preexisting,
            "messages": [{"role": "assistant", "content": preexisting}],
        }

    # Keep calculator quote fresh for customize / price asks / checkout.
    quote_patch: dict[str, Any] = {}
    needs_quote = bool(
        state.get("custom_image_url")
        or state.get("custom_design_result")
        or state.get("selected_fabric_catalog_code")
        or state.get("customization_stage")
    )
    existing_quote = state.get("price_quote") if isinstance(state.get("price_quote"), dict) else {}
    if needs_quote and not existing_quote.get("ok"):
        quote_patch = await _refresh_price_quote(dict(state))
        if quote_patch.get("price_quote"):
            state = {**dict(state), **quote_patch}

    handover_result = state.get("handover_result") or {}
    contact = state.get("customer_contact") or {}

    if handover_result.get("handover_created"):
        # Do not return a regex language template — let the final LLM write
        # confirmation in the customer's language using these facts.
        inv = state.get("inventory_result")
        lead = None
        details = state.get("product_details") or {}
        if isinstance(details, dict):
            lead = details.get("lead_time_days")
        stock_note = None
        if isinstance(inv, dict) and inv.get("available") is not None:
            avail = "available" if inv.get("available") else "not currently available"
            stock_note = f"This piece is {avail} in the requested size/colour."
            if lead is not None:
                stock_note += f" Typical lead time is about {lead} days."
        handover_facts = _handover_facts_for_llm(contact)
        if stock_note:
            handover_facts["stock_note"] = stock_note
        # Fall through to LLM path with handover facts in context.
        state = {**state, "handover_llm_facts": handover_facts}

    messages = state.get("messages") or []
    recent_messages = messages[-8:]
    history_lines = [
        f"{message.get('role', 'unknown')}: {truncate_text(message.get('content', ''), max_len=300)}"
        for message in recent_messages
    ]
    image_url = _resolve_product_image_url(state)
    recommendations = state.get("recommendations") or state.get("products") or []
    recommendations = apply_display_currency_list(
        [p for p in recommendations if isinstance(p, dict)],
        None,
    )
    catalog_search_note = state.get("catalog_search_note") or ""
    if not recommendations and state.get("intent") in ("product_search", "accessories_search", "mixed"):
        empty_note = (
            "No catalogue rows matched these filters. Say so honestly — offer to broaden "
            "colour/budget/event or connect a Style Consultant. Never invent prices or claim "
            "pricing is 'not loaded'."
        )
        if empty_note not in catalog_search_note:
            catalog_search_note = f"{catalog_search_note} {empty_note}".strip() if catalog_search_note else empty_note
    if state.get("measurement_prompt"):
        catalog_search_note = (
            f"{catalog_search_note} {state.get('measurement_prompt')}".strip()
            if catalog_search_note
            else state.get("measurement_prompt")
        )
    context = {
        "intent": state.get("intent"),
        "sales_stage": state.get("sales_stage"),
        "buying_intent": state.get("buying_intent"),
        "objection_type": state.get("objection_type"),
        "recommendations": [
            {
                "product_id": p.get("product_id"),
                "name": p.get("name"),
                "description": p.get("description"),
                "price": (
                    None
                    if p.get("price_unavailable")
                    else (p.get("display_price") if p.get("display_price") is not None else p.get("price"))
                ),
                "currency": None if p.get("price_unavailable") else (p.get("display_currency") or p.get("currency")),
                "catalogue_price": None if p.get("price_unavailable") else p.get("price"),
                "catalogue_currency": p.get("currency"),
                "display_price": None if p.get("price_unavailable") else p.get("display_price"),
                "display_currency": None if p.get("price_unavailable") else (p.get("display_currency") or p.get("currency")),
                "currency_needs_consultant": p.get("currency_needs_consultant", False),
                "price_unavailable": p.get("price_unavailable", False),
                "fabric": p.get("fabric"),
                "pattern": p.get("pattern"),
                "season": p.get("season"),
                "occasion": p.get("occasion"),
                "category": p.get("category"),
                "available_sizes": p.get("available_sizes"),
                "available_colors": p.get("available_colors"),
                "product_url": None,
            }
            for p in recommendations[:MAX_PRODUCTS_TO_SHOW]
            if isinstance(p, dict)
        ],
        "fabrics": [
            {
                "catalog_code": f.get("catalog_code"),
                "name": f.get("name"),
                "fabric_type": f.get("fabric_type"),
                "fabric_grade": f.get("fabric_grade") or f.get("grade"),
                "season": f.get("season"),
                "seasons": f.get("seasons") or [],
                "available_colors": f.get("available_colors"),
                "embroidery": f.get("embroidery"),
                "description": f.get("description"),
                "image_url": f.get("image_url"),
            }
            for f in (state.get("fabrics") or [])
            if isinstance(f, dict)
            and (
                not state.get("selected_fabric_catalog_code")
                or str(f.get("catalog_code")) == str(state.get("selected_fabric_catalog_code"))
                or state.get("customization_stage") in (None, "fabric_selection", "preferences")
            )
        ],
        "cross_sell_items": [
            {
                "product_id": p.get("product_id"),
                "name": p.get("name"),
                "price": p.get("price"),
                "category": p.get("category"),
                "available_colors": p.get("available_colors"),
            }
            for p in (state.get("cross_sell_items") or [])[:MAX_PRODUCTS_TO_SHOW]
            if isinstance(p, dict)
        ],
        "product_details": (
            {
                k: (
                    [
                        {vk: vv for vk, vv in var.items() if vk != "image_url"}
                        if isinstance(var, dict) else var
                        for var in v
                    ]
                    if k == "variations" and isinstance(v, list)
                    else v
                )
                for k, v in (state.get("product_details") or {}).items()
                if k not in ("is_bespoke_available", "floor_price", "image_url")
            }
            if isinstance(state.get("product_details"), dict)
            else state.get("product_details")
        ),
        "inventory_result": state.get("inventory_result"),
        "close_result": state.get("close_result"),
        "style_context": state.get("style_context", []),
        "negotiation_result": _public_negotiation_result_for_llm(state.get("negotiation_result")),
        "measurement_result": state.get("measurement_result"),
        "measurement_path": state.get("measurement_path"),
        "measurement_prompt": state.get("measurement_prompt"),
        "size": state.get("size"),
        "chest": state.get("chest"),
        "waist": state.get("waist"),
        "shoulder": state.get("shoulder"),
        "sleeve": state.get("sleeve"),
        "jacket_length": state.get("jacket_length"),
        "customization_stage": state.get("customization_stage"),
        "cut_style": state.get("cut_style"),
        "handover_result": handover_result,
        "handover_llm_facts": state.get("handover_llm_facts"),
        "handover_pending": False if handover_result.get("handover_created") else state.get("handover_pending"),
        "customer_contact": contact,
        "product_image_url": None,
        "event_type": state.get("event_type"),
        "product_type": state.get("product_type"),
        "color": state.get("color"),
        "wedding_date": state.get("wedding_date"),
        "sales_stage": state.get("sales_stage"),
        "catalog_categories": state.get("catalog_categories") or [],
        "custom_design_result": state.get("custom_design_result"),
        "custom_instructions": state.get("custom_instructions"),
        "catalog_variations": [] if (state.get("selected_product_id") and _is_product_variation_inquiry(state.get("user_message", ""))) else (state.get("catalog_variations") or []),
        "category_variations": [] if (state.get("selected_product_id") and _is_product_variation_inquiry(state.get("user_message", ""))) else (state.get("category_variations") or []),
        "selected_variation_id": state.get("selected_variation_id"),
        "selected_variation_name": state.get("selected_variation_name"),
        # CRITICAL: Clear product_variations when a variation is already selected — no need for picker
        "product_variations": [] if state.get("selected_product_variation_id") else (state.get("product_variations") or []),
        "selected_product_variation_id": state.get("selected_product_variation_id"),
        "selected_product_variation_name": state.get("selected_product_variation_name"),
        "product_variation_note": state.get("product_variation_note"),
        "discovery_next_slot": state.get("discovery_next_slot"),
        "catalog_search_note": catalog_search_note,
        "available_colors_summary": state.get("available_colors_summary") or [],
        "checkout_hand_off_note": state.get("checkout_hand_off_note"),
        "checkout_url": state.get("checkout_url"),
        "product_interest_note": state.get("product_interest_note"),
        "product_variation_note": state.get("product_variation_note"),
        "selected_product_url": state.get("checkout_url"),
        "price_quote": public_price_quote(state.get("price_quote") if isinstance(state.get("price_quote"), dict) else None),
        "customize_pricing_mode": state.get("customize_pricing_mode"),
    }

    sales_stage = state.get("sales_stage") or "discovery"
    system_prompt_content = SYSTEM_PROMPT

    # Hard pricing instruction when calculator quote is ready (beats consultant fallback).
    pub_quote = context.get("price_quote") if isinstance(context.get("price_quote"), dict) else None
    if pub_quote and pub_quote.get("list_price") is not None:
        currency = pub_quote.get("currency") or "GBP"
        list_price = pub_quote.get("list_price")
        unit = pub_quote.get("unit_price")
        system_prompt_content += f"""
[CRITICAL — CALCULATOR PRICE READY]
context.price_quote has list_price={list_price} {currency} (unit_price={unit}).
- ONLY if the customer asks about price / qeemat / kitna / cost / pricing: quote this exact list_price in {currency}.
- If they did NOT ask for price: do NOT mention list_price, GBP, or any number. Continue the design/fabric conversation.
- Never invent a different figure. Never say a Style Consultant will invent a different price when list_price exists and they asked for price.
"""

    neg_pub = context.get("negotiation_result") if isinstance(context.get("negotiation_result"), dict) else None
    neg_acc = (neg_pub or {}).get("accessories") if isinstance(neg_pub, dict) else None
    acc_list = context.get("accessories") if isinstance(context.get("accessories"), list) else []
    has_named_gift = bool(
        (isinstance(neg_acc, list) and any(
            (isinstance(a, str) and a.strip()) or (isinstance(a, dict) and (a.get("name") or a.get("title")))
            for a in neg_acc
        ))
        or any(
            (isinstance(a, dict) and (a.get("name") or a.get("title")))
            for a in acc_list
        )
    )
    if not has_named_gift:
        system_prompt_content += """
[CRITICAL — NO INVENTED ACCESSORIES]
negotiation_result.accessories / accessories lists are empty or missing.
- Do NOT offer or name pocket square, stole, khussa, cufflinks, tie, turban, or any complimentary gift.
- If the customer asks for discount: hold list price; say no complimentary accessory is confirmed in stock for this piece right now; offer Style Consultant if needed.
- NEVER invent a gift SKU.
"""

    if sales_stage == "discovery":
        profile_dict = dict(state.get("customer_profile") or {})
        profile_dict["session_id"] = state.get("session_id", "default")
        profile = CustomerProfileSchema(**profile_dict)
        system_prompt_content += DISCOVERY_PLAYBOOK + discovery_engine.build_discovery_prompt_guidance(
            profile,
            catalog_categories=state.get("catalog_categories") or [],
            event_type=state.get("event_type") or profile.event_type,
            category_variations=state.get("category_variations") or [],
        )
    elif (
        sales_stage == "negotiation"
        or state.get("negotiation_result")
        or state.get("intent") == "discount_request"
        or _negotiation_awaiting_color(state)
    ):
        system_prompt_content += NEGOTIATION_PLAYBOOK
    elif sales_stage == "objection" or state.get("objection_type"):
        system_prompt_content += OBJECTION_PLAYBOOK
        system_prompt_content += f"\n- Active objection_type: {state.get('objection_type')}.\n"
    elif state.get("checkout_hand_off_note") or state.get("checkout_url") or (
        state.get("close_result") and (state.get("sales_stage") == "closing" or state.get("buying_intent") == "ready_to_buy")
    ):
        # Checkout hand-off wins over customization/bespoke playbooks — never ask size/naap.
        system_prompt_content += CLOSING_PLAYBOOK
        system_prompt_content += """
[CRITICAL — CHECKOUT READY]
checkout_url / checkout_hand_off_note is present. Confirm their approval in 1–2 short sentences
and guide them to secure checkout NOW. Do NOT ask standard size or body measurements.
Size and naap are collected on the checkout page. Do NOT invent a cart URL.
"""
        if context.get("cross_sell_items"):
            system_prompt_content += CROSS_SELL_PLAYBOOK
    elif (
        sales_stage == "customization"
        or state.get("custom_design_result")
        or state.get("custom_image_url")
        or "generate_custom_design" in (state.get("executed_nodes") or [])
    ):
        system_prompt_content += CUSTOMIZATION_PLAYBOOK
        custom_img = state.get("custom_image_url") or (
            state.get("custom_design_result")
            if isinstance(state.get("custom_design_result"), dict)
            else {}
        ).get("image_url")
        if custom_img:
            system_prompt_content += """
[CRITICAL INSTRUCTION: BESPOKE DESIGN ALREADY CREATED]
The bespoke visual design concept has ALREADY been generated successfully and is displayed in the showroom visual card!
- Strictly NEVER write "Image:" or markdown image links in your text reply. The bespoke visual card is rendered exclusively by the frontend.
- You MUST directly, proudly, and enthusiastically present this newly created bespoke design to the customer right now.
- Do NOT ask "kya aap customize karwana chahenge" or ask if they want to customize it — IT IS ALREADY CREATED!
- Describe the bespoke piece briefly.
- Invite their feedback ("Does this match your vision? / Kya yeh aapke vision ke mutabiq hai?").
- Do NOT ask for standard size or body measurements in chat — those are on the checkout page.
- When they say pasand / perfect / I like this: hand them to checkout — never collect size/naap here.
"""
        elif state.get("customization_stage") == "cut_style" or (
            state.get("selected_fabric_catalog_code") and not state.get("cut_style")
        ):
            selected_code = state.get("selected_fabric_catalog_code")
            selected_name = None
            for f in context.get("fabrics") or []:
                if str(f.get("catalog_code")) == str(selected_code):
                    selected_name = f.get("name")
                    break
            label = selected_name or selected_code or "selected fabric"
            system_prompt_content += f"""
[CRITICAL INSTRUCTION: FABRIC CHOSEN — ASK DESIGN PREFERENCE ONLY]
Customer already chose fabric: {label} (catalog {selected_code}).
- Reply in 1–2 short sentences matching their language.
- Confirm the fabric choice briefly, then ask ONLY what design/cut they want
  (Sherwani, Angrakha, Bandhgala, Achkan, Prince Coat, Tuxedo / 3-piece, embroidery light or heavy).
- Do NOT list fabric colour variants, other fabrics, catalog codes, or invent cloth names.
- Do NOT generate or claim an image yet.
"""
            if catalog_search_note:
                system_prompt_content += f"\n[STAGE NOTE]\n{catalog_search_note}\n"
        else:
            system_prompt_content += """
[CRITICAL INSTRUCTION: GATHER MISSING CUSTOMIZATION DETAILS]
The customer wants a customized piece, but specific customization details (such as desired color, fabric type, or cut) have not been specified yet.
- Enthusiastically confirm that bespoke customization is fully possible.
- Ask them which specific color or styling details they have in mind — only from live context, never invent fabric names.
"""
            if catalog_search_note:
                system_prompt_content += f"\n[STAGE NOTE]\n{catalog_search_note}\n"
    elif sales_stage == "styling" or state.get("intent") == "style_advice":
        system_prompt_content += STYLING_PLAYBOOK
    elif state.get("cross_sell_items") or "suggest_cross_sell" in (state.get("required_steps") or []):
        system_prompt_content += CROSS_SELL_PLAYBOOK
    elif state.get("product_interest_note") and not state.get("checkout_hand_off_note"):
        system_prompt_content += (
            "\n[PLAYBOOK: SELECTED PIECE CONFIRM]\n"
            f"{state.get('product_interest_note')}\n"
            "Do NOT say 'Yeh hain hamare options' / 'Here are our pieces'. "
            "Do NOT re-list catalog products. Speak as if the piece is already chosen and move the sale forward.\n"
        )
        if state.get("sales_stage") == "closing":
            system_prompt_content += (
                "Invite checkout next. Share checkout_url ONLY if checkout_hand_off_note is present. "
                "Do NOT ask for body measurements in chat.\n"
            )
    elif (
        state.get("intent") == "fabric_custom"
        or "search_fabrics" in (state.get("executed_nodes") or [])
        or "search_fabrics" in (state.get("required_steps") or [])
        or context.get("fabrics") is not None
        and state.get("customization_stage") == "fabric_selection"
    ):
        system_prompt_content += CUSTOM_FABRIC_PLAYBOOK
        fabric_rows = context.get("fabrics") or []
        if not fabric_rows:
            system_prompt_content += (
                "\n[CRITICAL — NO FABRIC SWATCHES FOR THIS GARMENT/EVENT]\n"
                f"{catalog_search_note or 'No matching fabrics in catalogue for this category.'}\n"
                "Reply in the customer's language. Do NOT say 'Yeh hain hamare fabrics'. "
                "Do NOT invent or list Suit/Super-120 cloths for Nikah/Sherwani. "
                "Offer ready-made Sherwani browse or a Style Consultant for bespoke cloth sourcing.\n"
            )
        else:
            system_prompt_content += (
                "\n[CRITICAL — FABRIC CARDS ALREADY RENDERED]\n"
                f"fabrics has {len(fabric_rows)} row(s). Reply with ONE short intro only.\n"
            )
    elif state.get("checkout_hand_off_note") or state.get("product_variation_note") or (
        context["recommendations"]
        or state.get("product_details")
        or state.get("catalog_search_note")
        or state.get("sales_stage") == "recommendation"
    ):
        system_prompt_content += RECOMMENDATION_PLAYBOOK
        if recommendations and not state.get("product_interest_note"):
            cat = str(
                (recommendations[0] or {}).get("category")
                or state.get("product_type")
                or "atelier"
            )
            system_prompt_content += (
                "\n[CRITICAL — PRODUCT CARDS ALREADY RENDERED]\n"
                f"recommendations has {len(recommendations)} row(s) for '{cat}'. "
                "Your ENTIRE reply MUST be ONE short line only, e.g. "
                f"'Yeh hain hamare {cat} options:' or 'Here are our {cat} pieces:'. "
                "Do NOT mention fabric, price, colours, Achkan/Maharaja/Embroidered, or product names. "
                "Do NOT write stories. Max 20 words.\n"
            )
        if state.get("checkout_hand_off_note") or state.get("close_result"):
            system_prompt_content += CLOSING_PLAYBOOK
    elif (
        sales_stage == "closing"
        or state.get("close_result")
        or state.get("buying_intent") == "ready_to_buy"
        or state.get("handover_pending")
    ):
        system_prompt_content += CLOSING_PLAYBOOK
        if context.get("cross_sell_items"):
            system_prompt_content += CROSS_SELL_PLAYBOOK
    elif (
        state.get("customization_stage")
        or state.get("intent") in ("custom_product_variation", "custom_design")
        or context.get("product_variation_note")
    ):
        # Customer wants to customize a specific product/variation
        system_prompt_content += CUSTOM_FABRIC_PLAYBOOK
        if context.get("product_variation_note"):
            system_prompt_content += (
                f"\n[CUSTOMIZATION REQUEST]\n{context['product_variation_note']}\n"
                "Guide customer through customization: fabric choice, color changes, embroidery, or bespoke tailoring. "
                "Do NOT show product lists. Focus on this specific piece's customization options.\n"
            )
    elif (
        state.get("intent") == "fabric_custom"
        or context["fabrics"]
        or "search_fabrics" in (state.get("required_steps") or [])
    ):
        system_prompt_content += CUSTOM_FABRIC_PLAYBOOK
    elif state.get("inventory_result"):
        system_prompt_content += CLOSING_PLAYBOOK
    else:
        system_prompt_content += STYLING_PLAYBOOK

    try:
        with log_openai_call(logger, operation="final_response", model=_resolved_model_name()):
            response = await _active_llm().ainvoke([
                SystemMessage(content=system_prompt_content),
                HumanMessage(content=(
                    f"Recent conversation:\n" + "\n".join(history_lines) + "\n\n"
                    f"Current user message: {state['user_message']}\n\n"
                    f"Tool/context results:\n{json.dumps(context, indent=2)}"
                )),
            ])
    except Exception:
        logger.exception("final_response_node failed session_id=%s", state.get("session_id"))
        # Never bubble as HTTP 500 — keep the sales turn alive for the customer / eval.
        soft = (
            "I apologise — I’m briefly unable to complete that reply. "
            "Please try again in a moment, or share your WhatsApp so a Style Consultant can assist you. "
            "(Reply in the customer's language on the next turn.)"
        )
        return {
            "final_response": soft,
            "messages": [{"role": "assistant", "content": soft}],
            "customer_contact": contact,
            "handover_pending": bool(state.get("handover_pending", False)),
        }

    # 5-Layer Safety Net Sanitization (use full negotiation for floor guard; LLM context is stripped)
    guard_context = {
        **context,
        "negotiation_result": state.get("negotiation_result"),
        "product_details": state.get("product_details"),
    }
    sanitized_reply = guardrails.sanitize_agent_output(response.content, guard_context)
    sanitized_reply = _force_checkout_handoff_reply(
        sanitized_reply,
        user_message=state.get("user_message") or "",
        checkout_url=state.get("checkout_url"),
    )
    # Hard cap: product cards must never be preceded by long LLM stories.
    if recommendations and not (
        state.get("checkout_hand_off_note")
        or state.get("negotiation_result")
        or state.get("product_interest_note")
        or state.get("custom_image_url")
        or state.get("custom_design_result")
        or _negotiation_awaiting_color(state)
        or _in_active_negotiation(state)
    ):
        sanitized_reply = _force_short_product_reply(
            sanitized_reply,
            state.get("user_message") or "",
            [p for p in recommendations if isinstance(p, dict)],
        )
    elif (
        not recommendations
        and state.get("sales_stage") == "recommendation"
        and state.get("intent") in ("product_search", "accessories_search", "mixed")
        and "search_products" in (state.get("required_steps") or state.get("executed_nodes") or [])
        and not (state.get("checkout_hand_off_note") or state.get("negotiation_result"))
    ):
        # ONLY after a real empty product search — never overwrite discovery preference asks.
        note = str(state.get("catalog_search_note") or "").lower()
        empty_markers = (
            "unavailable",
            "no rows",
            "budget_too_low",
            "already been presented",
            "out of stock",
            "not in the ready-made",
        )
        if any(m in note for m in empty_markers) or not note:
            urdu = _looks_roman_urdu(state.get("user_message") or "")
            if "unavailable" in note:
                sanitized_reply = (
                    "Catalogue abhi thodi der ke liye available nahi — dobara try karein?"
                    if urdu
                    else "Catalogue is briefly unavailable — shall I try again?"
                )
            elif "already been presented" in note:
                # Keep LLM wording if short; otherwise one warm closer.
                if len((sanitized_reply or "").split()) > 40:
                    cat = state.get("product_type") or "atelier"
                    sanitized_reply = (
                        f"In '{cat}' pieces pehle dikha chuke hain — kisi ko customize karein, ya Style Consultant?"
                        if urdu
                        else f"You've already seen our '{cat}' pieces — customize one, or speak with a Style Consultant?"
                    )
            elif len((sanitized_reply or "").split()) > 35:
                cat = state.get("product_type") or "atelier"
                sanitized_reply = (
                    f"Is waqt '{cat}' ready-made options limited hain — Style Consultant se connect karein?"
                    if urdu
                    else f"Ready-made '{cat}' options are limited right now — connect with a Style Consultant?"
                )

    logger.info(
        "final_response_node finish session_id=%s reply_length=%s sanitized=%s",
        state.get("session_id"),
        safe_len(sanitized_reply),
        sanitized_reply != response.content,
    )
    handover_pending = state.get("handover_pending", False)
    if handover_result.get("status") == "pending_contact":
        handover_pending = True

    return {
        "final_response": sanitized_reply,
        "messages": [{"role": "assistant", "content": sanitized_reply}],
        "customer_contact": contact,
        "handover_pending": handover_pending,
        "price_quote": state.get("price_quote"),
        "customize_pricing_mode": state.get("customize_pricing_mode"),
        "customer_profile": state.get("customer_profile"),
    }

