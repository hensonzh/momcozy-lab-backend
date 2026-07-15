from __future__ import annotations

from typing import Any

from .schemas import ServiceResult, error_result, hhmm, norm_text, ok_result, to_bool, to_int

PUMP_DURATION_MINUTES = 30
MIN_PUMP_GAP_MINUTES = 90
DAY_MINUTES = 24 * 60


def preview_adjusted_schedule(
    *,
    existing_items: list[dict[str, Any]],
    new_event: dict[str, Any],
    adjustable_types: set[str] | None = None,
    duration_minutes: int = PUMP_DURATION_MINUTES,
    min_gap_minutes: int = MIN_PUMP_GAP_MINUTES,
) -> ServiceResult:
    """Build a schedule adjustment proposal without database access.

    `existing_items` should be normalized calendar-like rows for one date.
    `new_event` should include `start_time`, `end_time`, and `content`.
    """

    duration = max(to_int(duration_minutes, PUMP_DURATION_MINUTES), 1)
    min_gap = max(to_int(min_gap_minutes, MIN_PUMP_GAP_MINUTES), 0)
    adjustable_types = adjustable_types or {"吸奶"}

    event_start = parse_minute_of_day(new_event.get("start_time"))
    event_end = parse_minute_of_day(new_event.get("end_time"))
    if event_start is None or event_end is None or event_start == event_end:
        return error_result("invalid_event_time", "The new event needs a valid start and end time.")

    blocked_periods = [
        {
            "start_time": format_hhmm(event_start),
            "end_time": format_hhmm(event_end),
            "content": norm_text(new_event.get("content")) or "自定义日程",
            "source_item": dict(new_event),
        }
    ]
    fixed_blocks = _fixed_blocks_from_items(existing_items, adjustable_types)
    all_blocks = [*fixed_blocks, *blocked_periods]

    adjustable_items = [
        item
        for item in existing_items
        if norm_text(item.get("type") or item.get("entry_type")) in adjustable_types
    ]
    adjusted_result = build_adjusted_schedule_rows(
        blocked_periods=all_blocks,
        original_items=adjustable_items,
        duration_minutes=duration,
        min_gap_minutes=min_gap,
        blocked_entry_type="custom_event",
        adjustable_entry_type="calendar_task",
    )

    adjusted_tasks = adjusted_result["adjusted_items"]
    removed_items = adjusted_result["removed_items"]
    by_identity = {_item_identity(item): item for item in adjusted_tasks}

    proposed_changes: list[dict[str, Any]] = []
    unchanged_items: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for original in adjustable_items:
        identity = _item_identity(original)
        adjusted = by_identity.get(identity)
        original_start = format_hhmm(parse_minute_of_day(original.get("start_time") or original.get("time") or original.get("time_point")) or 0)
        if adjusted is None:
            continue
        if adjusted.get("start_time") == original_start:
            unchanged_items.append(_public_item(original))
            continue
        proposed_changes.append(
            {
                "item": _public_item(original),
                "before": {
                    "start_time": original_start,
                    "end_time": _end_hhmm(original, duration),
                },
                "after": {
                    "start_time": adjusted["start_time"],
                    "end_time": adjusted["end_time"],
                },
                "reason": "conflicts_with_new_event",
            }
        )

    for original in adjustable_items:
        original_start = parse_minute_of_day(original.get("start_time") or original.get("time") or original.get("time_point"))
        if original_start is None:
            continue
        if _find_overlapping_range(original_start, _block_ranges(blocked_periods), duration) is not None:
            conflicts.append(
                {
                    "item_id": original.get("item_id"),
                    "task_id": original.get("task_id"),
                    "start_time": format_hhmm(original_start),
                    "content": norm_text(original.get("content")),
                    "conflict_with": norm_text(new_event.get("content")) or "自定义日程",
                }
            )

    return ok_result(
        "preview_ready",
        data={
            "requires_confirmation": True,
            "new_event": {
                **dict(new_event),
                "start_time": format_hhmm(event_start),
                "end_time": format_hhmm(event_end),
                "content": norm_text(new_event.get("content")) or "自定义日程",
            },
            "conflicts": conflicts,
            "proposed_changes": proposed_changes,
            "unchanged_items": unchanged_items,
            "removed_items": [_public_item(item) for item in removed_items],
            "adjusted_items": adjusted_tasks,
            "summary": _preview_summary(conflicts, proposed_changes, removed_items),
        },
    )


