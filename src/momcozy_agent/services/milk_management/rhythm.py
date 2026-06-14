from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .db import fetch_all
from .schemas import (
    CALENDAR_TYPE_NURSING,
    CALENDAR_TYPE_PUMP,
    norm_text,
    parse_datetime,
    to_bool,
    to_int,
)


def build_recent_milk_rhythm_context(
    *,
    user_id: str,
    as_of_time: str | None = None,
    days: int = 7,
    include_today: bool = False,
) -> dict[str, Any]:
    """Read the recent milk schedule and actual records used by assessment and planning."""

    uid = norm_text(user_id)
    as_of_dt = parse_datetime(as_of_time) if as_of_time else datetime.now()
    if not uid or as_of_dt is None:
        return {}

    window_days = max(to_int(days, 7), 1)
    end_day = as_of_dt.date() + timedelta(days=1) if include_today else as_of_dt.date()
    start_day = end_day - timedelta(days=window_days)
    start_dt = datetime.combine(start_day, datetime.min.time())
    end_dt = datetime.combine(end_day, datetime.min.time())

    pumping_rows = fetch_all(
        """
        SELECT pumping_id, pump_start_time, pump_end_time, pump_milk_volum,
               pump_type, pump_milk_duration
        FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time >= ?
          AND pump_start_time < ?
        ORDER BY pump_start_time ASC
        """,
        (uid, _db_time(start_dt), _db_time(end_dt)),
    )
    feeding_rows = fetch_all(
        """
        SELECT feeding_id, infant_id, feed_time, feed_milk_volum, feed_type
        FROM feeding_log
        WHERE user_id = ?
          AND feed_time >= ?
          AND feed_time < ?
        ORDER BY feed_time ASC
        """,
        (uid, _db_time(start_dt), _db_time(end_dt)),
    )
    calendar_rows = fetch_all(
        """
        SELECT item_id, date, task_id, start_time, end_time, content, type, finish, is_milk_pump
        FROM calendar
        WHERE user_id = ?
          AND date >= ?
          AND date < ?
          AND type IN (?, ?)
        ORDER BY date ASC, start_time ASC, task_id ASC
        """,
        (uid, start_day.isoformat(), end_day.isoformat(), CALENDAR_TYPE_PUMP, CALENDAR_TYPE_NURSING),
    )

    slots = {
        (start_day + timedelta(days=offset)).isoformat(): _empty_day(start_day + timedelta(days=offset))
        for offset in range(window_days)
    }
    for row in calendar_rows:
        day = norm_text(row.get("date")) or _date_from_value(row.get("start_time"))
        slot = slots.get(day)
        if slot is None:
            continue
        event = {
            "time": _hhmm_from_value(row.get("start_time")),
            "end_time": _hhmm_from_value(row.get("end_time")),
            "completed": _finished(row.get("finish")),
            "title": norm_text(row.get("content")),
        }
        if not event["time"]:
            continue
        item_type = norm_text(row.get("type"))
        if item_type == CALENDAR_TYPE_NURSING:
            slot["planned_nursing"].append(event)
        elif item_type == CALENDAR_TYPE_PUMP or to_bool(row.get("is_milk_pump")):
            slot["planned_pumping"].append(event)

    for row in pumping_rows:
        if to_int(row.get("pump_type"), 0) == 2:
            continue
        day = _date_from_value(row.get("pump_start_time"))
        slot = slots.get(day)
        if slot is None:
            continue
        event = {
            "time": _hhmm_from_value(row.get("pump_start_time")),
            "end_time": _hhmm_from_value(row.get("pump_end_time")),
            "milk_ml": round(_float(row.get("pump_milk_volum")), 1),
            "duration_minutes": to_int(row.get("pump_milk_duration"), 0),
        }
        if event["time"]:
            slot["actual_pumping"].append(event)

    for row in feeding_rows:
        day = _date_from_value(row.get("feed_time"))
        slot = slots.get(day)
        if slot is None:
            continue
        feed_type = norm_text(row.get("feed_type"))
        event = {
            "time": _hhmm_from_value(row.get("feed_time")),
            "milk_ml": round(_float(row.get("feed_milk_volum")), 1),
            "feed_type": feed_type,
        }
        if not event["time"]:
            continue
        if _is_direct_nursing(feed_type):
            slot["actual_nursing"].append(event)
        elif _is_formula(feed_type):
            slot["formula_bottle"].append(event)
        else:
            slot["breastmilk_bottle"].append(event)

    day_items = [_finalize_day(slot) for slot in slots.values()]
    selected_day = _select_basis_day(day_items)
    summary = _summarize_rhythm(day_items, selected_day=selected_day)
    return {
        "window": {
            "start_date": start_day.isoformat(),
            "end_date_exclusive": end_day.isoformat(),
            "days": window_days,
            "include_today": bool(include_today),
        },
        "days": day_items,
        "selected_day": selected_day,
        "summary": summary,
    }


