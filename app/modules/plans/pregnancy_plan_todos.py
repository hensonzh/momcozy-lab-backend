from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import NAMESPACE_URL, uuid5


def normalize_pregnancy_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy whose todo item IDs are stable and unique within the plan."""
    normalized = deepcopy(payload)
    card = normalized.get("card")
    if not isinstance(card, dict):
        return normalized
    card_json = card.get("card_json")
    if not isinstance(card_json, dict):
        return normalized
    todo_plan = card_json.get("todo_plan")
    if not isinstance(todo_plan, dict):
        return normalized
    periods = todo_plan.get("periods")
    if not isinstance(periods, list):
        return normalized
    todo_plan["periods"] = normalize_pregnancy_todo_periods(periods)
    return normalized


def normalize_pregnancy_todo_periods(periods: list[Any]) -> list[Any]:
    """Normalize card-level periods before the same card is shown and persisted."""
    normalized_periods = deepcopy(periods)
    seen_item_ids: set[str] = set()
    for period_index, period in enumerate(normalized_periods):
        if not isinstance(period, dict):
            continue
        raw_items = period.get("items")
        if not isinstance(raw_items, list):
            continue
        normalized_items: list[Any] = []
        period_id = _text(period.get("id")) or f"period_{period_index + 1:02d}"
        for item_index, raw_item in enumerate(raw_items):
            if isinstance(raw_item, str):
                item: dict[str, Any] = {"title": raw_item}
            elif isinstance(raw_item, dict):
                item = deepcopy(raw_item)
            else:
                normalized_items.append(raw_item)
                continue
            stable_id = _text(item.get("item_id")) or _text(item.get("id"))
            if not stable_id:
                title = _text(item.get("title"))
                seed = f"momcozy:pregnancy-plan:{period_id}:{item_index}:{title}"
                stable_id = f"todo-{uuid5(NAMESPACE_URL, seed)}"
            elif stable_id in seen_item_ids:
                seed = f"momcozy:pregnancy-plan-instance:{period_id}:{item_index}:{stable_id}"
                stable_id = f"todo-{uuid5(NAMESPACE_URL, seed)}"
            item["item_id"] = stable_id
            seen_item_ids.add(stable_id)
            normalized_items.append(item)
        period["items"] = normalized_items
    return normalized_periods


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
