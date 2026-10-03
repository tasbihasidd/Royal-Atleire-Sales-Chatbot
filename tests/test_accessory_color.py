"""Unit tests for accessory colour matching and single-gift selection."""
from __future__ import annotations

from app.schemas.negotiation import NegotiationStateSchema
from app.services.accessories_service import (
    color_matches_product,
    filter_accessories_for_product,
    filter_free_gift_candidates,
    resolve_garment_color_for_accessory,
)
from app.services.negotiation_engine import negotiation_engine


def test_exact_color_match_scores_highest():
    matches, score = color_matches_product(["Black"], "Black")
    assert matches is True
    assert score == 3


def test_neutral_black_accessory_matches_navy():
    matches, score = color_matches_product(["Black"], "Navy")
    assert matches is True
    assert score == 1


def test_gold_complements_navy():
    matches, score = color_matches_product(["Gold"], "Navy")
    assert matches is True
    assert score == 2


def test_white_accessory_does_not_match_black_sherwani():
    matches, score = color_matches_product(["White", "Ivory"], "Black")
    assert matches is False
    assert score == 0


def test_resolve_asks_when_multiple_colors_and_no_choice():
    resolved, available, pending = resolve_garment_color_for_accessory(
        None,
        ["Black", "Gold", "Maroon"],
    )
    assert resolved is None
    assert pending is True
    assert available == ["Black", "Gold", "Maroon"]


def test_resolve_palette_dark_is_not_a_specific_color():
    resolved, available, pending = resolve_garment_color_for_accessory(
        "Dark",
        ["Black", "Gold", "Maroon"],
    )
    assert resolved is None
    assert pending is True
    assert "Black" in available


def test_resolve_named_color_from_options():
    resolved, _, pending = resolve_garment_color_for_accessory(
        "Black",
        ["Black", "Gold", "Maroon"],
    )
    assert resolved == "Black"
    assert pending is False


def test_resolve_single_available_color_auto_selects():
    resolved, _, pending = resolve_garment_color_for_accessory(None, ["Navy"])
    assert resolved == "Navy"
    assert pending is False


def test_filter_returns_one_color_matched_accessory():
    accessories = [
        {
            "accessory_id": "white",
            "name": "White shawl",
            "price": 80,
            "pairs_with_categories": ["Sherwani"],
            "available_colors": ["White", "Ivory"],
        },
        {
            "accessory_id": "black",
            "name": "Black shawl",
            "price": 90,
            "pairs_with_categories": ["Sherwani"],
            "available_colors": ["Black"],
        },
        {
            "accessory_id": "gold",
            "name": "Gold stole",
            "price": 100,
            "pairs_with_categories": ["Sherwani"],
            "available_colors": ["Gold"],
        },
    ]
    out = filter_accessories_for_product(
        accessories,
        product_category="Sherwani",
        product_color="Black",
        limit=1,
    )
    assert len(out) == 1
    assert out[0]["name"] == "Black shawl"


def test_free_gift_limit_one_within_margin():
    accessories = [
        {
            "accessory_id": "a1",
            "name": "Black shawl",
            "price": 80,
            "pairs_with_categories": ["Prince Coat"],
            "available_colors": ["Black"],
        },
        {
            "accessory_id": "a2",
            "name": "Premium Shawl",
            "price": 90,
            "pairs_with_categories": ["Prince Coat"],
            "available_colors": ["Black"],
        },
    ]
    gifted = filter_free_gift_candidates(
        accessories,
        list_price=300,
        floor_price=200,
        product_category="Prince Coat",
        currency="GBP",
        product_color="Black",
    )
    assert len(gifted) == 1
    assert gifted[0]["name"] == "Black shawl"


def test_negotiation_round2_asks_color_before_gift():
    state = NegotiationStateSchema()
    state, _ = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=300,
        floor_price_override=200,
        accessories=[],
        currency="GBP",
        margin_budget=100,
    )
    accessories = [{"name": "Black shawl", "price": 80, "accessory_type": "Stole"}]
    state, strategy = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=300,
        floor_price_override=200,
        accessories=accessories,
        currency="GBP",
        margin_budget=100,
        bundle_offer={"type": "FREE"},
        product_colors=["Black", "Gold", "Maroon"],
        color_pending=True,
    )
    assert strategy["action"] == "ask_color_preference"
    assert strategy["free_accessory"] is False
    assert strategy["accessories"] == []
    assert "black" in strategy["prompt_directive"].lower()
    assert "do not offer a complimentary accessory" in strategy["prompt_directive"].lower()
    assert state.last_action == "ask_color_preference"


def test_negotiation_resumes_gift_after_color_reply():
    """Colour ask is a pause in round 2 — answering it must gift, not skip to round 3."""
    state = NegotiationStateSchema()
    state, _ = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=300,
        floor_price_override=200,
        accessories=[],
        currency="GBP",
        margin_budget=100,
    )
    accessories = [{"name": "Gold stole", "price": 80, "accessory_type": "Stole"}]
    state, _ = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=300,
        floor_price_override=200,
        accessories=accessories,
        currency="GBP",
        margin_budget=100,
        bundle_offer={"type": "FREE"},
        product_colors=["Black", "Gold", "Maroon"],
        color_pending=True,
    )
    assert state.round_number == 2
    assert state.last_action == "ask_color_preference"

    state, strategy = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=300,
        floor_price_override=200,
        accessories=accessories,
        currency="GBP",
        margin_budget=100,
        bundle_offer={"type": "FREE"},
        product_color="Gold",
        product_colors=["Black", "Gold", "Maroon"],
        color_pending=False,
    )
    assert state.round_number == 2
    assert strategy["action"] == "offer_free_accessory"
    assert strategy["free_accessory"] is True
    assert strategy["accessories"][0]["name"] == "Gold stole"


def test_negotiation_round2_one_color_matched_gift():
    state = NegotiationStateSchema()
    state, _ = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=300,
        floor_price_override=200,
        accessories=[],
        currency="GBP",
        margin_budget=100,
    )
    accessories = [
        {"name": "Black shawl", "price": 80, "accessory_type": "Stole"},
        {"name": "Premium Shawl", "price": 90, "accessory_type": "Stole"},
    ]
    _, strategy = negotiation_engine.process_negotiation_round(
        current_state=state,
        list_price=300,
        floor_price_override=200,
        accessories=accessories,
        currency="GBP",
        margin_budget=100,
        bundle_offer={"type": "FREE"},
        product_color="Black",
        product_colors=["Black", "Gold", "Maroon"],
        color_pending=False,
    )
    assert strategy["action"] == "offer_free_accessory"
    assert strategy["free_accessory"] is True
    assert len(strategy["accessories"]) == 1
    assert strategy["accessories"][0]["name"] == "Black shawl"
    assert "black shawl" in strategy["prompt_directive"].lower()
    assert "do not offer a second accessory" in strategy["prompt_directive"].lower()
