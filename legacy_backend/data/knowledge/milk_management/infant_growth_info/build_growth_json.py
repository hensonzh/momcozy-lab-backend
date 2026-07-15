#!/usr/bin/env python3
"""
Merge infant growth Excel files into one JSON file while preserving full row data.

Default behavior:
- Input directory: current script directory
- Output file: merged_growth_reference_full.json (in input directory)

No third-party dependencies are required.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET


NS_MAIN = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"

# 宽松匹配：优先解析标准命名；若缺少部分字段则后续使用 unknown 兜底。
FILE_RE = re.compile(
    r"^tab_(?P<indicator>[a-z0-9]+)(?:_(?P<sex>boys|girls))?(?:_p_(?P<age_band>\d+_\d+))?\.xlsx$",
    re.IGNORECASE,
)


@dataclass
class TableData:
    file_name: str
    file_path: str
    indicator: str
    sex: str
    age_band: str
    sheet_name: str
    header: List[str]
    rows: List[Dict]


def col_ref_to_idx(cell_ref: str) -> int:
    m = re.match(r"([A-Z]+)", cell_ref)
    if not m:
        return 0
    col = m.group(1)
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def safe_num(raw: str):
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    if re.fullmatch(r"[+-]?\d+", s):
        try:
            return int(s)
        except ValueError:
            return s
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+|\d+)([Ee][+-]?\d+)?", s):
        try:
            f = float(s)
            if f.is_integer():
                return int(f)
            return f
        except ValueError:
            return s
    return s


def unique_headers(headers: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    result: List[str] = []
    for idx, h in enumerate(headers):
        name = str(h).strip() if h is not None else ""
        if name == "":
            name = f"COL_{idx + 1}"
        count = seen.get(name, 0) + 1
        seen[name] = count
        if count > 1:
            name = f"{name}_{count}"
        result.append(name)
    return result


def get_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    path = "xl/sharedStrings.xml"
    if path not in zf.namelist():
        return []
    root = ET.fromstring(zf.read(path))
    values: List[str] = []
    for si in root.findall("a:si", NS_MAIN):
        text = "".join((n.text or "") for n in si.findall(".//a:t", NS_MAIN))
        values.append(text)
    return values


def get_first_sheet_info(zf: zipfile.ZipFile) -> Tuple[str, str]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))

    rid_to_target: Dict[str, str] = {}
    for rel in rels.findall(f"{{{NS_REL_PKG}}}Relationship"):
        rid_to_target[rel.attrib["Id"]] = rel.attrib["Target"]

    first_sheet = wb.find("a:sheets/a:sheet", NS_MAIN)
    if first_sheet is None:
        raise ValueError("No sheet found in workbook")

    sheet_name = first_sheet.attrib.get("name", "Sheet1")
    rid = first_sheet.attrib.get(f"{{{NS_REL_OFFICE}}}id")
    if not rid or rid not in rid_to_target:
        raise ValueError("Cannot resolve first worksheet relationship")

    target = rid_to_target[rid]
    if target.startswith("/"):
        target = target[1:]
    if not target.startswith("xl/"):
        target = f"xl/{target}"
    return sheet_name, target


def parse_sheet_rows(
    zf: zipfile.ZipFile, sheet_path: str, shared_strings: List[str]
) -> List[Tuple[int, List[str]]]:
    root = ET.fromstring(zf.read(sheet_path))
    rows: List[Tuple[int, List[str]]] = []

    for row in root.findall("a:sheetData/a:row", NS_MAIN):
        row_num = int(row.attrib.get("r", "0"))
        pairs: List[Tuple[int, str]] = []
        for c in row.findall("a:c", NS_MAIN):
            ref = c.attrib.get("r", "A1")
            ctype = c.attrib.get("t")
            val = ""

            v = c.find("a:v", NS_MAIN)
            if v is not None:
                raw = v.text or ""
                if ctype == "s" and raw.isdigit():
                    i = int(raw)
                    if 0 <= i < len(shared_strings):
                        val = shared_strings[i]
                    else:
                        val = raw
                else:
                    val = raw
            else:
                is_node = c.find("a:is", NS_MAIN)
                if is_node is not None:
                    val = "".join((n.text or "") for n in is_node.findall(".//a:t", NS_MAIN))

            pairs.append((col_ref_to_idx(ref), val))

        if not pairs:
            continue
        max_idx = max(i for i, _ in pairs)
        arr = [""] * (max_idx + 1)
        for i, v in pairs:
            arr[i] = v
        rows.append((row_num, arr))

    return rows


def parse_one_xlsx(path: Path) -> TableData:
    m = FILE_RE.match(path.name)
    if m:
        indicator = (m.group("indicator") or "unknown").lower()
        sex = (m.group("sex") or "unknown").lower()
        age_band = m.group("age_band") or "unknown"
    else:
        # 文件名不满足标准模式时也不丢弃，保证“目录下全部 xlsx”都被合并。
        indicator = path.stem.lower()
        sex = "unknown"
        age_band = "unknown"

    with zipfile.ZipFile(path, "r") as zf:
        shared_strings = get_shared_strings(zf)
        sheet_name, sheet_path = get_first_sheet_info(zf)
        parsed_rows = parse_sheet_rows(zf, sheet_path, shared_strings)

    if not parsed_rows:
        raise ValueError(f"Empty worksheet: {path.name}")

    header = parsed_rows[0][1]
    header_keys = unique_headers(header)
    data_rows: List[Dict] = []

    for row_num, cells in parsed_rows[1:]:
        if not any(str(v).strip() for v in cells):
            continue
        if len(cells) < len(header_keys):
            cells = cells + [""] * (len(header_keys) - len(cells))

        raw_values = {k: cells[i] if i < len(cells) else "" for i, k in enumerate(header_keys)}
        values = {k: safe_num(v) for k, v in raw_values.items()}

        data_rows.append(
            {
                "excel_row": row_num,
                "values": values,
                "raw_values": raw_values,
            }
        )

    return TableData(
        file_name=path.name,
        file_path=str(path.resolve()),
        indicator=indicator,
        sex=sex,
        age_band=age_band,
        sheet_name=sheet_name,
        header=header_keys,
        rows=data_rows,
    )


def build_output(tables: List[TableData], input_dir: Path) -> Dict:
    all_rows: List[Dict] = []
    table_items: List[Dict] = []

    for t in tables:
        month_values = []
        for row in t.rows:
            month = row["values"].get("Month")
            if isinstance(month, (int, float)):
                month_values.append(month)
            all_rows.append(
                {
                    "source_file": t.file_name,
                    "sheet_name": t.sheet_name,
                    "indicator": t.indicator,
                    "sex": t.sex,
                    "age_band": t.age_band,
                    "excel_row": row["excel_row"],
                    "values": row["values"],
                    "raw_values": row["raw_values"],
                }
            )

        table_items.append(
            {
                "file_name": t.file_name,
                "file_path": t.file_path,
                "indicator": t.indicator,
                "sex": t.sex,
                "age_band": t.age_band,
                "sheet_name": t.sheet_name,
                "header": t.header,
                "row_count": len(t.rows),
                "month_min": min(month_values) if month_values else None,
                "month_max": max(month_values) if month_values else None,
            }
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_dir.resolve()),
        "table_count": len(table_items),
        "record_count": len(all_rows),
        "tables": table_items,
        "records": all_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge infant growth Excel files into one full JSON dataset."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory containing .xlsx files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path. Default: <input-dir>/merged_growth_reference_full.json",
    )
    args = parser.parse_args()

    input_dir: Path = args.input_dir.resolve()
    output_path: Path = (
        args.output.resolve()
        if args.output
        else (input_dir / "merged_growth_reference_full.json")
    )

    files = sorted(input_dir.glob("*.xlsx"))
    if not files:
        raise SystemExit(f"No .xlsx files found in {input_dir}")

    tables: List[TableData] = []
    for path in files:
        tables.append(parse_one_xlsx(path))

    merged = build_output(tables, input_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Merged {len(files)} files -> {output_path}")
    print(f"Tables: {merged['table_count']}, Records: {merged['record_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
