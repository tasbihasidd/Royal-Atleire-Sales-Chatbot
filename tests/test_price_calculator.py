"""Turabees price calculator + Path A/B quote resolver tests."""

from __future__ import annotations

import pytest

from app.services.embroidery_tier import parse_embroidery_response
from app.services.price_calculator import PriceCalculationError, calculate_turabees_price
from app.services.price_quote_service import (
    catalogue_quote_from_product,
    detect_customize_mode,
    fabric_grade_from_row,
    map_style_key,
    public_price_quote,
)
from app.services.pricing_config import (
    clear_pricing_config_cache,
    excel_reference_pricing_config,
    normalize_pricing_config,
    resolve_delivery_fee,
    set_cached_pricing_config,
)


@pytest.fixture(autouse=True)
def _pricing_cache():
    clear_pricing_config_cache()
    set_cached_pricing_config(excel_reference_pricing_config())
    yield
    clear_pricing_config_cache()


def test_sherwani_light_excel_parity_unit_price():
    cfg = excel_reference_pricing_config()
    result = calculate_turabees_price(
        {
            "style": "Sherwani",
            "fabric": "Standard blend",
            "embroidery": "Light",
            "addons": [],
            "quantity": 1,
            "delivery_fee": 0,
        },
        cfg,
    )
    calc = result["calculations"]
    assert calc["fabric_cost_per_outfit"] == 45.0
    assert calc["subtotal_no_markup_per_outfit"] == 115.0
    assert calc["total_markup_fee"] == 17.25
    assert calc["price_per_outfit_with_markup"] == 132.25
    assert calc["floor_price"] == 115.0
    assert calc["grand_total"] == 132.25


def test_example_payload_with_addons_and_delivery():
    cfg = excel_reference_pricing_config()
    result = calculate_turabees_price(
        {
            "style": "Sherwani",
            "fabric": "Standard blend",
            "embroidery": "Light",
            "addons": ["stole", "waistcoat"],
            "quantity": 1,
            "delivery_fee": 15,
        },
        cfg,
    )
    calc = result["calculations"]
    # 132.25 + 5 (stole) + 20 (waistcoat) + 15 delivery = 172.25
    assert calc["price_per_outfit_with_markup"] == 132.25
    assert calc["addons_total"] == 25.0
    assert calc["delivery_fee"] == 15.0
    assert calc["grand_total"] == 172.25
    assert calc["floor_price"] == 115.0 + 25.0 + 15.0


def test_addon_outfit_vs_order_quantity():
    cfg = excel_reference_pricing_config()
    result = calculate_turabees_price(
        {
            "style": "Sherwani",
            "fabric": "Standard blend",
            "embroidery": "None",
            "addons": ["stole", "swatch"],
            "quantity": 2,
            "delivery_fee": 0,
        },
        cfg,
    )
    calc = result["calculations"]
    # stole outfit ×2 = 10; swatch order once = 5
    assert calc["addons_total"] == 15.0


def test_unknown_grade_raises():
    cfg = excel_reference_pricing_config()
    with pytest.raises(PriceCalculationError):
        calculate_turabees_price(
            {
                "style": "Sherwani",
                "fabric": "Invented Grade",
                "embroidery": "Light",
                "addons": [],
                "quantity": 1,
                "delivery_fee": 0,
            },
            cfg,
        )


def test_normalize_pricing_config_from_api_shape():
    cfg = normalize_pricing_config(
        {
            "data": {
                "styles": {"Sherwani": {"base": 50, "fabric_length": 4.5}},
                "fabric_grades": {"Standard blend": {"rate_per_metre": 10}},
                "embroidery": {"Light": 20, "None": 0},
                "markup": 0.15,
                "addons": [{"id": "stole", "price": 5, "charge_per": "outfit"}],
                "delivery": {"default": 15, "express": 30},
            }
        }
    )
    assert cfg.styles["Sherwani"].base == 50
    assert cfg.fabric_grades["Standard blend"] == 10
    assert resolve_delivery_fee(cfg) == 15
    assert resolve_delivery_fee(cfg, option="express") == 30


def test_normalize_pricing_config_uppercase_live_api_keys():
    """Live GET /pricing-config returns STYLES / FABRICS / EMBROIDERY (uppercase)."""
    cfg = normalize_pricing_config(
        {
            "delivery": 0,
            "lead_time": "3-4 weeks",
            "delivery_time": "3-5 business days",
            "STYLES": {
                "Sherwani": {"base": 50, "fabric_length": 4.5},
                "2-Piece Suit": {"base": 40, "fabric_length": 3},
            },
            "FABRICS": {
                "Standard blend": 10,
                "Silk blend": 16,
                "Jamawar / Velvet": 24,
            },
            "EMBROIDERY": {"None": 0, "Light": 20, "Medium": 40, "Heavy": 80},
        }
    )
    assert cfg.styles["Sherwani"].base == 50
    assert cfg.styles["Sherwani"].fabric_length == 4.5
    assert cfg.fabric_grades["Standard blend"] == 10
    assert cfg.embroidery["Light"] == 20
    assert resolve_delivery_fee(cfg) == 0


def test_map_style_and_fabric_grade():
    assert map_style_key(product_type="Sherwani") == "Sherwani"
    assert map_style_key(product_type="3-Piece Suit") == "3-Piece Suit"
    assert fabric_grade_from_row({"fabric_grade": "Silk blend"}) == "Silk blend"
    assert fabric_grade_from_row({"fabric_grade": ["Standard blend"]}) == "Standard blend"
    assert fabric_grade_from_row({"grade": ["Silk blend", "Jamawar / Velvet"]}) == "Silk blend"
    assert fabric_grade_from_row({"name": "No grade"}) is None