def _empty_day(day: Any) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "planned_pumping": [],
        "planned_nursing": [],
        "actual_pumping": [],
        "actual_nursing": [],
        "breastmilk_bottle": [],
        "formula_bottle": [],
    }


def _finalize_day(slot: dict[str, Any]) -> dict[str, Any]:
    planned_pumping = _sort_events(slot.get("planned_pumping"))
    planned_nursing = _sort_events(slot.get("planned_nursing"))
    actual_pumping = _sort_events(slot.get("actual_pumping"))
    actual_nursing = _sort_events(slot.get("actual_nursing"))
    breastmilk_bottle = _sort_events(slot.get("breastmilk_bottle"))
    formula_bottle = _sort_events(slot.get("formula_bottle"))
    all_times = _unique_sorted_times(
        [
            *_event_times(planned_pumping),
            *_event_times(planned_nursing),
            *_event_times(actual_pumping),
            *_event_times(actual_nursing),
            *_event_times(breastmilk_bottle),
            *_event_times(formula_bottle),
        ]
    )
    planned_count = len(planned_pumping) + len(planned_nursing)
    actual_count = len(actual_pumping) + len(actual_nursing) + len(breastmilk_bottle) + len(formula_bottle)
    completed_planned_count = len([item for item in [*planned_pumping, *planned_nursing] if item.get("completed")])
    completeness = "empty"
    if planned_count and completed_planned_count >= max(planned_count * 0.6, 1):
        completeness = "schedule_completed"
    elif actual_count >= 3:
        completeness = "actual_records_available"
    elif planned_count or actual_count:
        completeness = "partial"
    return {
        "date": slot.get("date"),
        "planned_pumping": planned_pumping,
        "planned_nursing": planned_nursing,
        "actual_pumping": actual_pumping,
        "actual_nursing": actual_nursing,
        "breastmilk_bottle": breastmilk_bottle,
        "formula_bottle": formula_bottle,
        "planned_pumping_times": _event_times(planned_pumping),
        "planned_nursing_times": _event_times(planned_nursing),
        "actual_pumping_times": _event_times(actual_pumping),
        "actual_nursing_times": _event_times(actual_nursing),
        "breastmilk_bottle_times": _event_times(breastmilk_bottle),
        "formula_bottle_times": _event_times(formula_bottle),
        "all_times": all_times,
        "planned_count": planned_count,
        "actual_count": actual_count,
        "completed_planned_count": completed_planned_count,
        "completeness": completeness,
    }


