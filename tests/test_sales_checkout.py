"""Sales Agent Checkout API payload + close_sale checkout_url."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.nodes import close_sale_node
from app.services.backend_api import BackendAPIError
from app.services.checkout_service import build_checkout_items, build_checkout_request
from app.services.chat_store import ChatStore

CATALOG_ID = "09246cd6-0a67-4b56-923b-a2be05532616"
ACCESSORY_ID = "accessory-uuid-999"
CHECKOUT_URL = "https://turabees.com/cart?s=281dc7e0"


def test_standard_checkout_payload():
    payload = build_checkout_request(
        {
            "session_id": "sess-1",
            "selected_product_id": CATALOG_ID,
            "product_details": {"name": "Classic Black Sherwani", "product_id": CATALOG_ID},
        }
    )
    assert payload is not None
    assert payload["user_id"] == "sess-1"
    assert payload["user_type"] == "GUEST"
    assert payload["items"] == [
        {
            "item_type": "STANDARD",
            "catalog_product_id": CATALOG_ID,
            "item_name_snapshot": "Classic Black Sherwani",
        }
    ]


def test_custom_checkout_payload():
    payload = build_checkout_request(
        {
            "session_id": "sess-2",
            "custom_image_url": "https://turabees.com/uploads/ai-render-8812.jpg",
            "custom_design_result": {
                "generated_product_name": "Custom Midnight Blue Peak Lapel Suit",
                "image_url": "https://turabees.com/uploads/ai-render-8812.jpg",
            },
            "product_details": {"price": 750.0, "fabric": "Italian Wool", "fabric_id": "fabric-uuid-123"},
            "selected_fabric_catalog_code": "fabric-uuid-123",
            "color": "Midnight Blue",
        }
    )
    assert payload is not None
    assert payload["user_type"] == "GUEST"
    item = payload["items"][0]
    assert item["item_type"] == "CUSTOM"
    assert item["catalog_product_id"] is None
    assert item["item_name_snapshot"] == "Custom Midnight Blue Peak Lapel Suit"
    assert item["unit_price"] == 750.0
    assert item["custom_attributes"]["generated_image_url"] == "https://turabees.com/uploads/ai-render-8812.jpg"
    assert item["custom_attributes"]["fabric_id"] == "fabric-uuid-123"
    assert item["custom_attributes"]["fabric"] == "Italian Wool"
    assert item["custom_attributes"]["color"] == "Midnight Blue"


def test_custom_plus_accessory_payload():
    items = build_checkout_items(
        {
            "session_id": "sess-3",
            "custom_image_url": "https://example.com/render.jpg",
            "custom_design_result": {"generated_product_name": "Custom Midnight Blue Peak Lapel Suit"},
            "product_details": {"price": 750.0},
            "negotiation_result": {
                "approved": True,
                "free_accessory": True,
                "accessories_full": [{"accessory_id": ACCESSORY_ID, "name": "Silk Embroidered Turban"}],
            },
        }
    )
    assert items[0]["item_type"] == "CUSTOM"
    assert items[1]["item_type"] == "CUSTOM"
    assert items[1]["unit_price"] == 0
    assert items[1]["custom_attributes"]["accessory_id"] == ACCESSORY_ID
    assert items[1]["custom_attributes"]["complimentary"] is True


def test_accessory_not_added_during_defend_round():
    items = build_checkout_items(
        {
            "selected_product_id": CATALOG_ID,
            "product_details": {"name": "Classic Black Sherwani"},
            "negotiation_result": {
                "action": "defend_value",
                "approved": False,
                "accessories_full": [{"accessory_id": ACCESSORY_ID}],
            },
        }
    )
    assert len(items) == 1
    assert items[0]["item_type"] == "STANDARD"


def test_session_context_persists_gift_for_next_turn_checkout():
    """Turn A offer → context; Turn B hydrate → ACCESSORY on checkout payload."""
    from app.services.checkout_service import slim_negotiation_result_for_session

    store = ChatStore()
    offer = {
        "approved": True,
        "free_accessory": True,
        "action": "offer_free_accessory",
        "accessories": ["Royal Heritage Pagri - Sapphire Crest Safa"],
        "accessories_full": [
            {
                "accessory_id": ACCESSORY_ID,
                "name": "Royal Heritage Pagri - Sapphire Crest Safa",
                "accessory_type": "Turban / Pagri",
                "price": 85,
                "available_colors": ["Maroon", "Gold"],
            }
        ],
        "prompt_directive": "long text should not all persist",
        "strategy": {"action": "offer_free_accessory"},
    }
    context = store.build_session_context_from_result({"negotiation_result": offer}, {})
    slim = context["negotiation_result"]
    assert slim["free_accessory"] is True
    assert slim["accessories_full"][0]["accessory_id"] == ACCESSORY_ID
    assert "strategy" not in slim
    assert "prompt_directive" not in slim

    # Simulate next-turn state hydrated only from session context (no in-memory gift).
    payload = build_checkout_request(
        {
            "session_id": "sess-gift",
            "selected_product_id": CATALOG_ID,
            "product_details": {"name": "Champagne Gold Leaf Embroidered Sherwani", "product_id": CATALOG_ID},
            "negotiation_result": slim,
        }
    )
    assert payload is not None
    assert len(payload["items"]) == 2
    assert payload["items"][0]["item_type"] == "STANDARD"
    gift = payload["items"][1]
    assert gift["item_type"] == "CUSTOM"
    assert gift["unit_price"] == 0
    assert gift["custom_attributes"]["accessory_id"] == ACCESSORY_ID
    assert "Pagri" in gift["item_name_snapshot"]

    # Defend round clears persisted gift.
    cleared = store.build_session_context_from_result(
        {"negotiation_result": {"action": "defend_value", "approved": False}},
        context,
    )
    assert "negotiation_result" not in cleared or cleared.get("negotiation_result") is None
    assert slim_negotiation_result_for_session({"action": "defend_value"}) is None


def test_close_sale_attaches_persisted_complimentary_pagri():
    state = {
        "session_id": "sess-pagri",
        "selected_product_id": CATALOG_ID,
        "product_details": {"name": "Champagne Gold Leaf Embroidered Sherwani", "product_id": CATALOG_ID},
        "customer_contact": {},
        "inventory_result": {},
        "products": [],
        "negotiation_result": {
            "approved": True,
            "free_accessory": True,
            "action": "offer_free_accessory",
            "accessories_full": [
                {"accessory_id": ACCESSORY_ID, "name": "Royal Heritage Pagri - Sapphire Crest Safa"}
            ],
        },
    }
    mock_resp = {
        "success": True,
        "checkout_url": CHECKOUT_URL,
        "session_id": "281dc7e0",
    }
    pagri_img = "https://cdn.example.com/accessories/pagri.webp"
    with (
        patch(
            "app.agent.nodes._refresh_price_quote",
            new=AsyncMock(
                return_value={
                    "price_quote": {
                        "ok": True,
                        "unit_price": 1000,
                        "list_price": 1000,
                        "source": "catalogue",
                    }
                }
            ),
        ),
        patch(
            "app.agent.nodes.backend_api.get_accessory",
            new=AsyncMock(
                return_value={
                    "accessory_id": ACCESSORY_ID,
                    "name": "Royal Heritage Pagri - Sapphire Crest Safa",
                    "image_url": pagri_img,
                }
            ),
        ),
        patch(
            "app.agent.nodes.backend_api.create_checkout_session",
            new=AsyncMock(return_value=mock_resp),
        ) as mock_checkout,
    ):
        out = asyncio.run(close_sale_node(state))
    body = mock_checkout.await_args.args[0]
    assert len(body["items"]) == 2
    gift_line = body["items"][1]
    assert gift_line["item_type"] == "CUSTOM"
    assert gift_line["unit_price"] == 0
    assert gift_line["custom_attributes"]["accessory_id"] == ACCESSORY_ID
    assert gift_line["custom_attributes"]["generated_image_url"] == pagri_img
    note = out.get("checkout_hand_off_note") or ""
    assert "Pagri" in note or "complimentary" in note.lower()
    assert CHECKOUT_URL in note


def test_complimentary_accessory_is_zero_priced_not_catalogue():
    """Gift pagri must not inflate bag total (e.g. £1000 + £85 → £1085).

    Turabees ignores unit_price on ACCESSORY, so complimentary gifts go as CUSTOM @ 0.
    """
    pagri_img = "https://cdn.example.com/accessories/pagri.webp"
    items = build_checkout_items(
        {
            "selected_product_id": CATALOG_ID,
            "product_details": {
                "name": "Champagne Gold Leaf Embroidered Sherwani",
                "product_id": CATALOG_ID,
                "price": 1000,
            },
            "negotiation_result": {
                "approved": True,
                "free_accessory": True,
                "action": "offer_free_accessory",
                "accessories_full": [
                    {
                        "accessory_id": ACCESSORY_ID,
                        "name": "Royal Heritage Pagri - Sapphire Crest Safa",
                        "price": 85,
                        "image_url": pagri_img,
                    }
                ],
            },
        }
    )
    assert len(items) == 2
    assert items[0]["item_type"] == "STANDARD"
    assert items[1]["item_type"] == "CUSTOM"
    assert items[1]["unit_price"] == 0
    assert items[1]["custom_attributes"]["accessory_id"] == ACCESSORY_ID
    assert items[1]["custom_attributes"]["generated_image_url"] == pagri_img
    assert items[1]["custom_attributes"]["image_url"] == pagri_img
    # Paid upsell keeps catalogue ACCESSORY pricing.
    paid = build_checkout_items(
        {
            "selected_product_id": CATALOG_ID,
            "product_details": {"product_id": CATALOG_ID},
            "negotiation_result": {
                "approved": True,
                "free_accessory": False,
                "action": "offer_paid_accessory_upsell",
                "accessories_full": [{"accessory_id": ACCESSORY_ID, "price": 85}],
            },
        }
    )
    assert paid[1] == {"item_type": "ACCESSORY", "accessory_id": ACCESSORY_ID}
    assert "unit_price" not in paid[1]


def test_slim_persists_gift_image_url():
    from app.services.checkout_service import slim_negotiation_result_for_session

    slim = slim_negotiation_result_for_session(
        {
            "approved": True,
            "free_accessory": True,
            "action": "offer_free_accessory",
            "accessories_full": [
                {
                    "accessory_id": ACCESSORY_ID,
                    "name": "Royal Heritage Pagri",
                    "image_url": "https://cdn.example.com/pagri.webp",
                    "available_colors": ["Gold"],
                }
            ],
        }
    )
    assert slim is not None
    assert slim["accessories_full"][0]["image_url"] == "https://cdn.example.com/pagri.webp"


def test_close_sale_puts_checkout_url_on_state():
    state = {
        "session_id": "sess-1",
        "selected_product_id": CATALOG_ID,
        "product_details": {"name": "Classic Black Sherwani", "product_id": CATALOG_ID},
        "customer_contact": {},
        "inventory_result": {},
        "products": [],
    }
    mock_resp = {
        "success": True,
        "checkout_url": CHECKOUT_URL,
        "session_id": "281dc7e0",
    }
    with (
        patch(
            "app.agent.nodes._refresh_price_quote",
            new=AsyncMock(return_value={"price_quote": {"ok": True, "unit_price": 400, "list_price": 400, "source": "catalogue"}}),
        ),
        patch(
            "app.agent.nodes.backend_api.create_checkout_session",
            new=AsyncMock(return_value=mock_resp),
        ) as mock_checkout,
    ):
        out = asyncio.run(close_sale_node(state))
    mock_checkout.assert_awaited_once()
    body = mock_checkout.await_args.args[0]
    assert body["user_type"] == "GUEST"
    assert body["items"][0]["catalog_product_id"] == CATALOG_ID
    assert out["checkout_url"] == CHECKOUT_URL
    assert out["checkout_session_id"] == "281dc7e0"
    assert CHECKOUT_URL in (out["checkout_hand_off_note"] or "")
    assert "exact checkout_url" in (out["checkout_hand_off_note"] or "")
    assert out["close_result"]["status"] == "ready_for_checkout_link"


def test_close_sale_blocks_custom_checkout_without_unit_price():
    state = {
        "session_id": "sess-custom-zero",
        "custom_image_url": "https://example.com/x.png",
        "custom_design_result": {
            "generated_product_name": "Oatmeal Linen Walima Suit",
            "image_url": "https://example.com/x.png",
        },
        "customer_contact": {},
        "inventory_result": {},
        "products": [],
    }
    with (
        patch(
            "app.agent.nodes._refresh_price_quote",
            new=AsyncMock(
                return_value={
                    "price_quote": {
                        "ok": False,
                        "source": "calculator",
                        "error": "Fabric grade missing",
                    }
                }
            ),
        ),
        patch(
            "app.agent.nodes.backend_api.create_checkout_session",
            new=AsyncMock(),
        ) as mock_checkout,
    ):
        out = asyncio.run(close_sale_node(state))
    mock_checkout.assert_not_awaited()
    assert out.get("checkout_url") in (None, "")
    assert out["close_result"]["status"] == "checkout_link_failed"


def test_close_sale_api_failure_does_not_fallback_to_catalog_url():
    state = {
        "session_id": "sess-1",
        "selected_product_id": CATALOG_ID,
        "product_details": {
            "name": "Classic Black Sherwani",
            "product_id": CATALOG_ID,
            "product_url": "https://turabees.com/product/classic-black",
        },
        "customer_contact": {},
        "inventory_result": {},
        "products": [],
    }
    with patch(
        "app.agent.nodes.backend_api.create_checkout_session",
        new=AsyncMock(side_effect=BackendAPIError("fail", status_code=400, path="/checkout")),
    ):
        out = asyncio.run(close_sale_node(state))
    assert out.get("checkout_url") in (None, "")
    assert out.get("checkout_hand_off_note") in (None, "")
    assert out["close_result"]["status"] == "checkout_link_failed"
    note = str(out["close_result"].get("note") or "")
    assert "product_url" in note.lower() or "invent" in note.lower()


def test_create_checkout_session_parses_data_envelope():
    from app.services.backend_api import backend_api

    with (
        patch.object(
            backend_api,
            "_request",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "data": {
                        "checkout_url": CHECKOUT_URL,
                        "session_id": "281dc7e0",
                        "total_amount": 870.0,
                        "currency": "GBP",
                    },
                }
            ),
        ) as mock_request,
        patch("app.services.backend_api.settings.USE_MOCK_DATA", False),
    ):
        result = asyncio.run(
            backend_api.create_checkout_session(
                {"user_id": "sess-1", "user_type": "GUEST", "items": [{"item_type": "STANDARD"}]}
            )
        )
    assert result["success"] is True
    assert result["checkout_url"] == CHECKOUT_URL
    assert result["session_id"] == "281dc7e0"
    mock_request.assert_awaited_once_with(
        "POST",
        "/checkout",
        json={"user_id": "sess-1", "user_type": "GUEST", "items": [{"item_type": "STANDARD"}]},
    )


def test_session_context_persists_checkout_url():
    store = ChatStore()
    context = store.build_session_context_from_result(
        {"checkout_url": CHECKOUT_URL, "checkout_session_id": "281dc7e0"},
        {},
    )
    assert context["checkout_url"] == CHECKOUT_URL
    assert context["checkout_session_id"] == "281dc7e0"


def test_generated_product_name_copied_from_metadata_title():
    metadata = MagicMock()
    metadata.title = "Royal Ivory Sherwani for Walima"
    metadata.model_dump.return_value = {"title": metadata.title}

    with (
        patch("app.services.custom_design_service.generate_image_bytes_fal", return_value=b"png"),
        patch("app.services.custom_design_service.save_generated_image", return_value="file.png"),
        patch("app.services.custom_design_service.generate_image_metadata", new=AsyncMock(return_value=metadata)),
        patch("app.services.custom_design_service.analyze_fabric_image", new=AsyncMock(return_value={"texture": "silk"})),
        patch("app.services.custom_design_service.image_store.save_generated_image_record", new=AsyncMock(return_value=1)),
        patch("app.services.custom_design_service.settings") as mock_settings,
    ):
        mock_settings.BASE_URL = "http://localhost:8015"
        from app.services.custom_design_service import generate_bespoke_design

        _url, result = asyncio.run(
            generate_bespoke_design(
                base_product={"name": "Ivory Sherwani", "description": "royal", "fabric": "silk"},
                fabric_details={"name": "Silk", "image_url": "https://example.com/f.jpg"},
                user_instructions="ivory with gold work",
                ceremony="Walima",
                colors="Ivory",
                dress_category="Sherwani",
                session_id="sess-1",
            )
        )
    assert result["generated_product_name"] == "Royal Ivory Sherwani for Walima"
