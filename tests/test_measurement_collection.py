"""Unit checks for live size-chart parsing and the two measurement paths."""
from __future__ import annotations

from app.services.backend_api import _normalize_size_chart
from app.services.measurement_collection_service import (
    apply_standard_size,
    chart_for_category,
    detect_measurement_path,
    extract_body_values,
    extract_size_label,
    fields_from_chart,
    missing_body_fields,
    nearest_size,
    save_measurements_json,
)
from app.services.measurement_service import validate_measurements
from app.services.mock_data_service import list_mock_size_charts


LIVE_PAYLOAD = {
    "id": "8c69bd10-d537-4171-9b91-cbb0854e4f32",
    "name": "Standard Chart for Men's Suits",
    "categoryId": "fcd721bb-a109-4432-9461-1324fd43490d",
    "columns": ["Size", "Chest (in)", "Waist (in)", "Shoulder (in)", "Sleeve (in)", "Length (in)"],
    "rows": [
        {"Size": "36R", "Chest (in)": "36", "Waist (in)": "30", "Length (in)": "29.5", "Sleeve (in)": "24.5", "Shoulder (in)": "17.5"},
        {"Size": "40R", "Chest (in)": "40", "Waist (in)": "34", "Length (in)": "30.5", "Sleeve (in)": "25.5", "Shoulder (in)": "18.5"},
        {"Size": "44R", "Chest (in)": "44", "Waist (in)": "38", "Length (in)": "31.5", "Sleeve (in)": "26.5", "Shoulder (in)": "19.5"},
    ],
    "howToMeasure": [
        {"part": "Chest", "instruction": "Measure around the fullest part of your chest."},
        {"part": "Waist", "instruction": "Measure around your natural waistline."},
        {"part": "Shoulder", "instruction": "Measure across the back."},
        {"part": "Sleeve Length", "instruction": "Measure from shoulder seam to wrist."},
        {"part": "Jacket Length", "instruction": "Measure down the center back."},
    ],
    "category": {"id": "fcd721bb-a109-4432-9461-1324fd43490d", "name": "Suits", "slug": "suits-4975"},
}


def test_normalize_and_fields_from_live_payload():
    chart = _normalize_size_chart(LIVE_PAYLOAD)
    assert chart["category_name"] == "Suits"
    assert chart["id"] == LIVE_PAYLOAD["id"]
    fields = fields_from_chart(chart)
    assert fields == ["chest", "waist", "shoulder", "sleeve", "jacket_length"]
    assert "neck" not in fields
    assert "inseam" not in fields


def test_chart_for_category_suits_only():
    charts = [_normalize_size_chart(LIVE_PAYLOAD)]
    assert chart_for_category(charts, "Suits") is not None
    assert chart_for_category(charts, "suit") is not None
    assert chart_for_category(charts, "Sherwani") is None
    assert chart_for_category(charts, "Prince Coat") is None


def test_apply_standard_size_40r():
    chart = _normalize_size_chart(LIVE_PAYLOAD)
    applied = apply_standard_size(chart, "40R")
    assert applied["status"] == "ok"
    assert applied["size"] == "40R"
    assert applied["chest"] == "40"
    assert applied["waist"] == "34"
    assert applied["shoulder"] == "18.5"
    assert applied["sleeve"] == "25.5"
    assert applied["jacket_length"] == "30.5"
    assert applied["requires_tailor_review"] is False
    # "40" uniquely maps to 40R
    assert apply_standard_size(chart, "40")["size"] == "40R"


def test_nearest_size_and_off_chart():
    chart = _normalize_size_chart(LIVE_PAYLOAD)
    near = nearest_size(chart, {"chest": "40", "waist": "34"})
    assert near["nearest_size"] == "40R"
    assert near["requires_tailor_review"] is False
    off = nearest_size(chart, {"chest": "52", "waist": "48"})
    assert off["requires_tailor_review"] is True


def test_missing_body_fields():
    chart = _normalize_size_chart(LIVE_PAYLOAD)
    missing = missing_body_fields({"chest": "40", "body_measurements": {"chest": "40"}}, chart)
    assert "waist" in missing
    assert "chest" not in missing


def test_detect_paths_and_extract():
    chart = _normalize_size_chart(LIVE_PAYLOAD)
    assert detect_measurement_path("mera size 40R hai", chart) == "standard_size"
    assert detect_measurement_path("chest 40 waist 34", chart) == "body_measurements"
    assert extract_size_label("I wear 40R", chart) == "40R"
    body = extract_body_values("chest 40, waist 34, shoulder 18.5")
    assert body["chest"] == "40"
    assert body["waist"] == "34"


def test_no_chart_does_not_fake_suits_numbers():
    result = validate_measurements(selected_size="42", product_type="Sherwani")
    assert result["requires_tailor_review"] is True
    assert "chart" in " ".join(result["warnings"]).lower()
    assert result.get("nearest_size") == "42"


def test_mock_charts_match_live_shape():
    charts = list_mock_size_charts()
    assert len(charts) == 1
    assert charts[0]["category_name"] == "Suits"
    applied = apply_standard_size(charts[0], "38R")
    assert applied["chest"] == "38"


def test_collect_from_message_standard_size_is_collected():
    from app.services.measurement_collection_service import collect_from_message

    chart = _normalize_size_chart(LIVE_PAYLOAD)
    got = collect_from_message(user_message="40R", chart=chart, state={}, available_sizes=["40R"])
    assert got["status"] == "collected"
    assert got["chest"] == "40"
    pending = collect_from_message(user_message="naap lena hai", chart=chart, state={})
    assert pending["status"] in ("awaiting_path", "awaiting_body", "awaiting_size")


def test_save_measurements_json(tmp_path, monkeypatch):
    import app.services.measurement_collection_service as mcs

    monkeypatch.setattr(mcs, "MEASUREMENTS_DIR", tmp_path)
    path = save_measurements_json("sess_test", {"path": "standard_size", "size": "40R"})
    assert path.endswith("sess_test.json")
    text = (tmp_path / "sess_test.json").read_text(encoding="utf-8")
    assert "40R" in text
