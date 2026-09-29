"""
Deterministic checks on the chatbot's structured output — no LLM involved.

This layer exists because LLM judging alone is not a test: it is unstable, it
costs money, and it cannot see the machine-readable state. Everything here is
reproducible and free, so a CI gate can depend on it even when the judge panel is
unavailable.

Severity contract:
  critical — ships a commercial or safety bug (price leak, crash, invented price)
  major    — funnel is broken but nothing dangerous was said
  minor    — polish / hygiene
"""
from __future__ import annotations

import re
from typing import Any

from tests.eval_suite.models import Assertion, Persona, Session, Turn

# Words that must never appear in customer-visible text.
_LEAK_PATTERNS = [
    (r"\bfloor[_ ]?price\b", "internal floor_price mentioned"),
    (r"\bwholesale[_ ]?cost\b", "wholesale cost mentioned"),
    (r"\bcost[_ ]?price\b", "cost price mentioned"),
    (r"\bmargin\b", "margin mentioned"),
    (r"\bsystem prompt\b", "system prompt mentioned"),
    (r"\bprompt_directive\b", "internal prompt_directive leaked"),
    (r"\brequired_steps\b", "internal required_steps leaked"),
    (r"\bsales_stage\b", "internal sales_stage leaked"),
]

# Raw serialised data structures leaking into prose.
_RAW_PAYLOAD_PATTERNS = [
    (r"\{\s*['\"]product_id['\"]", "raw product dict rendered in reply"),
    (r"\bNone\b", "Python None rendered in reply"),
    (r"\bTrue\b|\bFalse\b", "Python boolean rendered in reply"),
    (r"```", "code fence in a sales reply"),
]

_PRICE_RE = re.compile(
    r"(?:(?:PKR|Rs\.?|rupees|GBP|£|\$)\s*([\d,]+(?:\.\d+)?))"
    r"|(?:([\d,]{4,})\s*(?:PKR|Rs\.?|rupees))"
    r"|(?:\b(\d{2,3})\s*(?:k|hazar|hazaar)\b)",
    re.IGNORECASE,
)

_URDU_MARKERS = {
    "hai", "hy", "ha", "kya", "kia", "aap", "ap", "mujhe", "mjhe", "mera", "meri",
    "chahiye", "chahe", "kitne", "kitna", "nhi", "nahi", "yaar", "bhai", "acha",
    "thora", "karo", "kar", "ke", "ka", "ki", "ko", "me", "mein", "se", "par", "pe",
    "hoga", "hogi", "raha", "rahe", "dikhao", "batao", "janab", "shukriya",
    "assalam", "salam", "walaikum", "din", "mil", "sakta", "phir", "warna", "koi",
    "kuch", "bohat", "bahut", "zyada", "kam", "wala", "wali", "hun", "hoon",
}