def _select_basis_day(days: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [day for day in days if day.get("all_times")]
    if not candidates:
        return {}
    schedule_candidates = [day for day in candidates if day.get("planned_pumping_times") or day.get("planned_nursing_times")]
    pool = schedule_candidates or candidates
    return max(pool, key=lambda day: (_basis_score(day), str(day.get("date") or "")))


def _basis_score(day: dict[str, Any]) -> int:
    return (
        len(day.get("planned_pumping_times") or []) * 5
        + len(day.get("planned_nursing_times") or []) * 4
        + len(day.get("actual_pumping_times") or []) * 3
        + len(day.get("actual_nursing_times") or []) * 2
        + len(day.get("breastmilk_bottle_times") or [])
        + len(day.get("formula_bottle_times") or [])
        + to_int(day.get("completed_planned_count"), 0) * 2
    )


def _summarize_rhythm(days: list[dict[str, Any]], *, selected_day: dict[str, Any]) -> dict[str, Any]:
    non_empty_days = [day for day in days if day.get("all_times")]
    enough_days = [day for day in days if day.get("completeness") in {"schedule_completed", "actual_records_available"}]
    selected_pumping_times = (selected_day.get("planned_pumping_times") or selected_day.get("actual_pumping_times") or [])
    selected_nursing_times = (selected_day.get("planned_nursing_times") or selected_day.get("actual_nursing_times") or [])
    selected_all_times = _unique_sorted_times([*selected_pumping_times, *selected_nursing_times])
    confidence = "low"
    if len(enough_days) >= 3:
        confidence = "high"
    elif non_empty_days:
        confidence = "medium"
    ask_daily_counts = confidence == "low"
    return {
        "basis_date": selected_day.get("date") or "",
        "confidence": confidence,
        "ask_daily_counts": ask_daily_counts,
        "ask_missing_records": confidence != "high",
        "usable_for_schedule": confidence != "low",
        "non_empty_days": len(non_empty_days),
        "complete_or_usable_days": len(enough_days),
        "average_pumping_count_per_day": _average_count(days, "actual_pumping_times", "planned_pumping_times"),
        "average_nursing_count_per_day": _average_count(days, "actual_nursing_times", "planned_nursing_times"),
        "typical_pumping_times": selected_pumping_times,
        "typical_nursing_times": selected_nursing_times,
        "longest_gap_hours": _longest_gap_hours(selected_all_times),
        "basis_summary": _basis_summary(selected_day, confidence=confidence),
    }


def _average_count(days: list[dict[str, Any]], primary_key: str, fallback_key: str) -> float:
    if not days:
        return 0.0
    counts = [len(day.get(primary_key) or day.get(fallback_key) or []) for day in days]
    return round(sum(counts) / len(days), 1)


def _basis_summary(selected_day: dict[str, Any], *, confidence: str) -> str:
    if not selected_day:
        return "最近 7 天没有可用的吸奶或喂养时间记录。"
    pump_count = len(selected_day.get("planned_pumping_times") or selected_day.get("actual_pumping_times") or [])
    nursing_count = len(selected_day.get("planned_nursing_times") or selected_day.get("actual_nursing_times") or [])
    source = "计划提醒" if selected_day.get("planned_pumping_times") or selected_day.get("planned_nursing_times") else "实际记录"
    confidence_text = {"high": "比较可靠", "medium": "可参考", "low": "不足"}.get(confidence, "可参考")
    return f"最近 7 天里，{selected_day.get('date')} 的{source}最适合做排程参考：吸奶 {pump_count} 次，亲喂 {nursing_count} 次，信息{confidence_text}。"


def _sort_events(value: Any) -> list[dict[str, Any]]:
    events = [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    return sorted(events, key=lambda item: _minute_of_day(norm_text(item.get("time"))) or 0)


def _event_times(events: list[dict[str, Any]]) -> list[str]:
    return _unique_sorted_times([norm_text(item.get("time")) for item in events])


def _unique_sorted_times(times: list[str]) -> list[str]:
    valid = []
    for time in times:
        minute = _minute_of_day(norm_text(time))
        if minute is not None:
            valid.append((minute, f"{minute // 60:02d}:{minute % 60:02d}"))
    return [item[1] for item in sorted(set(valid))]


def _longest_gap_hours(times: list[str]) -> float | None:
    minutes = [_minute_of_day(item) for item in _unique_sorted_times(times)]
    minutes = [item for item in minutes if item is not None]
    if not minutes:
        return None
    if len(minutes) == 1:
        return 24.0
    gaps = []
    for index, start in enumerate(minutes):
        end = minutes[(index + 1) % len(minutes)]
        if index == len(minutes) - 1:
            end += 1440
        gaps.append(end - start)
    return round(max(gaps) / 60, 1)


def _minute_of_day(time_text: str) -> int | None:
    parsed = parse_datetime(time_text)
    if parsed is None:
        return None
    return parsed.hour * 60 + parsed.minute


def _hhmm_from_value(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _date_from_value(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return parsed.date().isoformat()


def _db_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _finished(value: Any) -> bool:
    token = norm_text(value).lower()
    return token in {"true", "1", "yes", "done", "completed", "complete", "已完成", "完成"}


def _is_direct_nursing(value: Any) -> bool:
    token = norm_text(value).lower()
    return token in {"direct", "breastfeeding", "breast", "母乳亲喂"} or "亲喂" in token


def _is_formula(value: Any) -> bool:
    token = norm_text(value).lower()
    return "奶粉" in token or "formula" in token
