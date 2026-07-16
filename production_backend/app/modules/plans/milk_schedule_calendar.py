from __future__ import annotations

from datetime import date
from typing import Any


MAX_MILK_SCHEDULE_CALENDAR_EVENTS = 21


class MilkScheduleCalendarEventError(ValueError):
    pass


def normalize_milk_schedule_calendar_events(
    value: Any,
    *,
    allowed_dates: list[date] | None = None,
) -> list[dict[str, Any]]:
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > MAX_MILK_SCHEDULE_CALENDAR_EVENTS:
        raise MilkScheduleCalendarEventError("invalid_milk_schedule_calendar_events")
    allowed = set(allowed_dates or [])
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise MilkScheduleCalendarEventError("invalid_milk_schedule_calendar_event")
        event_date = _date_value(raw.get("date"))
        if allowed and event_date not in allowed:
            raise MilkScheduleCalendarEventError("milk_schedule_calendar_event_outside_target_dates")
        start_time = _time_value(raw.get("start_time"))
        end_time = _time_value(raw.get("end_time"))
        start_minutes = _minutes(start_time)
        end_minutes = _minutes(end_time)
        if start_minutes >= end_minutes:
            raise MilkScheduleCalendarEventError("invalid_milk_schedule_calendar_event_time")
        title = str(raw.get("title") or "").strip()
        if not title:
            raise MilkScheduleCalendarEventError("milk_schedule_calendar_event_title_required")
        if len(title) > 120:
            raise MilkScheduleCalendarEventError("milk_schedule_calendar_event_title_too_long")
        description = str(raw.get("description") or "").strip()
        if len(description) > 500:
            raise MilkScheduleCalendarEventError("milk_schedule_calendar_event_description_too_long")
        event = {
            "date": event_date.isoformat(),
            "start_time": start_time,
            "end_time": end_time,
            "title": title,
            **({"description": description} if description else {}),
            "duration_minutes": end_minutes - start_minutes,
        }
        event_key = (event["date"], start_time, end_time, title)
        if event_key in seen:
            raise MilkScheduleCalendarEventError("duplicate_milk_schedule_calendar_event")
        seen.add(event_key)
        normalized.append(event)
    return normalized


def _date_value(value: Any) -> date:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise MilkScheduleCalendarEventError("invalid_milk_schedule_calendar_event_date") from exc


def _time_value(value: Any) -> str:
    parts = str(value or "").strip().split(":")
    if len(parts) != 2:
        raise MilkScheduleCalendarEventError("invalid_milk_schedule_calendar_event_time")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise MilkScheduleCalendarEventError("invalid_milk_schedule_calendar_event_time") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise MilkScheduleCalendarEventError("invalid_milk_schedule_calendar_event_time")
    return f"{hour:02d}:{minute:02d}"


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)
