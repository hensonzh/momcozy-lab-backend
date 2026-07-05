from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / "logs" / "ag-ui-timing.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Momcozy AG-UI timing JSONL logs.")
    parser.add_argument(
        "--path",
        default=os.getenv("MOMCOZY_AG_UI_TIMING_LOG_PATH") or str(DEFAULT_LOG_PATH),
        help="Timing JSONL path. Defaults to MomCozyAgent/logs/ag-ui-timing.jsonl.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Number of newest request groups to print.")
    parser.add_argument("--id", default="", help="Filter by client_timing_id, run_id, or thread_id.")
    parser.add_argument("--gap-ms", type=float, default=500.0, help="Highlight gaps above this threshold.")
    args = parser.parse_args()

    path = Path(args.path).expanduser()
    records = _load_records(path)
    if args.id:
        records = [
            record
            for record in records
            if args.id
            in {
                str(record.get("client_timing_id") or ""),
                str(record.get("run_id") or ""),
                str(record.get("thread_id") or ""),
            }
        ]
    if not records:
        print(f"No timing records found: {path}")
        return

    groups = _group_records(records)
    newest_groups = sorted(groups.items(), key=lambda item: _group_start_ms(item[1]), reverse=True)[: max(args.limit, 1)]
    print(f"Timing log: {path}")
    print(f"Records: {len(records)} | Groups: {len(groups)} | Showing: {len(newest_groups)}")
    for key, group in newest_groups:
        _print_group(key, group, gap_ms=args.gap_ms)


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _group_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = (
            str(record.get("client_timing_id") or "").strip()
            or str(record.get("run_id") or "").strip()
            or str(record.get("thread_id") or "").strip()
            or "ungrouped"
        )
        groups[key].append(record)
    return groups


def _print_group(key: str, records: list[dict[str, Any]], *, gap_ms: float) -> None:
    ordered = sorted(records, key=_record_wall_ms)
    first_ms = _record_wall_ms(ordered[0])
    run_ids = sorted({str(item.get("run_id") or "") for item in ordered if item.get("run_id")})
    thread_ids = sorted({str(item.get("thread_id") or "") for item in ordered if item.get("thread_id")})
    print()
    print(f"== {key} ==")
    if thread_ids:
        print(f"thread_id: {', '.join(thread_ids)}")
    if run_ids:
        print(f"run_id: {', '.join(run_ids)}")

    previous_ms: float | None = None
    previous_stage = ""
    largest_gap: tuple[float, str, str] | None = None
    for record in ordered:
        current_ms = _record_wall_ms(record)
        total_delta = current_ms - first_ms
        step_gap = 0.0 if previous_ms is None else current_ms - previous_ms
        stage = str(record.get("stage") or "")
        source = str(record.get("source") or "")
        elapsed = record.get("elapsed_ms")
        metadata = _metadata_summary(record.get("metadata"))
        marker = " *gap*" if previous_ms is not None and step_gap >= gap_ms else ""
        elapsed_text = f" elapsed={float(elapsed):.1f}ms" if isinstance(elapsed, (int, float)) else ""
        print(f"+{total_delta:8.1f}ms gap={step_gap:7.1f}ms [{source}] {stage}{elapsed_text}{metadata}{marker}")
        if previous_ms is not None and (largest_gap is None or step_gap > largest_gap[0]):
            largest_gap = (step_gap, previous_stage, stage)
        previous_ms = current_ms
        previous_stage = stage

    if largest_gap is not None:
        print(f"largest_gap: {largest_gap[0]:.1f}ms ({largest_gap[1]} -> {largest_gap[2]})")


def _metadata_summary(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    parts: list[str] = []
    for key in sorted(value)[:8]:
        item = value[key]
        if isinstance(item, (str, int, float, bool)):
            text = str(item).replace("\n", " ")[:80]
            parts.append(f"{key}={text}")
    return f" {' '.join(parts)}" if parts else ""


def _group_start_ms(records: list[dict[str, Any]]) -> float:
    return min(_record_wall_ms(item) for item in records)


def _record_wall_ms(record: dict[str, Any]) -> float:
    ts = str(record.get("ts") or "").strip()
    if ts:
        try:
            normalized = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).timestamp() * 1000
        except ValueError:
            pass
    value = record.get("client_ts_ms")
    if isinstance(value, (int, float)):
        return float(value)
    value = record.get("perf_counter_ms")
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.now(timezone.utc).timestamp() * 1000


if __name__ == "__main__":
    main()
