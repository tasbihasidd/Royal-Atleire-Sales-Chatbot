from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Map backend chart column labels → state keys (do not invent extra body parts).
COLUMN_TO_STATE_KEY: dict[str, str] = {
    "size": "size",
    "chest": "chest",
    "waist": "waist",
    "shoulder": "shoulder",
    "sleeve": "sleeve",
    "sleeve length": "sleeve",
    "length": "jacket_length",
    "jacket length": "jacket_length",
}

BODY_FIELD_ORDER = ("chest", "waist", "shoulder", "sleeve", "jacket_length")

FIELD_LABELS = {
    "chest": {"en": "Chest", "ur": "Seena (Chest)"},
    "waist": {"en": "Waist", "ur": "Kamar (Waist)"},
    "shoulder": {"en": "Shoulder", "ur": "Kandha (Shoulder)"},
    "sleeve": {"en": "Sleeve Length", "ur": "Bazu (Sleeve)"},
    "jacket_length": {"en": "Jacket Length", "ur": "Coat Lambai (Length)"},
    "size": {"en": "Size", "ur": "Size"},
}

MEASUREMENTS_DIR = Path(__file__).resolve().parents[2] / "data" / "measurements"


def _fold(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())


def _column_key(column: str) -> str | None:
    raw = (column or "").strip().lower()
    raw = re.sub(r"\s*\(in\)\s*", " ", raw).strip()
    return COLUMN_TO_STATE_KEY.get(raw) or COLUMN_TO_STATE_KEY.get(raw.replace("  ", " "))


def chart_for_category(
    charts: list[dict[str, Any]],
    product_type: str | None,
    category_id: str | None = None,
) -> dict[str, Any] | None:
    """Match a live size chart by category name / id. Never invent a chart."""
    if not charts:
        return None
    want = _fold(product_type)
    if category_id:
        for chart in charts:
            if str(chart.get("category_id") or "") == str(category_id):
                return chart
    if not want:
        return None
    for chart in charts:
        cat_name = (chart.get("category_name") or "")
        if isinstance(chart.get("category"), dict):
            cat_name = chart["category"].get("name") or cat_name
        folded = _fold(cat_name)
        if not folded:
            continue
        if want == folded or want in folded or folded in want:
            return chart
        # Soft aliases only when chart category is Suits and customer said suit/tuxedo
        if folded == "suits" and any(x in want for x in ("suit", "tuxedo", "blazer")):
            return chart
    return None


def fields_from_chart(chart: dict[str, Any] | None) -> list[str]:
    if not chart:
        return []
    keys: list[str] = []
    for col in chart.get("columns") or []:
        key = _column_key(str(col))
        if key and key != "size" and key not in keys:
            keys.append(key)
    # Prefer stable order matching howToMeasure
    ordered = [k for k in BODY_FIELD_ORDER if k in keys]
    for k in keys:
        if k not in ordered:
            ordered.append(k)
    return ordered


def size_labels_from_chart(chart: dict[str, Any] | None) -> list[str]:
    if not chart:
        return []
    labels: list[str] = []
    for row in chart.get("rows") or []:
        if not isinstance(row, dict):
            continue
        label = row.get("Size") or row.get("size")
        if label is not None and str(label).strip():
            labels.append(str(label).strip())
    return labels


def _row_values(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for col, val in row.items():
        key = _column_key(str(col))
        if key and val is not None and str(val).strip():
            out[key] = str(val).strip()
    return out


def find_chart_row(chart: dict[str, Any] | None, size_label: str | None) -> dict[str, Any] | None:
    if not chart or not size_label:
        return None
    want = _fold(size_label)
    for row in chart.get("rows") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("Size") or row.get("size") or "")
        if _fold(label) == want or want in _fold(label) or _fold(label) in want:
            return row
    return None


