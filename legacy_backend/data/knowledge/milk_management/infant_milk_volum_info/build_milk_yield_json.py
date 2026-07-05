#!/usr/bin/env python3
"""
Convert milk_yield_percentiles_0_360.csv to JSON.

Default:
  input : milk_yield_percentiles_0_360.csv
  output: milk_yield_percentiles_0_360.json
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _to_number(value: str) -> Any:
    token = str(value or "").strip()
    if token == "":
        return None
    if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
        try:
            return int(token)
        except ValueError:
            return token
    try:
        return float(token)
    except ValueError:
        return token


def _normalize_key(raw_key: str) -> str:
    key = str(raw_key or "").strip().lower().replace(" ", "_")
    if key == "days_postpartum":
        return "day_postpartum"
    return key


def convert_csv_to_json(input_csv: Path, output_json: Path) -> Path:
    if not input_csv.exists():
        raise FileNotFoundError(f"CSV not found: {input_csv}")

    records: List[Dict[str, Any]] = []
    with input_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item: Dict[str, Any] = {}
            for k, v in row.items():
                nk = _normalize_key(k)
                item[nk] = _to_number(v)
            records.append(item)

    payload = {
        "source_file": input_csv.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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
    parser = argparse.ArgumentParser(description="Convert milk yield percentile CSV to JSON.")
    parser.add_argument(
        "--input",
        type=Path,
        default=base_dir / "milk_yield_percentiles_0_360.csv",
        help="Input CSV path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=base_dir / "milk_yield_percentiles_0_360.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    out = convert_csv_to_json(args.input, args.output)
    print(f"ok: {out}")


if __name__ == "__main__":
    main()