def build_adjusted_schedule_rows(
    *,
    blocked_periods: list[dict[str, Any]],
    original_items: list[dict[str, Any]],
    duration_minutes: int = PUMP_DURATION_MINUTES,
    min_gap_minutes: int = MIN_PUMP_GAP_MINUTES,
    blocked_entry_type: str = "blocked",
    adjustable_entry_type: str = "task",
) -> dict[str, Any]:
    """Adjust item times around blocked periods.

    This is a pure function. It does not read or write storage.
    """

    duration = max(to_int(duration_minutes, PUMP_DURATION_MINUTES), 1)
    min_gap = max(to_int(min_gap_minutes, MIN_PUMP_GAP_MINUTES), 0)
    ranges = _block_ranges(blocked_periods)

    normalized_items = _normalize_original_items(original_items)
    adjusted: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    previous_start: int | None = None

    for item in normalized_items:
        original_minute = int(item["minute"])
        conflict = _find_overlapping_range(original_minute, ranges, duration)
        scheduled_minute = original_minute

        if conflict is not None:
            move_earlier = _prefer_move_earlier(original_minute, conflict, duration)
            earlier_candidate = _find_previous_valid_start(
                preferred_start=int(conflict[0]) - duration,
                previous_start=previous_start,
                blocked_ranges=ranges,
                duration_minutes=duration,
                min_gap_minutes=min_gap,
            )
            later_candidate = _find_next_valid_start(
                preferred_start=int(conflict[1]),
                previous_start=previous_start,
                blocked_ranges=ranges,
                duration_minutes=duration,
                min_gap_minutes=min_gap,
            )
            candidates = [earlier_candidate, later_candidate] if move_earlier else [later_candidate, earlier_candidate]
            scheduled_minute = _first_candidate(candidates)
        else:
            valid_original = _find_next_valid_start(
                preferred_start=original_minute,
                previous_start=previous_start,
                blocked_ranges=ranges,
                duration_minutes=duration,
                min_gap_minutes=min_gap,
            )
            scheduled_minute = valid_original if valid_original is not None else -1

        if scheduled_minute is None or scheduled_minute < 0 or scheduled_minute + duration > DAY_MINUTES:
            removed.append(item["source_item"])
            continue

        previous_start = int(scheduled_minute)
        adjusted.append(
            {
                **item["source_item"],
                "entry_type": adjustable_entry_type,
                "start_time": format_hhmm(scheduled_minute),
                "end_time": format_hhmm(scheduled_minute + duration),
                "time": format_hhmm(scheduled_minute),
                "time_point": format_hhmm(scheduled_minute),
                "duration_minutes": duration,
                "finish": to_bool(item["source_item"].get("finish")),
            }
        )

    blocked_rows = _blocked_rows(blocked_periods, blocked_entry_type)
    merged_rows = [*blocked_rows, *adjusted]
    merged_rows.sort(key=lambda item: (parse_minute_of_day(item.get("start_time")) or 0, norm_text(item.get("content"))))
    return {
        "merged_rows": merged_rows,
        "adjusted_items": adjusted,
        "blocked_items": blocked_rows,
        "removed_items": removed,
    }


def parse_minute_of_day(raw_time: Any) -> int | None:
    token = norm_text(raw_time)
    if not token:
        return None
    if "T" in token:
        token = token.split("T")[-1]
    if " " in token:
        token = token.split(" ")[-1]
    token = token.strip().rstrip("Z")
    parsed = hhmm(token)
    if not parsed:
        return None
    try:
        hour, minute = parsed.split(":", 1)
        return int(hour) * 60 + int(minute)
    except ValueError:
        return None


def format_hhmm(minute_of_day: int) -> str:
    minute = int(minute_of_day) % DAY_MINUTES
    hour = minute // 60
    minute = minute % 60
    return f"{hour:02d}:{minute:02d}"


def _normalize_original_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        minute = parse_minute_of_day(raw.get("start_time") or raw.get("time_point") or raw.get("time"))
        if minute is None:
            continue
        normalized.append(
            {
                "minute": int(minute),
                "sort_order": to_int(raw.get("sort_order") or raw.get("task_id") or raw.get("item_id"), 0),
                "item_id": to_int(raw.get("item_id"), 0),
                "task_id": to_int(raw.get("task_id"), 0),
                "source_item": dict(raw),
            }
        )
    normalized.sort(key=lambda item: (item["minute"], item["sort_order"], item["item_id"], item["task_id"]))
    return normalized


