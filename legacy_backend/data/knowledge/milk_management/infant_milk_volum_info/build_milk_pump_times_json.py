#!/usr/bin/env python3
"""
Convert milk_pump_normal_times.csv to JSON.

Default:
  input : milk_pump_normal_times.csv
  output: milk_pump_normal_times.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _read_text_with_fallback(path: Path) -> Tuple[str, str]:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin1", errors="replace"), "latin1-replace"


def _to_number(value: str) -> Any:
    token = str(value or "").strip()
    if token == "":
        return None
    if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
        return int(token)
    try:
        return float(token)
    except ValueError:
        return token


def _extract_month_index(label: str) -> Any:
    m = re.search(r"(\d+)", str(label or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def convert_csv_to_json(input_csv: Path, output_json: Path) -> Path:
    if not input_csv.exists():
        raise FileNotFoundError(f"CSV not found: {input_csv}")

    text, encoding_used = _read_text_with_fallback(input_csv)
    reader = csv.reader(text.splitlines())
    rows = list(reader)
    if len(rows) < 2:
        raise ValueError(f"CSV has no data rows: {input_csv}")

    header = rows[0]
    data_rows = rows[1:]

    records: List[Dict[str, Any]] = []
    for row in data_rows:
        if not row or all(str(cell or "").strip() == "" for cell in row):
            continue
        padded = list(row) + ["", "", "", ""]
        month_label = str(padded[0] or "").strip()
        item = {
            "month_label": month_label,
            "month_index": _extract_month_index(month_label),
            "p25": _to_number(padded[1]),
            "p50": _to_number(padded[2]),
            "p75": _to_number(padded[3]),
        }
        records.append(item)

    payload = {
        "source_file": input_csv.name,
        "encoding_used": encoding_used,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "raw_header": header,
        "record_count": len(records),
        "records": records,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    with output_json.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return output_json


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Convert milk pump normal times CSV to JSON.")
    parser.add_argument(
        "--input",
        type=Path,
        default=base_dir / "milk_pump_normal_times.csv",
        help="Input CSV path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=base_dir / "milk_pump_normal_times.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    out = convert_csv_to_json(args.input, args.output)
    print(f"ok: {out}")


if __name__ == "__main__":
    main()
