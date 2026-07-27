from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class PregnancyPlanTaskDraft:
    task_date: date
    title: str
    description: str
    payload: dict[str, Any]


def pregnancy_plan_task_drafts(plan_payload: dict[str, Any]) -> list[PregnancyPlanTaskDraft]:
    card_json = _dict(_dict(plan_payload, "card"), "card_json")
    periods = _list(_dict(card_json, "todo_plan").get("periods"))
    generated_on = _generated_on(card_json)
    current_week = _positive_int(_dict(card_json, "generation_context").get("current_week"))
    drafts: list[PregnancyPlanTaskDraft] = []
    seen_instances: set[tuple[str, str]] = set()

    for period_index, raw_period in enumerate(periods):
        if not isinstance(raw_period, dict):
            continue
        period = dict(raw_period)
        period_id = _text(period.get("id")) or f"period_{period_index + 1:02d}"
        task_date = _period_task_date(
            period=period,
            period_index=period_index,
            generated_on=generated_on,
            current_week=current_week,
        )
        for item_index, raw_item in enumerate(_list(period.get("items"))):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            title = _text(item.get("title"))
            if not title:
                continue
            item_id = _text(item.get("item_id")) or _text(item.get("id")) or f"item_{item_index + 1:02d}"
            instance = (period_id, item_id)
            if instance in seen_instances:
                continue
            seen_instances.add(instance)
            steps = [_text(value) for value in _list(item.get("steps")) if _text(value)]
            description = _text(item.get("reason")) or _text(item.get("why_for_you"))
            drafts.append(
                PregnancyPlanTaskDraft(
                    task_date=task_date,
                    title=title,
                    description=description,
                    payload={
                        "domain": "pregnancy",
                        "event_type": "task",
                        "source": "pregnancy_plan",
                        "plan_todo_period_id": period_id,
                        "plan_todo_item_id": item_id,
                        "plan_todo_instance_id": f"{period_id}:{item_id}",
                        "period_title": _text(period.get("title")),
                        "week_start": _positive_int(period.get("week_start")),
                        "week_end": _positive_int(period.get("week_end")),
                        **({"steps": steps[:3]} if steps else {}),
                    },
                )
            )
    return drafts


def _period_task_date(
    *,
    period: dict[str, Any],
    period_index: int,
    generated_on: date,
    current_week: int | None,
) -> date:
    week_start = _positive_int(period.get("week_start"))
    if current_week is not None and week_start is not None:
        return generated_on + timedelta(weeks=max(0, week_start - current_week))
    if current_week is not None and _text(period.get("id")) == "period_terminal":
        return generated_on + timedelta(weeks=max(0, 40 - current_week))
    return generated_on + timedelta(weeks=period_index)


def _generated_on(card_json: dict[str, Any]) -> date:
    raw = _text(_dict(card_json, "generation_context").get("created_at"))
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return datetime.now(timezone.utc).date()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()