def test_resolve_fabric_grade_from_fabric_type():
    cfg = excel_reference_pricing_config()
    from app.services.price_quote_service import resolve_fabric_grade

    assert (
        resolve_fabric_grade({"fabric_type": "Jamawar Brocade", "name": "Silver Grey"}, cfg)
        == "Jamawar / Velvet"
    )
    assert resolve_fabric_grade({"fabric_grade": "Standard blend"}, cfg) == "Standard blend"
    assert resolve_fabric_grade({"fabric_grade": ["Standard blend"]}, cfg) == "Standard blend"
    # API short label must map onto config key
    assert resolve_fabric_grade({"fabric_grade": "Jamawar"}, cfg) == "Jamawar / Velvet"
    assert resolve_fabric_grade({"fabric_type": "Unknown Cloth XYZ"}, cfg) is None


def test_parse_embroidery_response():
    assert parse_embroidery_response('{"embroidery_tier":"Light","reason":"minimalist"}')[0] == "Light"
    assert parse_embroidery_response('{"embroidery_tier":"heavy","reason":"zardozi"}')[0] == "Heavy"
    assert parse_embroidery_response('{"embroidery_tier":"None"}')[0] == "None"
    assert parse_embroidery_response("not json")[0] is None


def test_detect_customize_modes():
    assert detect_customize_mode({"selected_fabric_catalog_code": "FAB-1"}) == "direct"
    assert (
        detect_customize_mode(
            {
                "selected_product_id": "P1",
                "product_details": {"product_id": "P1", "fabric_id": "FAB-1"},
                "customization_stage": "generation",
            }
        )
        == "product"
    )
    assert (
        detect_customize_mode(
            {
                "selected_product_id": "P1",
                "product_details": {"product_id": "P1", "fabric_id": "FAB-1"},
                "selected_fabric_catalog_code": "FAB-OTHER",
                "customization_stage": "generation",
            }
        )
        == "direct"
    )


def test_catalogue_quote_and_public_strip():
    cat = catalogue_quote_from_product({"product_id": "1", "price": 400, "floor_price": 350})
    assert cat and cat["list_price"] == 400
    assert cat["floor_price"] == 350
    pub = public_price_quote(
        {
            "ok": True,
            "source": "calculator",
            "list_price": 172.25,
            "unit_price": 132.25,
            "floor_price": 155,
            "total_markup_fee": 17.25,
            "currency": "GBP",
            "inputs": {"embroidery": "Light"},
        }
    )
    assert pub is not None
    assert "floor_price" not in pub
    assert "total_markup_fee" not in pub
    assert pub["list_price"] == 172.25


def test_resolve_sellable_price_catalogue_vs_calculator(monkeypatch):
    import asyncio
    from app.services import price_quote_service as pqs

    async def fake_load():
        return excel_reference_pricing_config()

    async def fake_fabric(state, *, mode):
        return {"catalog_code": "FAB-1", "fabric_grade": "Standard blend", "name": "Test"}

    async def fake_emb(state):
        return {"embroidery_tier": "Light", "source": "test"}

    monkeypatch.setattr(pqs, "_load_config", fake_load)
    monkeypatch.setattr(pqs, "_resolve_fabric_row", fake_fabric)
    monkeypatch.setattr(pqs, "infer_embroidery_tier", fake_emb)

    async def _run():
        catalogue = await pqs.resolve_sellable_price(
            {"product_details": {"product_id": "P1", "price": 500, "floor_price": 450}}
        )
        assert catalogue["source"] == "catalogue"
        assert catalogue["list_price"] == 500

        custom = await pqs.resolve_sellable_price(
            {
                "custom_image_url": "https://example.com/x.png",
                "selected_fabric_catalog_code": "FAB-1",
                "product_type": "Sherwani",
                "quantity": 1,
            }
        )
        assert custom["ok"] is True
        assert custom["source"] == "calculator"
        assert custom["customize_mode"] == "direct"
        assert custom["list_price"] == 132.25 + excel_reference_pricing_config().delivery_fee_default

    asyncio.run(_run())


def test_path_b_uses_product_fabric_grade(monkeypatch):
    import asyncio
    from app.services import price_quote_service as pqs

    async def fake_load():
        return excel_reference_pricing_config()

    seen: dict = {}

    async def fake_fabric(state, *, mode):
        seen["mode"] = mode
        return {"catalog_code": "PROD-FAB", "fabric_grade": "Silk blend"}

    async def fake_emb(state):
        return {"embroidery_tier": "None", "source": "test"}

    monkeypatch.setattr(pqs, "_load_config", fake_load)
    monkeypatch.setattr(pqs, "_resolve_fabric_row", fake_fabric)
    monkeypatch.setattr(pqs, "infer_embroidery_tier", fake_emb)

    async def _run():
        quote = await pqs.resolve_sellable_price(
            {
                "custom_image_url": "https://x",
                "selected_product_id": "P9",
                "product_details": {
                    "product_id": "P9",
                    "category": "Sherwani",
                    "fabric_id": "PROD-FAB",
                },
                "product_type": "Sherwani",
                "delivery_fee": 0,
            }
        )
        assert seen["mode"] == "product"
        assert quote["ok"] is True
        assert quote["fabric_grade"] == "Silk blend"
        # 50 + 4.5*16 + 0 = 122; *1.15 = 140.3
        assert quote["unit_price"] == 140.3

    asyncio.run(_run())