def apply_standard_size(
    chart: dict[str, Any] | None,
    size_label: str,
) -> dict[str, Any]:
    """Fill state fields from a chart row (standard-size path)."""
    row = find_chart_row(chart, size_label)
    if not row:
        return {
            "status": "unknown",
            "measurement_path": "standard_size",
            "size": size_label,
            "requires_tailor_review": True,
            "warnings": [f"Size {size_label} is not on the live size chart."],
            "body_measurements": {"size": size_label},
        }
    values = _row_values(row)
    size = values.get("size") or size_label
    body = {k: v for k, v in values.items() if k != "size"}
    body["size"] = size
    return {
        "status": "ok",
        "measurement_path": "standard_size",
        "size": size,
        "chest": values.get("chest"),
        "waist": values.get("waist"),
        "shoulder": values.get("shoulder"),
        "sleeve": values.get("sleeve"),
        "jacket_length": values.get("jacket_length"),
        "body_measurements": body,
        "requires_tailor_review": False,
        "warnings": [],
        "size_chart": {
            "id": chart.get("id") if chart else None,
            "name": chart.get("name") if chart else None,
            "category_name": (chart or {}).get("category_name"),
        },
    }


def parse_inches(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def nearest_size(
    chart: dict[str, Any] | None,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Nearest chart row by chest (primary) then waist. Off-chart → tailor review."""
    if not chart:
        return {
            "status": "unknown",
            "requires_tailor_review": True,
            "nearest_size": None,
            "warnings": ["No size chart available for this category."],
        }
    chest = parse_inches(body.get("chest"))
    waist = parse_inches(body.get("waist"))
    best_row = None
    best_score = None
    for row in chart.get("rows") or []:
        if not isinstance(row, dict):
            continue
        vals = _row_values(row)
        row_chest = parse_inches(vals.get("chest"))
        row_waist = parse_inches(vals.get("waist"))
        if row_chest is None:
            continue
        score = 0.0
        if chest is not None:
            score += abs(row_chest - chest) * 2.0
        if waist is not None and row_waist is not None:
            score += abs(row_waist - waist)
        if best_score is None or score < best_score:
            best_score = score
            best_row = row

    if not best_row:
        return {
            "status": "unknown",
            "requires_tailor_review": True,
            "nearest_size": None,
            "warnings": ["Could not match body measurements to a chart size."],
        }

    vals = _row_values(best_row)
    nearest = vals.get("size")
    warnings: list[str] = []
    off_chart = False
    if chest is not None:
        row_chests = [
            parse_inches(_row_values(r).get("chest"))
            for r in (chart.get("rows") or [])
            if isinstance(r, dict)
        ]
        nums = [c for c in row_chests if c is not None]
        if nums and (chest < min(nums) - 1 or chest > max(nums) + 1):
            off_chart = True
            warnings.append(f"Chest {chest} is outside chart range {min(nums)}–{max(nums)} in.")

    return {
        "status": "mismatch" if (warnings or (best_score or 0) > 3) else "ok",
        "requires_tailor_review": off_chart or (best_score or 0) > 3,
        "nearest_size": nearest,
        "matched_row": vals,
        "warnings": warnings,
        "score": best_score,
    }


def how_to_measure_for(chart: dict[str, Any] | None, field_key: str) -> str | None:
    if not chart:
        return None
    target = FIELD_LABELS.get(field_key, {}).get("en", field_key).lower()
    for item in chart.get("how_to_measure") or []:
        if not isinstance(item, dict):
            continue
        part = str(item.get("part") or "").strip().lower()
        if not part:
            continue
        if part in target or target in part or _fold(part) == _fold(target):
            return str(item.get("instruction") or "").strip() or None
        if field_key == "sleeve" and "sleeve" in part:
            return str(item.get("instruction") or "").strip() or None
        if field_key == "jacket_length" and ("jacket" in part or part == "length"):
            return str(item.get("instruction") or "").strip() or None
    return None


def missing_body_fields(state: dict[str, Any], chart: dict[str, Any] | None) -> list[str]:
    keys = fields_from_chart(chart)
    body = state.get("body_measurements") if isinstance(state.get("body_measurements"), dict) else {}
    missing: list[str] = []
    for key in keys:
        val = state.get(key) or body.get(key)
        if val is None or not str(val).strip():
            missing.append(key)
    return missing


def detect_measurement_path(user_message: str, chart: dict[str, Any] | None) -> str | None:
    text = (user_message or "").lower()
    if any(
        w in text
        for w in (
            "standard size",
            "ready size",
            "size chart",
            "size lo",
            "size de",
            "40r",
            "42r",
            "36r",
            "38r",
            "44r",
            "size 40",
            "size 42",
            "mera size",
        )
    ):
        # If they also dump body numbers, prefer body path when digits look like measurements
        if re.search(r"chest|seena|waist|kamar|shoulder|kandha", text):
            return "body_measurements"
        return "standard_size"
    if any(
        w in text
        for w in (
            "measure",
            "measurement",
            "naap",
            "nap",
            "body",
            "chest",
            "seena",
            "waist",
            "kamar",
            "shoulder",
            "kandha",
            "sleeve",
            "bazu",
        )
    ):
        return "body_measurements"
    # Explicit size label like 40R / 42
    labels = size_labels_from_chart(chart)
    for label in labels:
        if label.lower() in text or _fold(label) in _fold(text):
            return "standard_size"
    m = re.search(r"\b(3[6-9]|4[0-6])\s*r?\b", text)
    if m and ("size" in text or "size" in text.replace(" ", "")):
        return "standard_size"
    return None


def extract_size_label(user_message: str, chart: dict[str, Any] | None) -> str | None:
    text = user_message or ""
    for label in size_labels_from_chart(chart):
        if re.search(rf"\b{re.escape(label)}\b", text, re.IGNORECASE):
            return label
    m = re.search(r"\b((?:3[6-9]|4[0-6])R?)\b", text, re.IGNORECASE)
    if m:
        raw = m.group(1).upper()
        if not raw.endswith("R") and chart:
            candidate = f"{raw}R"
            if find_chart_row(chart, candidate):
                return candidate
        return raw
    return None


def extract_body_values(user_message: str) -> dict[str, str]:
    """Parse inches from free text for chart columns only."""
    text = user_message or ""
    out: dict[str, str] = {}
    patterns = {
        "chest": r"(?:chest|seena)\s*[:=]?\s*(\d+(?:\.\d+)?)",
        "waist": r"(?:waist|kamar)\s*[:=]?\s*(\d+(?:\.\d+)?)",
        "shoulder": r"(?:shoulder|kandha)\s*[:=]?\s*(\d+(?:\.\d+)?)",
        "sleeve": r"(?:sleeve|bazu|sleeve\s*length)\s*[:=]?\s*(\d+(?:\.\d+)?)",
        "jacket_length": r"(?:jacket\s*length|length|lambai|coat\s*length)\s*[:=]?\s*(\d+(?:\.\d+)?)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            out[key] = m.group(1)
    # Bare list of numbers when asking for all at once: "40, 34, 18.5, 25.5, 30.5"
    if not out:
        nums = re.findall(r"(\d+(?:\.\d+)?)", text)
        if len(nums) >= 2:
            for key, num in zip(BODY_FIELD_ORDER, nums):
                out[key] = num
    return out


def build_measurement_prompt(
    *,
    chart: dict[str, Any] | None,
    available_sizes: list[str] | None = None,
    measurement_path: str | None = None,
    missing: list[str] | None = None,
    roman_urdu: bool = False,
) -> str:
    sizes = size_labels_from_chart(chart) or list(available_sizes or [])
    size_bit = ", ".join(sizes) if sizes else "standard ready sizes on the product"

    if measurement_path is None:
        if roman_urdu:
            return (
                f"Janab, sizing ke liye do tareeqe hain: (1) standard size choose karein "
                f"({size_bit}), ya (2) body measurements inches mein dein "
                f"(chest, waist, shoulder, sleeve, jacket length). "
                "Kaunsa tareeqa prefer karenge?"
            )
        return (
            f"For sizing we can either (1) pick a standard size ({size_bit}), or "
            "(2) take your body measurements in inches (chest, waist, shoulder, sleeve, jacket length). "
            "Which would you prefer?"
        )

    if measurement_path == "standard_size":
        if roman_urdu:
            return f"Bilkul — apna standard size batayein ({size_bit})."
        return f"Please share your standard size ({size_bit})."

    missing = missing or fields_from_chart(chart) or list(BODY_FIELD_ORDER)
    next_field = missing[0]
    label = FIELD_LABELS.get(next_field, {}).get("ur" if roman_urdu else "en", next_field)
    tip = how_to_measure_for(chart, next_field)
    if roman_urdu:
        msg = f"Barah-e-karam apna {label} inches mein batayein."
    else:
        msg = f"Please share your {label} in inches."
    if tip:
        msg += f" Tip: {tip}"
    if len(missing) > 1:
        rest = ", ".join(
            FIELD_LABELS.get(k, {}).get("en", k) for k in missing[1:]
        )
        if roman_urdu:
            msg += f" Baqi fields baad mein: {rest}."
        else:
            msg += f" Still needed after this: {rest}."
    return msg


def save_measurements_json(session_id: str, payload: dict[str, Any]) -> str:
    MEASUREMENTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id or "unknown")
    path = MEASUREMENTS_DIR / f"{safe_id}.json"
    data = {"session_id": session_id, **payload}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("save_measurements_json path=%s", path)
    return str(path)


def handover_measurements_payload(state: dict[str, Any]) -> dict[str, Any]:
    chart = state.get("size_chart") if isinstance(state.get("size_chart"), dict) else {}
    return {
        "path": state.get("measurement_path"),
        "size": state.get("size"),
        "size_chart_id": chart.get("id"),
        "chest": state.get("chest"),
        "waist": state.get("waist"),
        "shoulder": state.get("shoulder"),
        "sleeve": state.get("sleeve"),
        "jacket_length": state.get("jacket_length"),
        "body_measurements": state.get("body_measurements"),
        "measurement_result": state.get("measurement_result"),
    }


# --- Aliases expected by planner / collect_measurements_node ---

def infer_measurement_path(user_message: str, chart: dict[str, Any] | None) -> str | None:
    return detect_measurement_path(user_message, chart)


def build_collection_prompt(
    *,
    chart: dict[str, Any] | None,
    available_sizes: list[str] | None = None,
    missing: list[str] | None = None,
    path: str | None = None,
    roman_urdu: bool = False,
) -> str:
    return build_measurement_prompt(
        chart=chart,
        available_sizes=available_sizes,
        measurement_path=path,
        missing=missing,
        roman_urdu=roman_urdu,
    )


def product_available_sizes(state: dict[str, Any]) -> list[str]:
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    sizes = list(details.get("available_sizes") or [])
    if sizes:
        return [str(s) for s in sizes if str(s).strip()]
    for product in state.get("products") or []:
        if not isinstance(product, dict):
            continue
        if str(product.get("product_id") or "") == str(state.get("selected_product_id") or ""):
            return [str(s) for s in (product.get("available_sizes") or []) if str(s).strip()]
    return []


def measurements_complete(state: dict[str, Any]) -> bool:
    result = state.get("measurement_result") if isinstance(state.get("measurement_result"), dict) else {}
    if result.get("status") in ("ok", "collected", "mismatch") and (
        state.get("size") or state.get("body_measurements")
    ):
        # mismatch still counts as collected (with tailor review flag)
        if result.get("status") == "awaiting_path":
            return False
        if result.get("status") in ("awaiting_size", "awaiting_body", "pending"):
            return False
        return True
    if state.get("measurement_path") == "standard_size" and state.get("size"):
        return True
    body = state.get("body_measurements") if isinstance(state.get("body_measurements"), dict) else {}
    chart = state.get("size_chart") if isinstance(state.get("size_chart"), dict) else None
    if chart and body and not missing_body_fields(state, chart):
        return True
    return False


def collect_from_message(
    *,
    user_message: str,
    chart: dict[str, Any] | None,
    state: dict[str, Any],
    available_sizes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Parse one user turn into a measurement collection update.
    status: awaiting_path | awaiting_size | awaiting_body | collected
    """
    path = state.get("measurement_path") or detect_measurement_path(user_message, chart)
    available_sizes = list(available_sizes or [])

    if not path:
        return {
            "status": "awaiting_path",
            "measurement_path": None,
            "missing_fields": fields_from_chart(chart),
            "requires_tailor_review": False,
            "warnings": [],
        }

    if path == "standard_size":
        size_label = extract_size_label(user_message, chart) or state.get("size")
        if not size_label:
            # Try matching available product sizes
            text = (user_message or "").lower()
            for s in available_sizes:
                if str(s).lower() in text:
                    size_label = str(s)
                    break
        if not size_label:
            return {
                "status": "awaiting_size",
                "measurement_path": "standard_size",
                "missing_fields": ["size"],
                "requires_tailor_review": not bool(chart),
                "warnings": [],
            }
        if chart:
            applied = apply_standard_size(chart, size_label)
            return {
                **applied,
                "status": "collected",
            }
        return {
            "status": "collected",
            "measurement_path": "standard_size",
            "size": size_label,
            "body_measurements": {"size": size_label},
            "requires_tailor_review": True,
            "warnings": [
                "No live size chart for this category — Style Consultant will confirm remaining measurements."
            ],
        }

    # body_measurements
    body = dict(state.get("body_measurements") or {})
    for key in ("chest", "waist", "shoulder", "sleeve", "jacket_length"):
        if state.get(key) and key not in body:
            body[key] = state.get(key)
    body.update(extract_body_values(user_message))
    missing = missing_body_fields({**state, "body_measurements": body}, chart) if chart else []
    if chart and missing:
        return {
            "status": "awaiting_body",
            "measurement_path": "body_measurements",
            "body_measurements": body,
            "missing_fields": missing,
            "chest": body.get("chest"),
            "waist": body.get("waist"),
            "shoulder": body.get("shoulder"),
            "sleeve": body.get("sleeve"),
            "jacket_length": body.get("jacket_length"),
            "requires_tailor_review": False,
            "warnings": [],
        }
    if not chart and not body and not state.get("size"):
        return {
            "status": "awaiting_size",
            "measurement_path": "body_measurements",
            "missing_fields": ["size"],
            "requires_tailor_review": True,
            "warnings": ["No live size chart for this category."],
        }
    match = nearest_size(chart, body) if chart else {
        "status": "unknown",
        "requires_tailor_review": True,
        "nearest_size": state.get("size"),
        "warnings": ["No live size chart — consultant review required."],
    }
    return {
        "status": "collected",
        "measurement_path": "body_measurements",
        "size": match.get("nearest_size") or state.get("size"),
        "body_measurements": body,
        "chest": body.get("chest"),
        "waist": body.get("waist"),
        "shoulder": body.get("shoulder"),
        "sleeve": body.get("sleeve"),
        "jacket_length": body.get("jacket_length"),
        "requires_tailor_review": bool(match.get("requires_tailor_review")),
        "warnings": list(match.get("warnings") or []),
        "nearest_size": match.get("nearest_size"),
    }


def product_available_sizes(state: dict[str, Any] | None) -> list[str]:
    state = state or {}
    details = state.get("product_details") if isinstance(state.get("product_details"), dict) else {}
    sizes = list(details.get("available_sizes") or [])
    if sizes:
        return [str(s) for s in sizes if str(s).strip()]
    selected = state.get("selected_product_id")
    for product in state.get("products") or []:
        if selected and str(product.get("product_id")) != str(selected):
            continue
        raw = product.get("available_sizes") or []
        if raw:
            return [str(s) for s in raw if str(s).strip()]
    return []


def measurements_complete(state: dict[str, Any] | None) -> bool:
    state = state or {}
    path = state.get("measurement_path")
    result = state.get("measurement_result") if isinstance(state.get("measurement_result"), dict) else {}
    if result.get("status") in ("awaiting_path", "awaiting_size", "awaiting_body", "pending"):
        return False
    if result.get("status") in ("ok", "collected", "mismatch") and (
        state.get("size") or state.get("body_measurements")
    ):
        return True
    if path == "standard_size" and state.get("size"):
        return True
    if path == "body_measurements":
        chart = state.get("size_chart") if isinstance(state.get("size_chart"), dict) else None
        if chart:
            return not missing_body_fields(state, chart)
        return bool(state.get("size") or state.get("body_measurements"))
    return False


# Thin aliases — do NOT rebind build_collection_prompt to build_measurement_prompt
# (kwargs differ: path vs measurement_path).
extract_standard_size_label = extract_size_label
extract_body_from_message = extract_body_values
chart_size_labels = size_labels_from_chart
