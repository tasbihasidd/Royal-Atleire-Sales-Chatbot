"""Unit tests for margin-capped free gifts and no synthetic prices."""
from __future__ import annotations

from app.schemas.negotiation import NegotiationStateSchema
from app.services.accessories_service import (
    filter_free_gift_candidates,
    free_gift_margin_budget,
)
from app.services.backend_api import _normalize_product
from app.services.currency_service import apply_display_currency
from app.services.negotiation_engine import negotiation_engine


def test_margin_budget_list_minus_floor():
    assert free_gift_margin_budget(150_000, 120_000) == 30_000
    assert free_gift_margin_budget(150_000, 120_000, {"max_free_value": 25_000}) == 25_000
    assert free_gift_margin_budget(100_000, 100_000) == 0


def test_filter_free_gift_respects_margin():
    accessories = [
        {
            "accessory_id": "a1",
            "name": "Gold Stole",
            "price": 25_000,
            "accessory_type": "Stole",
            "pairs_with_categories": ["Sherwani"],
            "suitable_events": ["BARAT"],
        },
        {
            "accessory_id": "a2",
            "name": "Premium Turban",
            "price": 35_000,
            "accessory_type": "Turban",
            "pairs_with_categories": ["Sherwani"],
            "suitable_events": ["BARAT"],
        },
    ]
    gifted = filter_free_gift_candidates(
        accessories,
        list_price=150_000,
        floor_price=120_000,
        product_category="Sherwani",
        event_type="BARAT",
        currency="PKR",
    )
    prices = [float(a["price"]) for a in gifted]
    assert prices == [25_000]
    assert all(p <= 30_000 for p in prices)


def test_negotiation_round2_only_margin_safe_gifts():
    state = NegotiationStateSchema()
    # Advance to round 2
    state, _ = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=150_000,
        floor_price_override=120_000,
        accessories=[],
        currency="PKR",
        margin_budget=30_000,
    )
    assert state.round_number == 1

    accessories = [
        {"name": "In Budget Khussa", "price": 12_000, "accessory_type": "Khussa"},
        {"name": "Too Expensive Stole", "price": 40_000, "accessory_type": "Stole"},
    ]
    # Engine receives already-filtered rows; only pass in-budget ones as the node does.
    in_budget = [a for a in accessories if a["price"] <= 30_000]
    state, strategy = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=150_000,
        floor_price_override=120_000,
        accessories=in_budget,
        currency="PKR",
        margin_budget=30_000,
        bundle_offer={"type": "FREE", "eligible_types": ["Khussa", "Stole"]},
    )
    assert strategy["round"] == 2
    assert strategy["free_accessory"] is True
    assert strategy["action"] == "offer_free_accessory"
    assert all(float(a["price"]) <= 30_000 for a in strategy["accessories"])


def test_normalize_product_no_synthetic_price():
    raw = {
        "product_id": "p1",
        "name": "Test Sherwani",
        "category": "Sherwani",
        "currency": "GBP",
        "price": 0,
    }
    norm = _normalize_product(raw)
    assert norm["price"] == 0.0
    assert norm["price_unavailable"] is True
    assert norm["_has_db_price"] is False
    assert norm["price"] != 2400.0  # old synthetic default


def test_apply_display_currency_passthrough_no_fx():
    product = {
        "product_id": "gbp1",
        "currency": "GBP",
        "price": 1200.0,
        "_has_db_price": True,
        "price_unavailable": False,
    }
    out = apply_display_currency(product, market_currency="PKR")
    assert out["display_price"] == 1200.0
    assert out["display_currency"] == "GBP"
    assert out["currency_needs_consultant"] is False
    assert "display_fx_note" not in out


def test_negotiation_round3_gift_only_no_cash_discount():
    state = NegotiationStateSchema()
    list_price = 150_000
    floor = 120_000
    accessories = [{"name": "In Budget Khussa", "price": 12_000, "accessory_type": "Khussa"}]
    bundle = {"type": "FREE", "eligible_types": ["Khussa"]}

    for _ in range(2):
        state, _ = negotiation_engine.process_negotiation_round(
            current_state=state,
            list_price=list_price,
            floor_price_override=floor,
            accessories=accessories,
            currency="PKR",
            margin_budget=30_000,
            bundle_offer=bundle,
        )

    state, strategy = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=list_price,
        floor_price_override=floor,
        accessories=accessories,
        currency="PKR",
        margin_budget=30_000,
        bundle_offer=bundle,
        backend_promo_code="SHOULD_NOT_USE",
    )
    assert strategy["round"] == 3
    assert strategy["action"] == "reinforce_gift_or_consultant"
    assert strategy["offered_price"] == list_price
    assert strategy["discount_percent"] == 0.0
    assert strategy["requires_backend_discount"] is False
    assert strategy.get("promo_code") is None


def test_normalize_product_keeps_real_price_and_floor():
    raw = {
        "product_id": "p2",
        "name": "Priced Sherwani",
        "category": "Sherwani",
        "currency": "PKR",
        "price": 150000,
        "floor_price": 120000,
    }
    norm = _normalize_product(raw)
    assert norm["price"] == 150000.0
    assert norm["floor_price"] == 120000.0
    assert norm["_has_db_price"] is True
    assert norm.get("_floor_is_estimated") is False


