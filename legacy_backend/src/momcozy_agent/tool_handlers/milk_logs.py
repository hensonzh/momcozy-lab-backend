from __future__ import annotations

from typing import Any

from ..types import RuntimeInputs


def query_records(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    return {
        "tool_name": str(args.get("_tool_name", "")),
        "records": inputs.get("retrieved_records", []),
        "status": "read_from_runtime_inputs",
    }


def calculate_trend(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    return {
        "tool_name": "trend_calculate",
        "metric": args.get("metric", ""),
        "window_days": args.get("window_days"),
        "summary": _summarize_trend(args.get("records", []), str(args.get("metric", ""))),
        "status": "calculated",
    }


def _summarize_trend(records: Any, metric: str) -> dict[str, Any]:
    if not isinstance(records, list):
        return {"count": 0, "metric": metric, "note": "records was not a list"}

    numeric_values = [record.get(metric) for record in records if isinstance(record, dict) and isinstance(record.get(metric), (int, float))]
    if not numeric_values:
        return {"count": len(records), "metric": metric, "note": "no numeric values found"}

    return {
        "count": len(numeric_values),
        "metric": metric,
        "min": min(numeric_values),
        "max": max(numeric_values),
        "average": sum(numeric_values) / len(numeric_values),
    }