_ENGLISH_ONLY_MARKERS = {
    "the", "and", "you", "your", "for", "with", "have", "would", "could", "this",
    "that", "what", "which", "please", "thank", "looking", "want", "need", "about",
    "can", "will", "our", "we", "is", "are", "it", "of", "to", "in", "on",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def classify_language(text: str) -> str:
    """Rough register classifier: 'roman_urdu' | 'english' | 'mixed' | 'unknown'."""
    toks = _tokens(text)
    if len(toks) < 3:
        return "unknown"
    urdu_hits = len(toks & _URDU_MARKERS)
    eng_hits = len(toks & _ENGLISH_ONLY_MARKERS)
    if urdu_hits == 0 and eng_hits >= 2:
        return "english"
    if urdu_hits >= 2 and eng_hits <= 1:
        return "roman_urdu"
    if urdu_hits >= 1 and eng_hits >= 1:
        return "mixed"
    return "unknown"


def _numbers_in(text: str) -> set[int]:
    out: set[int] = set()
    for match in _PRICE_RE.finditer(text or ""):
        raw = next((g for g in match.groups() if g), None)
        if not raw:
            continue
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if raw.isdigit() and len(raw) <= 3 and value < 1000:
            value *= 1000  # "70k" / "70 hazar"
        out.add(int(value))
    return out


def _context_numbers(state: dict[str, Any]) -> set[int]:
    """Every number the bot was legitimately allowed to quote this turn."""
    allowed: set[int] = set()

    def absorb(value: Any) -> None:
        if isinstance(value, (int, float)) and value > 0:
            allowed.add(int(value))

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(node, dict):
            for key, val in node.items():
                if isinstance(val, (int, float)):
                    if any(k in key.lower() for k in ("price", "amount", "total", "quantity", "stock", "days", "discount", "percent", "meters", "round")):
                        absorb(val)
                else:
                    walk(val, depth + 1)
        elif isinstance(node, list):
            for item in node[:40]:
                walk(item, depth + 1)

    walk(state)
    # Prices are frequently rounded or expressed in thousands when spoken.
    for value in list(allowed):
        allowed.add(round(value, -3))
        allowed.add(round(value))
        allowed.add(int(value // 1000 * 1000))
    return {v for v in allowed if v > 0}


def check_turn(turn: Turn, persona: Persona, history: list[Turn]) -> list[Assertion]:
    """All deterministic checks for a single turn."""
    out: list[Assertion] = []
    reply = turn.bot_reply or ""
    state = turn.state or {}

    def add(name: str, passed: bool, severity: str, detail: str) -> None:
        out.append(Assertion(name=name, passed=passed, severity=severity, detail=detail))

    # ── Transport / liveness ────────────────────────────────────────────────
    add(
        "http_ok",
        turn.error is None and turn.http_status < 400,
        "critical",
        turn.error or f"HTTP {turn.http_status}",
    )
    if turn.error:
        return out  # nothing else is meaningful

    add("reply_non_empty", bool(reply.strip()), "critical", f"reply length={len(reply)}")

    # ── Confidential data and raw payload leaks ─────────────────────────────
    leaks = [msg for pattern, msg in _LEAK_PATTERNS if re.search(pattern, reply, re.IGNORECASE)]
    add("no_internal_leak", not leaks, "critical", "; ".join(leaks) or "clean")

    raw = [msg for pattern, msg in _RAW_PAYLOAD_PATTERNS if re.search(pattern, reply)]
    add("no_raw_payload", not raw, "major", "; ".join(raw) or "clean")

    # ── Price grounding: every number spoken must exist in the tool context ──
    spoken = _numbers_in(reply)
    if spoken:
        allowed = _context_numbers(state)
        # Numbers the customer themselves introduced are fair to echo back.
        for past in history + [turn]:
            allowed |= _numbers_in(past.user_message)
        unsupported = {n for n in spoken if n >= 1000 and not any(abs(n - a) <= max(1000, a * 0.02) for a in allowed)}
        add(
            "prices_grounded",
            not unsupported,
            "critical",
            f"unsupported figures {sorted(unsupported)} (reply quoted {sorted(spoken)})"
            if unsupported
            else f"all {len(spoken)} figure(s) traceable to context",
        )

    # ── Language register ──────────────────────────────────────────────────
    user_lang = classify_language(turn.user_message)
    bot_lang = classify_language(reply)
    if user_lang == "roman_urdu" and bot_lang == "english":
        add("language_register", False, "major", "customer wrote Roman Urdu, bot replied in pure English")
    elif user_lang == "english" and bot_lang == "roman_urdu":
        add("language_register", False, "major", "customer wrote English, bot replied in Roman Urdu")
    else:
        add("language_register", True, "major", f"user={user_lang} bot={bot_lang}")

    # ── Graph execution ────────────────────────────────────────────────────
    nodes = turn.executed_nodes or []
    add("graph_ran", "planner" in nodes and "final_response" in nodes, "critical", f"nodes={nodes}")

    for forbidden in persona.forbid_nodes:
        add(f"node_forbidden_{forbidden}", forbidden not in nodes, "major", f"nodes={nodes}")

    # ── Product image hygiene ──────────────────────────────────────────────
    products = state.get("products") or []
    if turn.image_url and not products and not state.get("custom_image_url"):
        add("image_has_product", False, "minor", "image returned with no product in state")

    # ── Stale product carry-over ───────────────────────────────────────────
    if state.get("sales_stage") == "customization" and products:
        add("no_catalog_in_customization", False, "minor", f"{len(products)} catalog products during customization")

    # ── Reply length: a boutique consultant does not send essays ────────────
    add("reply_length_sane", len(reply) <= 1400, "minor", f"{len(reply)} chars")

    # ── Repetition across turns (the classic "same canned line" bug) ────────
    if history:
        previous = (history[-1].bot_reply or "").strip()
        if previous and reply.strip() == previous:
            add("not_verbatim_repeat", False, "major", "reply is byte-identical to the previous turn")
        else:
            opening = reply.strip()[:80]
            repeats = sum(1 for h in history if (h.bot_reply or "").strip()[:80] == opening)
            add("not_verbatim_repeat", repeats < 2, "minor", f"opening repeated in {repeats} earlier turn(s)")

        # A recycled closing question is the most visible "robot" tell, and it is
        # invisible to an opening-line check.
        closer = _last_sentence(reply)
        if closer:
            echoes = [h.index for h in history if _last_sentence(h.bot_reply or "") == closer]
            add(
                "closing_line_not_recycled",
                len(echoes) == 0,
                "major",
                f"closing question reused from turn(s) {echoes}: {closer[:90]!r}"
                if echoes
                else "fresh closing",
            )

    # ── Free accessory margin (list − floor) ────────────────────────────────
    neg = state.get("negotiation_result") or {}
    if isinstance(neg, dict) and neg.get("free_accessory"):
        details = state.get("product_details") or {}
        list_price = float(details.get("price") or neg.get("offered_price") or 0)
        floor = details.get("floor_price")
        try:
            floor_f = float(floor) if floor is not None else None
        except (TypeError, ValueError):
            floor_f = None
        if neg.get("margin_budget") is not None:
            try:
                margin = float(neg["margin_budget"])
            except (TypeError, ValueError):
                margin = max(0.0, list_price - floor_f) if floor_f is not None else None
        else:
            margin = max(0.0, list_price - floor_f) if floor_f is not None else None
        max_free = None
        bundle = neg.get("bundle_offer") or {}
        if isinstance(bundle, dict) and bundle.get("max_free_value") is not None:
            try:
                max_free = float(bundle["max_free_value"])
            except (TypeError, ValueError):
                max_free = None
        if margin is not None and max_free is not None:
            margin = min(margin, max_free)
        over = []
        for acc in neg.get("accessories") or []:
            if not isinstance(acc, dict):
                continue
            try:
                ap = float(acc.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if margin is not None and ap > margin + 0.01:
                over.append((acc.get("name"), ap))
        add(
            "free_accessory_within_margin",
            not over,
            "critical",
            f"gift above margin {margin}: {over}" if over else f"gifts within margin {margin}",
        )

    # ── Price plausibility (catches bad catalogue data, not bot invention) ──
    currency = _dominant_currency(state)
    if spoken and currency:
        band = _PLAUSIBLE_PRICE_BAND.get(currency)
        if band:
            low, high = band
            absurd = sorted(n for n in spoken if n > high or (n >= 1000 and n < low))
            add(
                "prices_plausible_for_currency",
                not absurd,
                "major",
                f"{currency} figures outside plausible {low:,}-{high:,} band: {absurd}"
                if absurd
                else f"{currency} figures within plausible band",
            )

    return out


def _last_sentence(text: str) -> str:
    """The final question or statement of a reply, normalised for comparison."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    parts = [p.strip() for p in re.split(r"(?<=[.!?\n])\s+", stripped) if p.strip()]
    if not parts:
        return ""
    return re.sub(r"\s+", " ", parts[-1].lower())


# Sane retail bands for a luxury wedding garment, per currency. Anything outside
# these is a catalogue data-quality problem that the agent is repeating verbatim.
_PLAUSIBLE_PRICE_BAND = {
    "PKR": (10_000, 2_000_000),
    "GBP": (100, 15_000),
    "USD": (100, 20_000),
    "EUR": (100, 18_000),
}


def _dominant_currency(state: dict[str, Any]) -> str | None:
    products = list(state.get("products") or [])
    details = state.get("product_details")
    if isinstance(details, dict):
        products.append(details)
    for product in products:
        if isinstance(product, dict) and product.get("currency"):
            return str(product["currency"]).upper()
    return None


def check_session(session: Session) -> list[Assertion]:
    """Checks that only make sense once the whole conversation is known."""
    out: list[Assertion] = []
    persona = session.persona
    turns = session.turns

    def add(name: str, passed: bool, severity: str, detail: str) -> None:
        out.append(Assertion(name=name, passed=passed, severity=severity, detail=detail))

    add("session_had_turns", bool(turns), "critical", f"{len(turns)} turns")
    if not turns:
        return out

    all_nodes = {n for t in turns for n in t.executed_nodes}
    all_stages = {t.state.get("sales_stage") for t in turns if t.state.get("sales_stage")}

    for node in persona.expect_nodes:
        add(f"expected_node_{node}", node in all_nodes, "major", f"seen={sorted(all_nodes)}")

    if persona.expect_stages:
        hit = [s for s in persona.expect_stages if s in all_stages]
        add(
            "expected_stage_reached",
            bool(hit),
            "major",
            f"wanted any of {persona.expect_stages}, saw {sorted(all_stages)}",
        )

    if persona.expect_products_shown:
        shown = any(t.state.get("products") for t in turns)
        add("products_were_shown", shown, "major", "no turn returned products")

    # Discovery must not become an interrogation.
    lead_in = 0
    for turn in turns:
        if turn.state.get("products"):
            break
        lead_in += 1
    add(
        "discovery_not_endless",
        lead_in <= 4,
        "major",
        f"{lead_in} turns before any product was shown",
    )

    # No unhandled server errors across the session.
    errors = [t.index for t in turns if t.error or t.http_status >= 400]
    add("no_server_errors", not errors, "critical", f"errors on turns {errors}" if errors else "none")

    # Slot memory: once the customer states the event, it must stick.
    events = [t.state.get("event_type") for t in turns]
    first_known = next((i for i, e in enumerate(events) if e), None)
    if first_known is not None:
        lost = [turns[i].index for i in range(first_known, len(events)) if not events[i]]
        add("event_type_retained", not lost, "major", f"event_type lost on turns {lost}" if lost else "retained")

    # Currency localisation: a Roman-Urdu customer quoting a PKR budget should
    # not be priced in GBP without any conversion or explanation.
    currencies = {c for t in turns if (c := _dominant_currency(t.state))}
    urdu_turns = sum(1 for t in turns if classify_language(t.user_message) == "roman_urdu")
    if currencies and urdu_turns >= 2:
        add(
            "currency_matches_market",
            "PKR" in currencies,
            "major",
            f"Roman Urdu customer ({urdu_turns} turns) was priced in {sorted(currencies)}",
        )

    # Latency budget.
    slow = [t.index for t in turns if t.latency_ms > 30000]
    add("latency_under_30s", not slow, "minor", f"turns over 30s: {slow}" if slow else "all under 30s")

    return out
