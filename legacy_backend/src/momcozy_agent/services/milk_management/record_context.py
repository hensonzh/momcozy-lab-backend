from __future__ import annotations

from datetime import timedelta
from typing import Any

from .assessment import evaluate_milk_status
from .schemas import norm_text, parse_datetime, to_int

DEFAULT_RECENT_RAW_DAYS = 7
DEFAULT_RECENT_ROLLUP_DAYS = 7
DEFAULT_RECENT_RAW_LIMIT = 160
MAX_RECENT_RAW_DAYS = 14
MAX_RECENT_ROLLUP_DAYS = 14
MAX_RECENT_RAW_LIMIT = 160


def get_recent_raw_record_context(
    *,
    user_id: str,
    as_of_time: str | None = None,
    raw_days: int = DEFAULT_RECENT_RAW_DAYS,
    rollup_days: int = DEFAULT_RECENT_ROLLUP_DAYS,
    raw_limit: int = DEFAULT_RECENT_RAW_LIMIT,
    include_today: bool = False,
    purpose: str = "background_milk_analysis",
) -> dict[str, Any]:
    uid = norm_text(user_id)
    if not uid:
        return {}

    normalized_raw_days = _clamp_int(raw_days, DEFAULT_RECENT_RAW_DAYS, 1, MAX_RECENT_RAW_DAYS)
    normalized_rollup_days = _clamp_int(rollup_days, DEFAULT_RECENT_ROLLUP_DAYS, 1, MAX_RECENT_ROLLUP_DAYS)
    normalized_raw_limit = _clamp_int(raw_limit, DEFAULT_RECENT_RAW_LIMIT, 1, MAX_RECENT_RAW_LIMIT)
    window_days = max(normalized_raw_days, normalized_rollup_days)
    assessment = dict(
        evaluate_milk_status(
            user_id=uid,
            as_of_time=as_of_time,
            window_days=window_days,
            include_today=include_today,
        )
    )
    if assessment.get("ok") is not True:
        return {
            "purpose": purpose,
            "source": "evaluate_milk_status",
            "ok": False,
            "status": assessment.get("status"),
            "summary": assessment.get("summary"),
        }

    data = assessment.get("data") if isinstance(assessment.get("data"), dict) else {}
    source = data.get("source_record_context") if isinstance(data.get("source_record_context"), dict) else {}
    source_raw_records = source.get("raw_records") if isinstance(source.get("raw_records"), dict) else {}
    raw_records = _filter_and_limit_raw_records(
        source_raw_records,
        raw_days=normalized_raw_days,
        raw_limit=normalized_raw_limit,
        as_of_time=str(data.get("as_of_time") or as_of_time or ""),
        include_today=include_today,
    )
    record_counts = source.get("record_counts") if isinstance(source.get("record_counts"), dict) else {}
    return _drop_empty(
        {
            "purpose": purpose,
            "source": "evaluate_milk_status",
            "ok": True,
            "status": assessment.get("status"),
            "summary": assessment.get("summary"),
            "as_of_time": data.get("as_of_time"),
            "window": source.get("window") if isinstance(source.get("window"), dict) else data.get("window"),
            "policy": {
                "raw_days": normalized_raw_days,
                "rollup_days": normalized_rollup_days,
                "raw_limit": normalized_raw_limit,
                "include_today": bool(include_today),
            },
            "record_counts": record_counts,
            "truncated": bool(source.get("truncated")) or _raw_record_total(source_raw_records) > _raw_record_total(raw_records),
            "daily_rollups": source.get("daily_rollups") if isinstance(source.get("daily_rollups"), list) else [],
            "raw_records": raw_records,
            "computed_snapshot": _computed_snapshot_from_assessment(data, source),
            "assessment_status": data.get("assessment_status"),
            "milk_normality_status": (
                data.get("milk_normality", {}).get("overall_status")
                if isinstance(data.get("milk_normality"), dict)
                else None
            ),
        }
    )


def _filter_and_limit_raw_records(
    raw_records: dict[str, Any],
    *,
    raw_days: int,
    raw_limit: int,
    as_of_time: str,
    include_today: bool,
) -> dict[str, list[dict[str, Any]]]:
    all_records = []
    for key in ("pumping", "feeding"):
        records = raw_records.get(key) if isinstance(raw_records.get(key), list) else []
        for record in records:
            if isinstance(record, dict):
                all_records.append(record)

    start_at = _raw_start_at(as_of_time, raw_days=raw_days, include_today=include_today)
    if start_at is not None:
        filtered = []
        for record in all_records:
            occurred = parse_datetime(record.get("occurred_at"))
            if occurred is not None and occurred >= start_at:
                filtered.append(record)
        all_records = filtered

    all_records.sort(
        key=lambda item: (
            norm_text(item.get("occurred_at")),
            norm_text(item.get("record_table")),
            to_int(item.get("record_id"), 0),
        )
    )
    limited = all_records[-raw_limit:]
    return {
        "pumping": [item for item in limited if item.get("record_table") == "pumping_log"],
        "feeding": [item for item in limited if item.get("record_table") == "feeding_log"],
    }


def _computed_snapshot_from_assessment(data: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    daily_rollups = source.get("daily_rollups") if isinstance(source.get("daily_rollups"), list) else []
    valid_days = [day for day in daily_rollups if isinstance(day, dict) and day.get("ok") is True]
    positive_days = [
        day
        for day in valid_days
        if _to_float(day.get("estimated_daily_milk_ml"), 0.0) > 0
    ]
    normality = data.get("milk_normality") if isinstance(data.get("milk_normality"), dict) else {}
    return _drop_empty(
        {
            "records_status": "collected" if valid_days and positive_days else "missing",
            "valid_days": len(valid_days),
            "positive_days": len(positive_days),
            "record_counts": source.get("record_counts") if isinstance(source.get("record_counts"), dict) else {},
            "milk_normality_status": normality.get("overall_status"),
            "analysis_status": data.get("assessment_status"),
        }
    )


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _raw_start_at(as_of_time: str, *, raw_days: int, include_today: bool):
    as_of_dt = parse_datetime(as_of_time)
    if as_of_dt is None:
        return None
    end_dt = as_of_dt if include_today else as_of_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return end_dt - timedelta(days=raw_days)


def _raw_record_total(raw_records: dict[str, Any]) -> int:
    return sum(len(records) for records in raw_records.values() if isinstance(records, list))


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    return min(max(to_int(value, default), minimum), maximum)


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
