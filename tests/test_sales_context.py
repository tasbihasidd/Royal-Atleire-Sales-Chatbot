"""Context follow-ups: like-confirm should close, colour after discount should negotiate."""
from __future__ import annotations

from app.agent.nodes import (
    _build_product_interest_note,
    _heuristic_plan,
    _in_active_negotiation,
    _is_fresh_category_browse,
    _is_like_confirm,
    _match_product_from_message,
    _negotiation_awaiting_color,
    _negotiation_last_action,
)


def test_like_confirm_roman_urdu():
    assert _is_like_confirm("mjhay ye pasand hai") is True
    assert _is_like_confirm("I like this") is True
    assert _is_like_confirm("Gold color thek hai") is True
    assert _is_like_confirm("mjhay acha lagraha hai") is True
    assert _is_like_confirm("black theek hai") is True
    assert _is_like_confirm("perfect , mjhay ye pasand hai") is True
    assert _is_like_confirm("sherwani dikhao") is False


def test_heuristic_bespoke_like_goes_to_checkout():
    plan = _heuristic_plan(
        "perfect , mjhay ye pasand hai",
        {"custom_image_url": "https://example.com/bespoke.png", "sales_stage": "customization"},
    )
    assert plan["required_steps"] == ["close_sale"]
    assert plan["intent"] == "closing"
    assert "collect_measurements" not in plan["required_steps"]


def test_awaiting_color_reads_persisted_last_action():
    state = {
        "sales_stage": "negotiation",
        "negotiation_state": {"round_number": 2, "last_action": "ask_color_preference"},
    }
    assert _negotiation_last_action(state) == "ask_color_preference"
    assert _negotiation_awaiting_color(state) is True
    assert _in_active_negotiation(state) is True


def test_heuristic_like_confirm_does_not_search():
    plan = _heuristic_plan(
        "mjhay ye pasand hai",
        {"selected_product_id": "prod-1", "sales_stage": "detail"},
    )
    assert "search_products" not in plan["required_steps"]


def test_heuristic_color_after_discount_continues_negotiation():
    plan = _heuristic_plan(
        "Gold color thek hai",
        {
            "selected_product_id": "prod-1",
            "sales_stage": "negotiation",
            "negotiation_state": {"round_number": 2, "last_action": "ask_color_preference"},
        },
    )
    assert "calculate_negotiation_offer" in plan["required_steps"]
    assert "search_products" not in plan["required_steps"]
    assert plan["intent"] == "discount_request"


def test_close_advance_note_forbids_catalog_dump():
    note = _build_product_interest_note(
        product_name="Blue Nawab Signature Shrwani",
        needs_variation=False,
        advance_close=True,
    )
    lowered = note.lower()
    assert "already selected" in lowered
    assert "here are our options" in lowered
    assert "closing" in lowered or "checkout" in lowered


def test_prince_coats_dikhao_does_not_match_prince_cut_sherwani():
    """'prince' in '(prince Cut)' must not SKU-lock when browsing Prince coats."""
    products = [
        {
            "product_id": "6edfb5f2-9b0e-4a00-becb-21b7c0cb13ab",
            "name": "Cream Gold Brooch Embroidered Sherwani (prince Cut)",
        }
    ]
    assert _match_product_from_message("mjhay prince coats dikhao", products) is None
    assert _is_fresh_category_browse(
        "mjhay prince coats dikhao",
        [{"name": "Prince coat", "product_count": 6}],
    )


def test_heuristic_prince_coats_dikhao_searches_despite_selected_sku():
    plan = _heuristic_plan(
        "mjhay prince coats dikhao",
        {
            "selected_product_id": "6edfb5f2-9b0e-4a00-becb-21b7c0cb13ab",
            "sales_stage": "detail",
            "product_interest_note": "Customer confirmed they like Cream Gold…",
            "catalog_categories": [
                {"name": "Prince coat", "product_count": 6},
                {"name": "Sherwani", "product_count": 23},
            ],
        },
    )
    assert "search_products" in plan["required_steps"]
    assert "get_product_details" not in plan["required_steps"]
    assert plan["intent"] == "product_search"


def test_nikah_event_fit_suggests_prince_coat_and_waistcoat():
    from app.agent.discovery_engine import discovery_engine

    cats = [
        {"name": "Sherwani", "product_count": 23},
        {"name": "Waistcoat", "product_count": 4},
        {"name": "Prince coat", "product_count": 6},
        {"name": "Suits", "product_count": 89},
    ]
    rule, preferred, avoid = discovery_engine._event_fit_categories("Nikah", cats)
    preferred_l = [p.lower() for p in preferred]
    assert any("sherwani" in p for p in preferred_l)
    assert any("prince" in p for p in preferred_l)
    assert any("waist" in p for p in preferred_l)
    assert "sherwani-only" in rule.lower() or "never" in rule.lower()
    assert not any("prince" in a.lower() for a in avoid)