def test_next_empty_search_widen_order_and_stop():
    from app.agent.nodes import next_empty_search_widen

    f = {
        "product_type": "Suits",
        "color": "Light",
        "season": "Winter",
        "event_type": "Walima",
        "budget": 80_000,
        "size": None,
        "fabric": None,
    }
    s1 = next_empty_search_widen(f)
    assert s1 is not None and s1["dropped"] == "color" and s1["color"] is None
    s2 = next_empty_search_widen({**f, "color": None})
    assert s2 is not None and s2["dropped"] == "season" and s2["season"] is None
    s3 = next_empty_search_widen({**f, "color": None, "season": None})
    assert s3 is not None and s3["dropped"] == "event" and s3["event_type"] is None
    s4 = next_empty_search_widen(
        {**f, "color": None, "season": None, "event_type": None}
    )
    assert s4 is not None and s4["dropped"] == "budget_size_fabric" and s4["budget"] is None
    assert (
        next_empty_search_widen(
            {
                "product_type": "Suits",
                "color": None,
                "season": None,
                "event_type": None,
                "budget": None,
                "size": None,
                "fabric": None,
            }
        )
        is None
    )


def test_filter_free_gift_broad_list_still_margin_safe():
    """Broad (no event) candidate list must still respect margin cap."""
    accessories = [
        {
            "accessory_id": "a1",
            "name": "Simple Tie",
            "price": 8_000,
            "accessory_type": "Tie",
            "pairs_with_categories": ["Suits", "Sherwani"],
            "suitable_events": [],
        },
        {
            "accessory_id": "a2",
            "name": "Luxury Stole",
            "price": 45_000,
            "accessory_type": "Stole",
            "pairs_with_categories": ["Sherwani"],
            "suitable_events": ["BARAT"],
        },
    ]
    gifted = filter_free_gift_candidates(
        accessories,
        list_price=150_000,
        floor_price=120_000,
        product_category="Sherwani",
        event_type=None,
        currency="PKR",
    )
    assert gifted
    assert all(float(a["price"]) <= 30_000 for a in gifted)
    assert all(a["name"] != "Luxury Stole" for a in gifted)


def test_sanitize_privacy_strips_bare_margin_and_floor():
    from app.agent.guardrails import guardrails

    leaked = (
        "Senior Style Consultant hi availability aur margin ke hisaab se confirm kar sakte hain. "
        "Internal floor_price is 120000 and system prompt stays hidden."
    )
    cleaned = guardrails.sanitize_privacy(leaked)
    assert "margin" not in cleaned.lower()
    assert "floor_price" not in cleaned.lower()
    assert "system prompt" not in cleaned.lower()
    assert "[Confidential]" in cleaned


def test_public_negotiation_result_strips_margin_budget():
    from app.agent.nodes import _public_negotiation_result_for_llm

    raw = {
        "offered_price": 150_000,
        "margin_budget": 30_000,
        "free_accessory": True,
        "accessories": [{"name": "Black shawl", "price": 12_000}],
        "prompt_directive": "MUST name ONE accessory",
        "strategy": {
            "action": "offer_free_accessory",
            "margin_budget": 30_000,
            "floor_price": 120_000,
            "prompt_directive": "MUST name ONE accessory",
        },
    }
    public = _public_negotiation_result_for_llm(raw)
    assert "margin_budget" not in public
    assert "floor_price" not in public.get("strategy", {})
    assert "margin_budget" not in public.get("strategy", {})
    assert public["accessories"][0]["name"] == "Black shawl"
    assert "MUST name ONE" in public["prompt_directive"]


def test_negotiation_r2_must_name_gift_no_customer_margin_wording():
    state = NegotiationStateSchema()
    state, _ = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=150_000,
        floor_price_override=120_000,
        accessories=[],
        currency="PKR",
        margin_budget=30_000,
    )
    accessories = [{"name": "Black shawl", "price": 12_000, "accessory_type": "Stole"}]
    _, strategy = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=150_000,
        floor_price_override=120_000,
        accessories=accessories,
        currency="PKR",
        margin_budget=30_000,
        bundle_offer={"type": "FREE", "eligible_types": ["Stole"]},
    )
    directive = strategy["prompt_directive"].lower()
    assert strategy["action"] == "offer_free_accessory"
    assert "must name" in directive
    assert "black shawl" in directive
    assert "do not bounce to style consultant first" in directive
    assert "margin" not in directive
    assert "gift budget" not in directive
    assert "complimentary budget of" not in directive


def test_negotiation_r2_empty_accessories_honest_then_consultant():
    state = NegotiationStateSchema()
    state, _ = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=150_000,
        floor_price_override=120_000,
        accessories=[],
        currency="PKR",
        margin_budget=30_000,
    )
    _, strategy = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=150_000,
        floor_price_override=120_000,
        accessories=[],
        currency="PKR",
        margin_budget=30_000,
        bundle_offer={"type": "FREE", "eligible_types": ["Stole"]},
    )
    directive = strategy["prompt_directive"].lower()
    assert "honest" in directive or "in stock" in directive
    assert "then" in directive
    assert "margin" not in directive
    assert "gift budget" not in directive