def _fixed_blocks_from_items(items: list[dict[str, Any]], adjustable_types: set[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for item in items:
        item_type = norm_text(item.get("type") or item.get("entry_type"))
        if item_type in adjustable_types:
            continue
        start = parse_minute_of_day(item.get("start_time") or item.get("time"))
        end = parse_minute_of_day(item.get("end_time"))
        if start is None:
            continue
        if end is None:
            end = start + PUMP_DURATION_MINUTES
        if end == start:
            continue
        blocks.append(
            {
                "start_time": format_hhmm(start),
                "end_time": format_hhmm(end),
                "content": norm_text(item.get("content")) or item_type or "固定日程",
                "source_item": dict(item),
            }
        )
    return blocks


def _block_ranges(blocked_periods: list[dict[str, Any]]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for period in blocked_periods:
        start = parse_minute_of_day(period.get("start_time"))
        end = parse_minute_of_day(period.get("end_time"))
        if start is None or end is None or start == end:
            continue
        ranges.extend(_split_period(start, end))
    ranges.sort(key=lambda item: (item[0], item[1]))
    return ranges


def _split_period(start_minute: int, end_minute: int) -> list[tuple[int, int]]:
    start = int(start_minute)
    end = int(end_minute)
    if start < end:
        return [(start, end)]
    return [(0, end), (start, DAY_MINUTES)]


def _blocked_rows(blocked_periods: list[dict[str, Any]], entry_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period in blocked_periods:
        start = parse_minute_of_day(period.get("start_time"))
        end = parse_minute_of_day(period.get("end_time"))
        if start is None or end is None or start == end:
            continue
        rows.append(
            {
                **dict(period.get("source_item") if isinstance(period.get("source_item"), dict) else {}),
                "entry_type": entry_type,
                "start_time": format_hhmm(start),
                "end_time": format_hhmm(end),
                "time": f"{format_hhmm(start)}-{format_hhmm(end)}",
                "content": norm_text(period.get("content")) or "自定义日程",
                "finish": False,
            }
        )
    return rows


def _find_overlapping_range(
    session_start: int,
    blocked_ranges: list[tuple[int, int]],
    duration_minutes: int,
) -> tuple[int, int] | None:
    session_end = int(session_start) + int(duration_minutes)
    if session_start < 0 or session_end > DAY_MINUTES:
        return (max(session_start, 0), min(session_end, DAY_MINUTES))
    for range_start, range_end in blocked_ranges:
        if int(session_start) < int(range_end) and int(session_end) > int(range_start):
            return (range_start, range_end)
    return None


def _prefer_move_earlier(session_start: int, conflict_range: tuple[int, int], duration_minutes: int) -> bool:
    overlap_start = max(int(session_start), int(conflict_range[0]))
    overlap_end = min(int(session_start) + int(duration_minutes), int(conflict_range[1]))
    overlap_midpoint = (overlap_start + overlap_end) / 2.0
    distance_to_start = abs(overlap_midpoint - int(conflict_range[0]))
    distance_to_end = abs(int(conflict_range[1]) - overlap_midpoint)
    return distance_to_start <= distance_to_end


def _find_next_valid_start(
    *,
    preferred_start: int,
    previous_start: int | None,
    blocked_ranges: list[tuple[int, int]],
    duration_minutes: int,
    min_gap_minutes: int,
) -> int | None:
    lower_bound = 0 if previous_start is None else int(previous_start) + int(min_gap_minutes)
    candidate = max(int(preferred_start), lower_bound)
    while candidate + int(duration_minutes) <= DAY_MINUTES:
        conflict = _find_overlapping_range(candidate, blocked_ranges, duration_minutes)
        if conflict is None:
            return candidate
        candidate = max(int(conflict[1]), lower_bound)
    return None


def _find_previous_valid_start(
    *,
    preferred_start: int,
    previous_start: int | None,
    blocked_ranges: list[tuple[int, int]],
    duration_minutes: int,
    min_gap_minutes: int,
) -> int | None:
    lower_bound = 0 if previous_start is None else int(previous_start) + int(min_gap_minutes)
    candidate = min(int(preferred_start), DAY_MINUTES - int(duration_minutes))
    while candidate >= lower_bound:
        conflict = _find_overlapping_range(candidate, blocked_ranges, duration_minutes)
        if conflict is None:
            return candidate
        candidate = int(conflict[0]) - int(duration_minutes)
    return None


def _first_candidate(candidates: list[int | None]) -> int:
    for candidate in candidates:
        if candidate is not None:
            return int(candidate)
    return -1


def _item_identity(item: dict[str, Any]) -> tuple[int, int, str]:
    return (to_int(item.get("item_id"), 0), to_int(item.get("task_id"), 0), norm_text(item.get("content")))


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item.get("item_id"),
        "task_id": item.get("task_id"),
        "type": item.get("type") or item.get("entry_type"),
        "start_time": format_hhmm(parse_minute_of_day(item.get("start_time") or item.get("time")) or 0),
        "end_time": _end_hhmm(item, PUMP_DURATION_MINUTES),
        "content": norm_text(item.get("content")),
        "finish": to_bool(item.get("finish")),
    }


def _end_hhmm(item: dict[str, Any], fallback_duration: int) -> str:
    end = parse_minute_of_day(item.get("end_time"))
    if end is not None:
        return format_hhmm(end)
    start = parse_minute_of_day(item.get("start_time") or item.get("time") or item.get("time_point"))
    if start is None:
        return ""
    return format_hhmm(start + int(fallback_duration))


def _preview_summary(
    conflicts: list[dict[str, Any]],
    proposed_changes: list[dict[str, Any]],
    removed_items: list[dict[str, Any]],
) -> str:
    if not conflicts:
        return "新日程没有影响当前排奶安排。"
    parts = [f"发现 {len(conflicts)} 个冲突"]
    if proposed_changes:
        parts.append(f"建议调整 {len(proposed_changes)} 个任务")
    if removed_items:
        parts.append(f"{len(removed_items)} 个任务暂时无法安排")
    return "，".join(parts) + "。"
