from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any


MAX_MILK_PLAN_DAYS = 30
MAX_MILK_PLAN_TASK_TEMPLATES = 16
MAX_MILK_PLAN_SCHEDULED_TASKS = 240
_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")


class MilkPlanScheduleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MilkPlanScheduledTask:
    task_date: date
    task_time: str
    title: str
    description: str
    task_type: str
    duration_minutes: int | None = None


def normalize_milk_plan_payload(
    payload: dict[str, Any],
    *,
    today: date | None = None,
) -> tuple[dict[str, Any], tuple[MilkPlanScheduledTask, ...]]:
    raw_start_date = payload.get("start_date")
    start_date = _optional_date(raw_start_date)
    if raw_start_date not in (None, "") and start_date is None:
        raise MilkPlanScheduleValidationError("start_date must use YYYY-MM-DD")
    if start_date is None:
        start_date = (today or datetime.now(timezone.utc).date()) + timedelta(days=1)
    days = _bounded_int(payload.get("days"), default=7, minimum=1, maximum=MAX_MILK_PLAN_DAYS, field="days")
    end_date = start_date + timedelta(days=days - 1)

    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise MilkPlanScheduleValidationError("milk plan tasks must contain at least one schedulable item")
    if len(raw_tasks) > MAX_MILK_PLAN_TASK_TEMPLATES:
        raise MilkPlanScheduleValidationError(f"milk plan tasks must contain at most {MAX_MILK_PLAN_TASK_TEMPLATES} templates")

    templates: list[dict[str, Any]] = []
    scheduled_by_key: dict[tuple[date, str, str, str], MilkPlanScheduledTask] = {}
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, dict):
            raise MilkPlanScheduleValidationError(f"milk plan task {index + 1} must be an object")
        template, target_dates = _normalize_task_template(
            raw_task,
            index=index,
            start_date=start_date,
            end_date=end_date,
            days=days,
        )
        templates.append(template)
        for target_date in target_dates:
            scheduled = MilkPlanScheduledTask(
                task_date=target_date,
                task_time=str(template["time"]),
                title=str(template["title"]),
                description=str(template.get("description") or ""),
                task_type=str(template["task_type"]),
                duration_minutes=_optional_int(template.get("duration_minutes")),
            )
            scheduled_by_key[(target_date, scheduled.task_time, scheduled.title, scheduled.task_type)] = scheduled
            if len(scheduled_by_key) > MAX_MILK_PLAN_SCHEDULED_TASKS:
                raise MilkPlanScheduleValidationError(f"milk plan expands to more than {MAX_MILK_PLAN_SCHEDULED_TASKS} schedule tasks")

    scheduled_tasks = tuple(
        sorted(
            scheduled_by_key.values(),
            key=lambda task: (task.task_date, task.task_time, task.title, task.task_type),
        )
    )
    normalized: dict[str, Any] = {
        "start_date": start_date.isoformat(),
        "days": days,
        "tasks": templates,
    }
    direction = _text(payload.get("direction"))
    if direction:
        normalized["direction"] = direction[:32]
    reminders = payload.get("reminders")
    if isinstance(reminders, list) and reminders:
        normalized["reminders"] = [dict(item) for item in reminders[:40] if isinstance(item, dict)]
    return normalized, scheduled_tasks


def scheduled_task_dates(tasks: tuple[MilkPlanScheduledTask, ...]) -> list[str]:
    return [value.isoformat() for value in sorted({task.task_date for task in tasks})]


def _normalize_task_template(
    raw: dict[str, Any],
    *,
    index: int,
    start_date: date,
    end_date: date,
    days: int,
) -> tuple[dict[str, Any], tuple[date, ...]]:
    task_time = _normalize_time(raw.get("time") or raw.get("task_time") or raw.get("time_point"))
    if task_time is None:
        raise MilkPlanScheduleValidationError(f"milk plan task {index + 1} requires a valid HH:mm time")
    task_type = _normalize_task_type(
        raw.get("task_type") or raw.get("event_type") or raw.get("type") or raw.get("kind"),
        title_hint=_text(raw.get("title") or raw.get("event") or raw.get("calendar_title")),
    )
    title = (
        _text(raw.get("title") or raw.get("event") or raw.get("calendar_title") or raw.get("content") or raw.get("action"))
        or {"pumping": "吸奶", "feeding": "喂养", "other": "自定义任务"}[task_type]
    )
    if len(title) > 255:
        raise MilkPlanScheduleValidationError(f"milk plan task {index + 1} title is too long")
    description = _text(raw.get("description") or raw.get("action") or raw.get("action_text"))
    if len(description) > 2000:
        raise MilkPlanScheduleValidationError(f"milk plan task {index + 1} description is too long")

    explicit_date_value = raw.get("date") if raw.get("date") not in (None, "") else raw.get("task_date")
    explicit_date = _optional_date(explicit_date_value)
    if explicit_date_value not in (None, "") and explicit_date is None:
        raise MilkPlanScheduleValidationError(f"milk plan task {index + 1} date must use YYYY-MM-DD")
    day_value = raw.get("day") if raw.get("day") not in (None, "") else raw.get("day_index")
    day_number = None
    if day_value not in (None, ""):
        day_number = _bounded_int(day_value, default=1, minimum=1, maximum=days, field=f"task {index + 1} day")
    if explicit_date is not None and not start_date <= explicit_date <= end_date:
        raise MilkPlanScheduleValidationError(f"milk plan task {index + 1} date is outside the plan range")
    if explicit_date is not None and day_number is not None:
        expected_date = start_date + timedelta(days=day_number - 1)
        if explicit_date != expected_date:
            raise MilkPlanScheduleValidationError(f"milk plan task {index + 1} date and day do not match")

    if explicit_date is not None:
        target_dates = (explicit_date,)
    elif day_number is not None:
        target_dates = (start_date + timedelta(days=day_number - 1),)
    else:
        target_dates = tuple(start_date + timedelta(days=offset) for offset in range(days))

    template: dict[str, Any] = {
        "title": title,
        "time": task_time,
        "task_type": task_type,
    }
    if description:
        template["description"] = description
    if explicit_date is not None:
        template["date"] = explicit_date.isoformat()
    elif day_number is not None:
        template["day"] = day_number
    duration = raw.get("duration_minutes")
    if duration not in (None, ""):
        template["duration_minutes"] = _bounded_int(
            duration,
            default=30,
            minimum=1,
            maximum=240,
            field=f"task {index + 1} duration_minutes",
        )
    return template, target_dates


def _normalize_task_type(value: Any, *, title_hint: str) -> str:
    token = _text(value).lower()
    if token in {"pump", "pumping", "breast_pump", "吸奶", "泵奶"}:
        return "pumping"
    if token in {"feed", "feeding", "breastfeed", "bottle", "formula", "喂养", "喂奶", "亲喂"}:
        return "feeding"
    if token in {"other", "custom", "meeting", "自定义"}:
        return "other"
    if token:
        raise MilkPlanScheduleValidationError(f"unsupported milk plan task type: {token}")
    if "吸奶" in title_hint or "泵奶" in title_hint or "pump" in title_hint.lower():
        return "pumping"
    if "喂" in title_hint or "feed" in title_hint.lower():
        return "feeding"
    return "other"


def _normalize_time(value: Any) -> str | None:
    match = _TIME_PATTERN.fullmatch(_text(value))
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _optional_date(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, field: str) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise MilkPlanScheduleValidationError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MilkPlanScheduleValidationError(f"{field} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise MilkPlanScheduleValidationError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return int(value) if isinstance(value, (int, float)) else None


def _text(value: Any) -> str:
    return str(value or "").strip()
