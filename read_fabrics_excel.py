#!/usr/bin/env python3
"""
Royal Atelier - Fabric Excel Reader & Data Audit Script
Reads 'fabrics.xlsx' using openpyxl (with zipfile fallback) and dumps clean JSON & CSV files.
"""

import os
import json
import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
EXCEL_FILE = BASE_DIR / "fabrics.xlsx"
OUTPUT_JSON = BASE_DIR / "fabrics_cleaned.json"
OUTPUT_CSV = BASE_DIR / "fabrics_cleaned.csv"


def read_with_openpyxl(file_path: Path):
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True)
    sheet = wb.active

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return [], []

    headers = [str(cell).strip() if cell is not None else f"Column_{i+1}" for i, cell in enumerate(rows[0])]
    data_rows = []

    for row in rows[1:]:
        if not any(row):  # Skip completely empty rows
            continue
        row_dict = {}
        for idx, val in enumerate(row):
            col_name = headers[idx] if idx < len(headers) else f"Column_{idx+1}"
            row_dict[col_name] = str(val).strip() if val is not None else ""
        data_rows.append(row_dict)

    return headers, data_rows


def read_with_zipfile_fallback(file_path: Path):
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(file_path, "r") as z:
        shared_strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for elem in tree.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
                t = elem.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
                if t is not None and t.text:
                    shared_strings.append(t.text)
                else:
                    text_parts = [
                        t_elem.text
                        for t_elem in elem.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
                        if t_elem.text
                    ]
                    shared_strings.append("".join(text_parts))

        sheet_xml = z.read("xl/worksheets/sheet1.xml")
        tree = ET.fromstring(sheet_xml)

        raw_rows = []
        for row_elem in tree.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            row_vals = []
            for cell in row_elem.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                val_type = cell.attrib.get("t")
                val_elem = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                val = val_elem.text if val_elem is not None else ""
                if val_type == "s" and val != "":
                    val = shared_strings[int(val)]
                row_vals.append(val)
            raw_rows.append(row_vals)

    if not raw_rows:
        return [], []

    headers = [str(c).strip() if c else f"Column_{i+1}" for i, c in enumerate(raw_rows[0])]
    data_rows = []

    for row in raw_rows[1:]:
        if not any(row):
            continue
        row_dict = {}
        for idx, val in enumerate(row):
            col_name = headers[idx] if idx < len(headers) else f"Column_{idx+1}"
            row_dict[col_name] = str(val).strip()
        data_rows.append(row_dict)

    return headers, data_rows


def main():
    print("================================================================================")
    print("📖 Reading 'fabrics.xlsx'...")
    print("================================================================================")

    if not EXCEL_FILE.exists():
        print(f"❌ Error: File not found at {EXCEL_FILE}")
        sys.exit(1)

    headers, data = [], []

    # Try openpyxl first
    try:
        headers, data = read_with_openpyxl(EXCEL_FILE)
        print("✅ Successfully read using 'openpyxl' library.")
    except ImportError:
        print("⚠️ 'openpyxl' not installed. Falling back to built-in zipfile parser...")
        try:
            headers, data = read_with_zipfile_fallback(EXCEL_FILE)
            print("✅ Successfully read using built-in zipfile parser.")
        except Exception as e:
            print(f"❌ Error reading file: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"⚠️ openpyxl error ({e}). Trying fallback...")
        headers, data = read_with_zipfile_fallback(EXCEL_FILE)

    print(f"\n📊 Summary:")
    print(f"   - Total Columns: {len(headers)}")
    print(f"   - Total Rows: {len(data)}")
    print(f"   - Headers: {headers}\n")

    print("================================================================================")
    print("🔍 Sample Data Preview (First 2 Rows):")
    print("================================================================================")
    for idx, row in enumerate(data[:2]):
        print(f"\n--- Row {idx+1} ---")
        for col in headers:
            print(f"  • {col}: {row.get(col, '')[:120]}")

    # Export to JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Saved clean JSON file to: {OUTPUT_JSON}")

    # Export to CSV
    if headers and data:
        with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(data)
        print(f"💾 Saved clean CSV file to:  {OUTPUT_CSV}")

    print("\n✅ Execution complete! You can now view 'fabrics_cleaned.csv' or 'fabrics_cleaned.json'.")


if __name__ == "__main__":
    main()
