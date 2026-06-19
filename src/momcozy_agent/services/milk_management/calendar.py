from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .. import data_store
from .db import execute, fetch_all, fetch_one, transaction
from .schemas import (
    CALENDAR_TYPE_CUSTOM,
    CALENDAR_TYPE_NURSING,
    CALENDAR_TYPE_PUMP,
    CalendarItem,
    ServiceResult,
    error_result,
    hhmm,
    norm_text,
    ok_result,
    parse_datetime,
    to_bool,
    to_int,
)
from .scheduling_rules import build_adjusted_schedule_rows, format_hhmm, parse_minute_of_day

VALID_CALENDAR_TYPES = {CALENDAR_TYPE_PUMP, CALENDAR_TYPE_NURSING, CALENDAR_TYPE_CUSTOM}
DEFAULT_RESCHEDULE_DURATION_MINUTES = 30
DEFAULT_RESCHEDULE_MIN_GAP_MINUTES = 90


def get_calendar_day(
    *,
    user_id: str,
    target_date: str,
    plan_id: int | None = None,
    item_type: str | None = None,
) -> ServiceResult:
    uid = str(user_id or "").strip()
    date = str(target_date or "").strip()
    if not uid or not date:
        return error_result("missing_required_field", "user_id and target_date are required.")

    params: list[Any] = [uid, date]
    clauses = ["user_id = ?", "date = ?"]
    if plan_id is not None:
        clauses.append("COALESCE(plan_id, 0) = ?")
        params.append(int(plan_id))
    if item_type:
        if item_type not in VALID_CALENDAR_TYPES:
            return error_result("invalid_calendar_type", f"Unsupported calendar type: {item_type}")
        clauses.append("type = ?")
        params.append(item_type)

    rows = fetch_all(
        f"""
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE {" AND ".join(clauses)}
        ORDER BY COALESCE(start_time, ''), COALESCE(task_id, item_id), item_id
        """,
        params,
    )
    items = [_normalize_calendar_row(row) for row in rows]
    completed = len([item for item in items if item.get("finish")])
    plan_context = _calendar_plan_context(uid, items, target_date=date)
    return ok_result(
        "calendar_day_loaded",
        data={
            "user_id": uid,
            "target_date": date,
            "items": items,
            "plan_context": plan_context,
            "summary": {
                "total_count": len(items),
                "completed_count": completed,
                "pending_count": max(len(items) - completed, 0),
            },
        },
    )


def get_calendar_range(
    *,
    user_id: str,
    start_at: str,
    end_at: str,
    plan_id: int | None = None,
    item_type: str | None = None,
    include_items: bool = True,
    limit: int = 200,
) -> ServiceResult:
    uid = str(user_id or "").strip()
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法读取计划执行情况。")
    window = _range_window(start_at, end_at)
    if window.get("error"):
        return error_result("invalid_time_range", window["error"])
    if item_type and item_type not in VALID_CALENDAR_TYPES:
        return error_result("invalid_calendar_type", f"Unsupported calendar type: {item_type}")

    rows = _fetch_calendar_range_rows(
        user_id=uid,
        start_at=_db_time(window["start_dt"]),
        end_at=_db_time(window["end_dt"]),
        plan_id=plan_id,
        item_type=item_type,
    )
    items = [_normalize_calendar_row(row) for row in rows]
    raw_limit = min(max(to_int(limit, 200), 1), 500)
    plan_context = _calendar_plan_context(
        uid,
        items,
        window={"start_at": _db_time(window["start_dt"]), "end_at": _db_time(window["end_dt"]), "end_exclusive": True},
    )
    return ok_result(
        "calendar_range_loaded",
        f"已读取 {window['start_text']} 到 {window['end_text']} 的计划执行情况。",
        {
            "user_id": uid,
            "window": {
                "start_at": _db_time(window["start_dt"]),
                "end_at": _db_time(window["end_dt"]),
                "end_exclusive": True,
            },
            "plan_id": plan_id,
            "item_type": item_type,
            "summary": _calendar_range_summary(items),
            "plan_context": plan_context,
            "items": items[:raw_limit] if include_items else [],
            "item_count": len(items),
            "raw_item_limit": raw_limit,
        },
    )


def get_current_calendar_plan(
    *,
    user_id: str,
    target_date: str,
    lookahead_days: int = 14,
    item_type: str | None = None,
    include_items: bool = True,
    limit: int = 200,
) -> ServiceResult:
    uid = str(user_id or "").strip()
    date = _date_text(target_date)
    if not uid or not date:
        return error_result("missing_required_field", "user_id and target_date are required.")
    start_dt = parse_datetime(date)
    if start_dt is None:
        return error_result("invalid_target_date", "target_date must be a valid date.")
    if item_type and item_type not in VALID_CALENDAR_TYPES:
        return error_result("invalid_calendar_type", f"Unsupported calendar type: {item_type}")

    lookahead = min(max(to_int(lookahead_days, 14), 0), 60)
    end_dt = start_dt + timedelta(days=lookahead + 1)
    rows = _fetch_calendar_range_rows(
        user_id=uid,
        start_at=_db_time(start_dt),
        end_at=_db_time(end_dt),
        plan_id=None,
        item_type=item_type,
    )
    items = [_normalize_calendar_row(row) for row in rows]
    search_window = {"start_at": _db_time(start_dt), "end_at": _db_time(end_dt), "end_exclusive": True}
    requested_date = start_dt.date().isoformat()
    plan_items = [item for item in items if _is_calendar_plan_task(item)]
    same_day_items = [item for item in plan_items if norm_text(item.get("date")) == requested_date]
    chosen_date = requested_date if same_day_items else _first_item_date(plan_items)
    if not chosen_date:
        return ok_result(
            "calendar_current_plan_not_found",
            "暂未找到当前或近期计划任务。",
            {
                "user_id": uid,
                "requested_date": requested_date,
                "target_date": requested_date,
                "current_plan_date": None,
                "used_lookahead": False,
                "lookahead_days": lookahead,
                "search_window": search_window,
                "items": [],
                "item_count": 0,
                "raw_item_limit": min(max(to_int(limit, 200), 1), 500),
                "summary": _calendar_range_summary([]),
                "plan_context": _calendar_plan_context(uid, [], target_date=requested_date),
            },
        )

    chosen_items = [item for item in items if norm_text(item.get("date")) == chosen_date]
    raw_limit = min(max(to_int(limit, 200), 1), 500)
    return ok_result(
        "calendar_current_plan_loaded",
        f"已读取 {chosen_date} 的当前奶量计划任务。",
        {
            "user_id": uid,
            "requested_date": requested_date,
            "target_date": chosen_date,
            "current_plan_date": chosen_date,
            "used_lookahead": chosen_date != requested_date,
            "lookahead_days": lookahead,
            "search_window": search_window,
            "items": chosen_items[:raw_limit] if include_items else [],
            "item_count": len(chosen_items),
            "raw_item_limit": raw_limit,
            "summary": _calendar_range_summary(chosen_items),
            "plan_context": _calendar_plan_context(uid, chosen_items, target_date=chosen_date),
        },
    )


def preview_calendar_adjustment(
    *,
    user_id: str,
    target_date: str,
    event_start_time: str,
    event_end_time: str | None = None,
    duration_minutes: int | None = None,
    content: str,
    item_type: str | None = None,
    plan_id: int | None = None,
) -> ServiceResult:
    uid = str(user_id or "").strip()
    date = _date_text(target_date)
    title = norm_text(content)
    start_at = _calendar_datetime(date, event_start_time)
    if not uid or not date or not title or not start_at:
        return error_result("missing_required_field", "user_id, target_date, event_start_time, and content are required.")

    end_at = _calendar_datetime(date, event_end_time) if event_end_time else ""
    if not end_at and duration_minutes is not None:
        end_at = _add_minutes(start_at, max(to_int(duration_minutes, 0), 1))
    if not end_at:
        end_at = _add_minutes(start_at, 30)

    conflicts = _calendar_conflicts(user_id=uid, target_date=date, start_at=start_at, end_at=end_at, plan_id=plan_id)
    calendar_type = _resolve_calendar_type(item_type, title)
    if calendar_type not in VALID_CALENDAR_TYPES:
        return error_result("invalid_calendar_type", f"Unsupported calendar type: {calendar_type}")
    adjustments = _build_adjustments_for_event(conflicts=conflicts, event_end_time=end_at)
    proposal = {
        "action": "insert_event_and_adjust_calendar",
        "user_id": uid,
        "target_date": date,
        "insert_event": {
            "start_time": start_at,
            "end_time": end_at,
            "content": title,
            "type": calendar_type,
            "source": "用户输入",
            "is_milk_pump": calendar_type == CALENDAR_TYPE_PUMP,
        },
        "item": {
            "start_time": start_at,
            "end_time": end_at,
            "content": title,
            "type": calendar_type,
            "source": "用户输入",
            "is_milk_pump": calendar_type == CALENDAR_TYPE_PUMP,
        },
        "updates": adjustments,
        "conflicts": conflicts,
    }
    return ok_result(
        "calendar_adjustment_previewed",
        data={
            "proposal": proposal,
            "proposal_json": json.dumps(proposal, ensure_ascii=False),
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "insert_event": proposal["insert_event"],
            "updates": adjustments,
        },
    )


def apply_calendar_adjustment(
    *,
    user_id: str,
    target_date: str,
    proposal: Any,
    idempotency_key: str | None = None,
) -> ServiceResult:
    uid = str(user_id or "").strip()
    date = _date_text(target_date)
    key = norm_text(idempotency_key)
    proposal_data = _parse_json_object(proposal)
    item = proposal_data.get("insert_event") if isinstance(proposal_data.get("insert_event"), dict) else {}
    if not item:
        item = proposal_data.get("item") if isinstance(proposal_data.get("item"), dict) else {}
    start_at = _calendar_datetime(date, item.get("start_time"))
    end_at = _calendar_datetime(date, item.get("end_time")) if item.get("end_time") else None
    content = norm_text(item.get("content"))
    item_type = _resolve_calendar_type(item.get("type"), content)
    if not uid or not date or not start_at or not content:
        return error_result("missing_required_field", "Confirmed calendar adjustment is missing required event fields.")
    if not key:
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")
    if item_type not in VALID_CALENDAR_TYPES:
        return error_result("invalid_calendar_type", f"Unsupported calendar type: {item_type}")
    updates = proposal_data.get("updates") if isinstance(proposal_data.get("updates"), list) else []

    replay_item_id = _idempotent_calendar_adjustment_item_id(user_id=uid, idempotency_key=key)
    if replay_item_id > 0:
        inserted = _calendar_item_by_id(replay_item_id)
        return ok_result(
            "calendar_adjustment_idempotent_replay",
            data={
                "user_id": uid,
                "target_date": date,
                "inserted_event": _normalize_calendar_row(inserted or {}),
                "item": _normalize_calendar_row(inserted or {}),
                "applied_updates": [],
            },
        )

    with transaction() as conn:
        _ensure_idempotency_table(conn)
        row = conn.execute(
            "SELECT COALESCE(MAX(task_id), 0) AS max_task_id FROM calendar WHERE user_id = ? AND date = ?",
            (uid, date),
        ).fetchone()
        task_id = int(row["max_task_id"] or 0) + 1
        cursor = conn.execute(
            """
            INSERT INTO calendar (
                user_id, plan_id, date, task_id, start_time, end_time,
                content, type, source, is_milk_pump, finish, created_at, modified_at
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, '用户输入', ?, 'false', CURRENT_TIMESTAMP, NULL)
            """,
            (uid, date, task_id, start_at, end_at, content, item_type, 1 if item_type == CALENDAR_TYPE_PUMP else 0),
        )
        inserted_id = int(cursor.lastrowid or 0)
        conn.execute(
            """
            INSERT OR IGNORE INTO tool_idempotency_log(user_id, tool_name, idempotency_key, resource_id, created_at)
            VALUES (?, 'milk_calendar_mutate.apply_adjustment', ?, ?, ?)
            """,
            (uid, key, inserted_id, _now()),
        )
        applied_updates: list[dict[str, Any]] = []
        for update in updates:
            if not isinstance(update, dict):
                continue
            item_id = to_int(update.get("item_id"), 0)
            new_start = _calendar_datetime(date, update.get("new_start_time"))
            new_end = _calendar_datetime(date, update.get("new_end_time")) if update.get("new_end_time") else None
            if item_id <= 0 or not new_start:
                continue
            cursor = conn.execute(
                """
                UPDATE calendar
                SET start_time = ?,
                    end_time = ?,
                    modified_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                  AND date = ?
                  AND item_id = ?
                """,
                (new_start, new_end, uid, date, item_id),
            )
            if int(cursor.rowcount or 0) > 0:
                applied_updates.append({"item_id": item_id, "new_start_time": new_start, "new_end_time": new_end})

    inserted = fetch_one(
        """
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE item_id = ?
        """,
        (inserted_id,),
    )
    return ok_result(
        "calendar_adjustment_applied",
        data={
            "user_id": uid,
            "target_date": date,
            "inserted_event": _normalize_calendar_row(inserted or {}),
            "item": _normalize_calendar_row(inserted or {}),
            "applied_updates": applied_updates,
        },
    )


def preview_day_reschedule(
    *,
    user_id: str,
    target_date: str,
    busy_windows: Any,
    adjustable_item_types: Any = None,
    plan_id: int | None = None,
    default_duration_minutes: int = DEFAULT_RESCHEDULE_DURATION_MINUTES,
    min_gap_minutes: int = DEFAULT_RESCHEDULE_MIN_GAP_MINUTES,
    include_busy_events: bool = True,
) -> ServiceResult:
    uid = str(user_id or "").strip()
    date = _date_text(target_date)
    if not uid or not date:
        return error_result("missing_required_field", "user_id and target_date are required.")

    windows = _parse_busy_windows(busy_windows, target_date=date)
    if not windows:
        return error_result("missing_busy_windows", "需要至少一个明确的不可用时间段。")

    adjustable_types = _parse_adjustable_types(adjustable_item_types)
    day_rows = fetch_all(
        """
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE user_id = ?
          AND date = ?
          AND (? IS NULL OR COALESCE(plan_id, 0) = COALESCE(?, 0))
        ORDER BY COALESCE(start_time, ''), COALESCE(task_id, item_id), item_id
        """,
        (uid, date, plan_id, plan_id),
    )
    items = [_normalize_calendar_row(row) for row in day_rows]
    adjustable_items = [item for item in items if _is_pending_adjustable_item(item, adjustable_types)]
    if not adjustable_items:
        return error_result(
            "no_adjustable_calendar_items",
            "这一天没有可重排的吸奶/亲喂计划任务。",
            data={"user_id": uid, "target_date": date, "busy_windows": windows, "adjustable_item_types": sorted(adjustable_types)},
        )

    fixed_blocks = _fixed_blocks_for_reschedule(items, adjustable_types)
    blocked_periods = [*_busy_blocks_for_reschedule(windows), *fixed_blocks]
    schedule = build_adjusted_schedule_rows(
        blocked_periods=blocked_periods,
        original_items=adjustable_items,
        duration_minutes=max(to_int(default_duration_minutes, DEFAULT_RESCHEDULE_DURATION_MINUTES), 1),
        min_gap_minutes=max(to_int(min_gap_minutes, DEFAULT_RESCHEDULE_MIN_GAP_MINUTES), 0),
        blocked_entry_type="busy_window",
        adjustable_entry_type="calendar_task",
    )
    updates = _reschedule_updates(date=date, original_items=adjustable_items, adjusted_items=schedule["adjusted_items"])
    conflicts = _reschedule_conflicts(windows=windows, adjustable_items=adjustable_items, duration_minutes=default_duration_minutes)
    proposal = {
        "action": "reschedule_day_around_busy_windows",
        "user_id": uid,
        "target_date": date,
        "busy_windows": windows,
        "insert_events": _insert_events_for_reschedule(windows) if include_busy_events else [],
        "updates": updates,
        "adjustable_item_types": sorted(adjustable_types),
        "default_duration_minutes": max(to_int(default_duration_minutes, DEFAULT_RESCHEDULE_DURATION_MINUTES), 1),
        "min_gap_minutes": max(to_int(min_gap_minutes, DEFAULT_RESCHEDULE_MIN_GAP_MINUTES), 0),
    }
    return ok_result(
        "calendar_reschedule_previewed",
        data={
            "proposal": proposal,
            "proposal_json": json.dumps(proposal, ensure_ascii=False),
            "target_date": date,
            "busy_windows": windows,
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "updates": updates,
            "updated_count": len(updates),
            "unchanged_count": max(len(adjustable_items) - len(updates), 0),
            "summary": _reschedule_summary(windows=windows, conflicts=conflicts, updates=updates),
        },
    )


def apply_calendar_reschedule(
    *,
    user_id: str,
    target_date: str,
    proposal: Any,
    idempotency_key: str | None = None,
) -> ServiceResult:
    uid = str(user_id or "").strip()
    date = _date_text(target_date)
    key = norm_text(idempotency_key)
    proposal_data = _parse_json_object(proposal)
    if not uid or not date or not proposal_data:
        return error_result("missing_required_field", "Confirmed calendar reschedule is missing required fields.")
    if not key:
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")
    if norm_text(proposal_data.get("action")) != "reschedule_day_around_busy_windows":
        return error_result("invalid_reschedule_proposal", "不是有效的日程重排 proposal。")

    replayed = _idempotent_calendar_reschedule_applied(user_id=uid, idempotency_key=key)
    if replayed:
        refreshed = get_calendar_range(user_id=uid, start_at=date, end_at=date, include_items=True, limit=200)
        return ok_result(
            "calendar_reschedule_idempotent_replay",
            data={"user_id": uid, "target_date": date, "calendar": refreshed.get("data") if isinstance(refreshed.get("data"), dict) else {}},
        )

    insert_events = proposal_data.get("insert_events") if isinstance(proposal_data.get("insert_events"), list) else []
    updates = proposal_data.get("updates") if isinstance(proposal_data.get("updates"), list) else []
    inserted_events: list[dict[str, Any]] = []
    applied_updates: list[dict[str, Any]] = []
    resource_id = 0

    with transaction() as conn:
        _ensure_idempotency_table(conn)
        next_task_id = _next_task_id(conn, user_id=uid, target_date=date)
        for raw in insert_events:
            if not isinstance(raw, dict):
                continue
            start_at = _calendar_datetime(date, raw.get("start_time"))
            end_at = _calendar_datetime(date, raw.get("end_time")) if raw.get("end_time") else None
            content = norm_text(raw.get("content")) or "会议"
            if not start_at:
                continue
            cursor = conn.execute(
                """
                INSERT INTO calendar (
                    user_id, plan_id, date, task_id, start_time, end_time,
                    content, type, source, is_milk_pump, finish, created_at, modified_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, '自定义', '用户输入', 0, 'false', CURRENT_TIMESTAMP, NULL)
                """,
                (uid, date, next_task_id, start_at, end_at, content),
            )
            inserted_id = int(cursor.lastrowid or 0)
            resource_id = resource_id or inserted_id
            next_task_id += 1
            inserted_events.append({"item_id": inserted_id, "start_time": start_at, "end_time": end_at, "content": content, "type": "自定义"})

        for raw in updates:
            if not isinstance(raw, dict):
                continue
            item_id = to_int(raw.get("item_id"), 0)
            new_start = _calendar_datetime(date, raw.get("new_start_time"))
            new_end = _calendar_datetime(date, raw.get("new_end_time")) if raw.get("new_end_time") else None
            if item_id <= 0 or not new_start:
                continue
            cursor = conn.execute(
                """
                UPDATE calendar
                SET start_time = ?,
                    end_time = ?,
                    modified_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                  AND date = ?
                  AND item_id = ?
                  AND finish = 'false'
                """,
                (new_start, new_end, uid, date, item_id),
            )
            if int(cursor.rowcount or 0) > 0:
                resource_id = resource_id or item_id
                applied_updates.append({"item_id": item_id, "new_start_time": new_start, "new_end_time": new_end})

        conn.execute(
            """
            INSERT OR IGNORE INTO tool_idempotency_log(user_id, tool_name, idempotency_key, resource_id, created_at)
            VALUES (?, 'milk_calendar_mutate.apply_reschedule', ?, ?, ?)
            """,
            (uid, key, int(resource_id or 0), _now()),
        )

    refreshed = get_calendar_range(user_id=uid, start_at=date, end_at=date, include_items=True, limit=200)
    return ok_result(
        "calendar_reschedule_applied",
        data={
            "user_id": uid,
            "target_date": date,
            "inserted_events": inserted_events,
            "applied_updates": applied_updates,
            "calendar": refreshed.get("data") if isinstance(refreshed.get("data"), dict) else {},
        },
    )


def update_calendar_range(
    *,
    user_id: str,
    start_at: str,
    end_at: str,
    operation: str,
    patch: dict[str, Any] | str,
    plan_id: int | None = None,
    item_type: str | None = None,
    idempotency_key: str,
) -> ServiceResult:
    _ = idempotency_key
    uid = str(user_id or "").strip()
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法修改计划。")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")
    window = _range_window(start_at, end_at)
    if window.get("error"):
        return error_result("invalid_time_range", window["error"])
    if item_type and item_type not in VALID_CALENDAR_TYPES:
        return error_result("invalid_calendar_type", f"Unsupported calendar type: {item_type}")
    op = norm_text(operation)
    if op not in {"shift", "delete", "patch_items"}:
        return error_result("invalid_operation", f"Unsupported calendar range operation: {operation}")
    patch_data = _parse_json_object(patch)
    if op != "delete" and not patch_data:
        return error_result("empty_patch", "没有可执行的计划修改。")

    start_text = _db_time(window["start_dt"])
    end_text = _db_time(window["end_dt"])
    with transaction() as conn:
        rows = _select_calendar_range_rows(
            conn,
            user_id=uid,
            start_at=start_text,
            end_at=end_text,
            plan_id=plan_id,
            item_type=item_type,
        )
        if op == "shift":
            changed = _shift_calendar_rows(conn, uid, rows, patch_data)
            status = "calendar_range_shifted"
            summary = f"已顺延 {len(changed)} 个计划条目。"
            data: dict[str, Any] = {"changed_items": changed, "updated_count": len(changed)}
        elif op == "delete":
            item_ids = [int(row["item_id"] or 0) for row in rows]
            deleted_count = 0
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                cursor = conn.execute(
                    f"DELETE FROM calendar WHERE user_id = ? AND item_id IN ({placeholders})",
                    [uid, *item_ids],
                )
                deleted_count = int(cursor.rowcount or 0)
            status = "calendar_range_deleted"
            summary = f"已删除 {deleted_count} 个计划条目。"
            data = {"deleted_count": deleted_count, "item_ids": item_ids}
        else:
            changed = _patch_calendar_rows(conn, uid, rows, patch_data)
            status = "calendar_range_patched"
            summary = f"已更新 {len(changed)} 个计划条目。"
            data = {"changed_items": changed, "updated_count": len(changed)}

    refreshed = get_calendar_range(
        user_id=uid,
        start_at=start_text,
        end_at=end_text,
        plan_id=plan_id,
        item_type=item_type,
        include_items=True,
        limit=200,
    )
    data["calendar"] = refreshed.get("data") if isinstance(refreshed.get("data"), dict) else {}
    return ok_result(status, summary, data)


def update_calendar_item(
    *,
    user_id: str,
    item_id: int,
    start_time: str | None = None,
    end_time: str | None = None,
    content: str | None = None,
    item_type: str | None = None,
    finish: bool | None = None,
) -> ServiceResult:
    uid = str(user_id or "").strip()
    if not uid or not item_id:
        return error_result("missing_required_field", "user_id and item_id are required.")
    current = fetch_one(
        """
        SELECT item_id, user_id, date, start_time, end_time, content, type, is_milk_pump, finish
        FROM calendar
        WHERE user_id = ? AND item_id = ?
        """,
        (uid, int(item_id)),
    )
    if not current:
        return error_result("calendar_item_not_found", "Calendar item was not found.")
    item_date = str(current.get("date") or "")

    fields: list[str] = []
    params: list[Any] = []
    if start_time is not None:
        normalized_start = _calendar_datetime(item_date, start_time)
        if not normalized_start:
            return error_result("invalid_datetime", "start_time must be a valid time or datetime.")
        fields.append("start_time = ?")
        params.append(normalized_start)
    if end_time is not None:
        normalized_end = _calendar_datetime(item_date, end_time)
        if not normalized_end:
            return error_result("invalid_datetime", "end_time must be a valid time or datetime.")
        fields.append("end_time = ?")
        params.append(normalized_end)
    if content is not None:
        fields.append("content = ?")
        params.append(str(content))
    if item_type is not None:
        if item_type not in VALID_CALENDAR_TYPES:
            return error_result("invalid_calendar_type", f"Unsupported calendar type: {item_type}")
        fields.append("type = ?")
        params.append(item_type)
    if finish is not None:
        fields.append("finish = ?")
        params.append("true" if finish else "false")
    if not fields:
        return error_result("empty_update", "No calendar item fields were provided.")

    fields.append("modified_at = CURRENT_TIMESTAMP")
    params.extend([uid, int(item_id)])
    execute(
        f"UPDATE calendar SET {', '.join(fields)} WHERE user_id = ? AND item_id = ?",
        params,
    )
    sync_result = {"pumping_id": 0, "feeding_id": 0}
    if finish is True:
        sync_result = data_store.sync_completed_calendar_item_logs(user_id=uid, item_id=int(item_id))
    return ok_result("calendar_item_updated", data={"user_id": uid, "target_date": item_date, "item_id": int(item_id), "synced_logs": sync_result})


def delete_calendar_item(*, user_id: str, item_id: int) -> ServiceResult:
    uid = str(user_id or "").strip()
    if not uid or not item_id:
        return error_result("missing_required_field", "user_id and item_id are required.")
    current = fetch_one(
        """
        SELECT date
        FROM calendar
        WHERE user_id = ? AND item_id = ?
        """,
        (uid, int(item_id)),
    )
    target_date = str(current.get("date") or "") if current else ""
    changed = execute("DELETE FROM calendar WHERE user_id = ? AND item_id = ?", [uid, int(item_id)])
    if changed <= 0:
        return error_result("calendar_item_not_found", "Calendar item was not found.")
    return ok_result("calendar_item_deleted", data={"user_id": uid, "target_date": target_date, "item_id": int(item_id)})


def _fetch_calendar_range_rows(
    *,
    user_id: str,
    start_at: str,
    end_at: str,
    plan_id: int | None,
    item_type: str | None,
) -> list[dict[str, Any]]:
    params: list[Any] = [user_id, start_at, end_at]
    clauses = [
        "user_id = ?",
        "COALESCE(start_time, date || ' 00:00:00') >= ?",
        "COALESCE(start_time, date || ' 00:00:00') < ?",
    ]
    if plan_id is not None:
        clauses.append("COALESCE(plan_id, 0) = ?")
        params.append(int(plan_id))
    if item_type:
        clauses.append("type = ?")
        params.append(item_type)
    return fetch_all(
        f"""
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE {" AND ".join(clauses)}
        ORDER BY date ASC, COALESCE(start_time, ''), COALESCE(task_id, item_id), item_id
        """,
        params,
    )


def _select_calendar_range_rows(
    conn: Any,
    *,
    user_id: str,
    start_at: str,
    end_at: str,
    plan_id: int | None,
    item_type: str | None,
) -> list[Any]:
    params: list[Any] = [user_id, start_at, end_at]
    clauses = [
        "user_id = ?",
        "COALESCE(start_time, date || ' 00:00:00') >= ?",
        "COALESCE(start_time, date || ' 00:00:00') < ?",
    ]
    if plan_id is not None:
        clauses.append("COALESCE(plan_id, 0) = ?")
        params.append(int(plan_id))
    if item_type:
        clauses.append("type = ?")
        params.append(item_type)
    return conn.execute(
        f"""
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE {" AND ".join(clauses)}
        ORDER BY date ASC, COALESCE(start_time, ''), COALESCE(task_id, item_id), item_id
        """,
        params,
    ).fetchall()


def _calendar_range_summary(items: list[CalendarItem]) -> dict[str, Any]:
    completed = len([item for item in items if item.get("finish")])
    by_date: dict[str, dict[str, Any]] = {}
    type_counts: dict[str, int] = {}
    for item in items:
        item_type = norm_text(item.get("type")) or CALENDAR_TYPE_CUSTOM
        type_counts[item_type] = type_counts.get(item_type, 0) + 1
        date = norm_text(item.get("date"))
        if not date:
            continue
        slot = by_date.setdefault(date, {"date": date, "total_count": 0, "completed_count": 0, "pending_count": 0, "type_counts": {}})
        slot["total_count"] += 1
        if item.get("finish"):
            slot["completed_count"] += 1
        slot["type_counts"][item_type] = slot["type_counts"].get(item_type, 0) + 1
    for slot in by_date.values():
        slot["pending_count"] = max(slot["total_count"] - slot["completed_count"], 0)
    total = len(items)
    return {
        "total_count": total,
        "completed_count": completed,
        "pending_count": max(total - completed, 0),
        "completion_rate": int((completed * 100) / total) if total > 0 else 0,
        "type_counts": type_counts,
        "daily": [by_date[key] for key in sorted(by_date)],
    }


def _shift_calendar_rows(conn: Any, user_id: str, rows: list[Any], patch_data: dict[str, Any]) -> list[dict[str, Any]]:
    minutes = to_int(patch_data.get("shift_minutes"), 0)
    if minutes == 0:
        return []
    changed: list[dict[str, Any]] = []
    for row in rows:
        item_id = int(row["item_id"] or 0)
        start_dt = parse_datetime(row["start_time"])
        if item_id <= 0 or start_dt is None:
            continue
        end_dt = parse_datetime(row["end_time"])
        new_start = start_dt + timedelta(minutes=minutes)
        new_end = end_dt + timedelta(minutes=minutes) if end_dt is not None else None
        conn.execute(
            """
            UPDATE calendar
            SET start_time = ?,
                end_time = ?,
                modified_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND item_id = ?
            """,
            (_db_time(new_start), _db_time(new_end) if new_end else None, user_id, item_id),
        )
        changed.append(
            {
                "item_id": item_id,
                "old_start_time": row["start_time"],
                "old_end_time": row["end_time"],
                "new_start_time": _db_time(new_start),
                "new_end_time": _db_time(new_end) if new_end else None,
            }
        )
    return changed


def _patch_calendar_rows(conn: Any, user_id: str, rows: list[Any], patch_data: dict[str, Any]) -> list[dict[str, Any]]:
    updates = patch_data.get("updates")
    if not isinstance(updates, list):
        return []
    rows_by_id = {int(row["item_id"] or 0): row for row in rows}
    changed: list[dict[str, Any]] = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        item_id = to_int(update.get("item_id"), 0)
        row = rows_by_id.get(item_id)
        if row is None:
            continue
        fields: list[str] = []
        params: list[Any] = []
        item_date = str(row["date"] or "")
        if "start_time" in update:
            start_time = _calendar_datetime(item_date, update.get("start_time"))
            if not start_time:
                continue
            fields.append("start_time = ?")
            params.append(start_time)
        if "end_time" in update:
            end_time = _calendar_datetime(item_date, update.get("end_time"))
            if not end_time:
                continue
            fields.append("end_time = ?")
            params.append(end_time)
        if "content" in update:
            fields.append("content = ?")
            params.append(str(update.get("content") or ""))
        next_type = update.get("item_type", update.get("type"))
        if next_type is not None:
            if next_type not in VALID_CALENDAR_TYPES:
                continue
            fields.append("type = ?")
            params.append(str(next_type))
            fields.append("is_milk_pump = ?")
            params.append(1 if next_type == CALENDAR_TYPE_PUMP else 0)
        if not fields:
            continue
        fields.append("modified_at = CURRENT_TIMESTAMP")
        params.extend([user_id, item_id])
        conn.execute(
            f"UPDATE calendar SET {', '.join(fields)} WHERE user_id = ? AND item_id = ?",
            params,
        )
        changed.append({"item_id": item_id, "updated_fields": sorted(set(update.keys()) - {"item_id"})})
    return changed


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _parse_busy_windows(value: Any, *, target_date: str) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for raw in _parse_json_list(value):
        if not isinstance(raw, dict):
            continue
        start_at = _calendar_datetime(target_date, raw.get("start_time"))
        if not start_at:
            continue
        end_at = _calendar_datetime(target_date, raw.get("end_time")) if raw.get("end_time") else ""
        if not end_at and raw.get("duration_minutes") is not None:
            end_at = _add_minutes(start_at, max(to_int(raw.get("duration_minutes"), 0), 1))
        if not end_at:
            continue
        if parse_datetime(end_at) is not None and parse_datetime(start_at) is not None and parse_datetime(end_at) <= parse_datetime(start_at):
            continue
        windows.append(
            {
                "start_time": start_at,
                "end_time": end_at,
                "content": norm_text(raw.get("content")) or "会议",
            }
        )
    windows.sort(key=lambda item: item["start_time"])
    return windows


def _parse_adjustable_types(value: Any) -> set[str]:
    parsed = {norm_text(item) for item in _parse_json_list(value)}
    selected = {item for item in parsed if item in {CALENDAR_TYPE_PUMP, CALENDAR_TYPE_NURSING}}
    return selected or {CALENDAR_TYPE_PUMP, CALENDAR_TYPE_NURSING}


def _is_pending_adjustable_item(item: CalendarItem, adjustable_types: set[str]) -> bool:
    return not to_bool(item.get("finish")) and norm_text(item.get("type")) in adjustable_types


def _fixed_blocks_for_reschedule(items: list[CalendarItem], adjustable_types: set[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for item in items:
        item_type = norm_text(item.get("type"))
        if item_type in adjustable_types and not to_bool(item.get("finish")):
            continue
        start = parse_minute_of_day(item.get("start_time") or item.get("time"))
        if start is None:
            continue
        end = parse_minute_of_day(item.get("end_time"))
        if end is None:
            end = start + DEFAULT_RESCHEDULE_DURATION_MINUTES
        blocks.append(
            {
                "start_time": format_hhmm(start),
                "end_time": format_hhmm(end),
                "content": norm_text(item.get("content")) or item_type or "固定日程",
                "source_item": dict(item),
            }
        )
    return blocks


def _busy_blocks_for_reschedule(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "start_time": item["start_time"],
            "end_time": item["end_time"],
            "content": norm_text(item.get("content")) or "会议",
            "source_item": dict(item),
        }
        for item in windows
    ]


def _insert_events_for_reschedule(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "start_time": item["start_time"],
            "end_time": item["end_time"],
            "content": norm_text(item.get("content")) or "会议",
            "type": CALENDAR_TYPE_CUSTOM,
            "source": "用户输入",
            "is_milk_pump": False,
        }
        for item in windows
    ]


def _reschedule_updates(*, date: str, original_items: list[CalendarItem], adjusted_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    adjusted_by_id = {to_int(item.get("item_id"), 0): item for item in adjusted_items}
    updates: list[dict[str, Any]] = []
    for original in original_items:
        item_id = to_int(original.get("item_id"), 0)
        adjusted = adjusted_by_id.get(item_id)
        if item_id <= 0 or adjusted is None:
            continue
        old_start = hhmm(original.get("start_time"))
        new_start = hhmm(adjusted.get("start_time"))
        if not new_start or new_start == old_start:
            continue
        old_end = hhmm(original.get("end_time"))
        new_end = hhmm(adjusted.get("end_time"))
        updates.append(
            {
                "item_id": item_id,
                "task_id": original.get("task_id"),
                "content": original.get("content"),
                "type": original.get("type"),
                "old_start_time": _calendar_datetime(date, old_start),
                "old_end_time": _calendar_datetime(date, old_end) if old_end else None,
                "new_start_time": _calendar_datetime(date, new_start),
                "new_end_time": _calendar_datetime(date, new_end) if new_end else None,
            }
        )
    return updates


def _reschedule_conflicts(
    *,
    windows: list[dict[str, Any]],
    adjustable_items: list[CalendarItem],
    duration_minutes: int,
) -> list[dict[str, Any]]:
    ranges = [
        (parse_minute_of_day(window.get("start_time")), parse_minute_of_day(window.get("end_time")), norm_text(window.get("content")) or "会议")
        for window in windows
    ]
    conflicts: list[dict[str, Any]] = []
    fallback_duration = max(to_int(duration_minutes, DEFAULT_RESCHEDULE_DURATION_MINUTES), 1)
    for item in adjustable_items:
        start = parse_minute_of_day(item.get("start_time"))
        if start is None:
            continue
        end = parse_minute_of_day(item.get("end_time"))
        if end is None:
            end = start + fallback_duration
        for busy_start, busy_end, content in ranges:
            if busy_start is None or busy_end is None:
                continue
            if start < busy_end and end > busy_start:
                conflicts.append(
                    {
                        "item_id": item.get("item_id"),
                        "task_id": item.get("task_id"),
                        "start_time": hhmm(item.get("start_time")),
                        "end_time": hhmm(item.get("end_time")),
                        "content": item.get("content"),
                        "conflict_with": content,
                    }
                )
                break
    return conflicts


def _reschedule_summary(*, windows: list[dict[str, Any]], conflicts: list[dict[str, Any]], updates: list[dict[str, Any]]) -> str:
    if not windows:
        return "没有识别到可用于重排的日程。"
    if not conflicts and not updates:
        return f"识别到 {len(windows)} 个不可用时间段，但没有影响当前吸奶/亲喂计划。"
    return f"识别到 {len(windows)} 个不可用时间段，发现 {len(conflicts)} 个冲突，建议调整 {len(updates)} 个吸奶/亲喂任务。"


def _next_task_id(conn: Any, *, user_id: str, target_date: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(task_id), 0) AS max_task_id FROM calendar WHERE user_id = ? AND date = ?",
        (user_id, target_date),
    ).fetchone()
    return int(row["max_task_id"] or 0) + 1


def _range_window(start_at: Any, end_at: Any) -> dict[str, Any]:
    start_dt = parse_datetime(start_at)
    end_dt = parse_datetime(end_at)
    if start_dt is None or end_dt is None:
        return {"error": "start_at 和 end_at 必须是有效日期或日期时间。"}
    if _is_date_only(end_at):
        end_dt = end_dt + timedelta(days=1)
    if end_dt <= start_dt:
        return {"error": "end_at 必须晚于 start_at。"}
    return {
        "start_dt": start_dt,
        "end_dt": end_dt,
        "start_text": _db_time(start_dt),
        "end_text": _db_time(end_dt),
    }


def _is_date_only(value: Any) -> bool:
    token = norm_text(value)
    return len(token) == 10 and token[4:5] in {"-", "/"} and token[7:8] in {"-", "/"}


def _calendar_plan_context(
    user_id: str,
    items: list[CalendarItem],
    *,
    target_date: str | None = None,
    window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan_counts = _plan_task_counts(items)
    plan_ids = [int(plan_id) for plan_id in plan_counts]
    plans_by_id = _fetch_plan_summaries(user_id, plan_ids)
    plans = [plans_by_id[plan_id] for plan_id in plan_ids if plan_id in plans_by_id]
    primary_plan_id = _primary_plan_id(items, plan_ids)
    primary_plan = plans_by_id.get(primary_plan_id) if primary_plan_id is not None else None
    inferred_count = _inferred_plan_task_count(items)
    inferred_plan = _inferred_calendar_plan_summary(items, inferred_count) if primary_plan is None and inferred_count > 0 else None
    return {
        "source": "calendar_plan_id" if plan_ids else ("calendar_inferred_schedule" if inferred_plan else "calendar"),
        "policy": "按所查日期或日期范围内 calendar 任务判断当前计划；优先使用关联 plan_id，旧数据或演示数据缺少 plan_id 时按系统生成的吸奶/亲喂任务推断，不按最新保存计划排序判断。",
        "target_date": target_date,
        "window": window,
        "has_plan_tasks": bool(plan_ids) or inferred_count > 0,
        "plan_ids": plan_ids,
        "primary_plan_id": primary_plan_id,
        "primary_plan": primary_plan or inferred_plan,
        "plans": plans,
        "task_count_by_plan_id": {str(plan_id): count for plan_id, count in plan_counts.items()},
        "task_count_without_plan_id": inferred_count,
        "inferred_from_calendar": bool(inferred_plan),
        "multiple_plans": len(plan_ids) > 1,
    }


def _plan_task_counts(items: list[CalendarItem]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for item in items:
        plan_id = _item_plan_id(item)
        if plan_id is None:
            continue
        counts[plan_id] = counts.get(plan_id, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: pair[0]))


def _item_plan_id(item: dict[str, Any]) -> int | None:
    try:
        plan_id = int(item.get("plan_id") or 0)
    except (TypeError, ValueError):
        return None
    return plan_id if plan_id > 0 else None


def _is_calendar_plan_task(item: dict[str, Any]) -> bool:
    if _item_plan_id(item) is not None:
        return True
    return _is_inferred_plan_task(item)


def _is_inferred_plan_task(item: dict[str, Any]) -> bool:
    item_type = norm_text(item.get("type"))
    source = norm_text(item.get("source"))
    if item_type not in {CALENDAR_TYPE_PUMP, CALENDAR_TYPE_NURSING}:
        return False
    if source == "用户输入":
        return False
    return bool(item.get("is_milk_pump") or item_type == CALENDAR_TYPE_NURSING)


def _inferred_plan_task_count(items: list[CalendarItem]) -> int:
    return sum(1 for item in items if _item_plan_id(item) is None and _is_inferred_plan_task(item))


def _inferred_calendar_plan_summary(items: list[CalendarItem], task_count: int) -> dict[str, Any] | None:
    if task_count <= 0:
        return None
    milk_types = {norm_text(item.get("type")) for item in items if _is_inferred_plan_task(item)}
    if milk_types == {CALENDAR_TYPE_PUMP}:
        plan_summary = "日历中已有系统生成的吸奶任务，按稳奶日程执行。"
    elif milk_types == {CALENDAR_TYPE_NURSING}:
        plan_summary = "日历中已有系统生成的亲喂任务，按稳奶日程执行。"
    else:
        plan_summary = "日历中已有系统生成的吸奶/亲喂任务，按稳奶日程执行。"
    return {
        "plan_id": None,
        "plan_name": "稳奶计划",
        "plan_type": "maintain_milk",
        "plan_days": None,
        "plan_summary": plan_summary,
        "milestone_summary": None,
        "milestone_list": [],
        "created_at": None,
        "updated_at": None,
        "inferred_from_calendar": True,
    }


def _primary_plan_id(items: list[CalendarItem], plan_ids: list[int]) -> int | None:
    if not plan_ids:
        return None
    ordered = sorted(
        (item for item in items if _item_plan_id(item) is not None),
        key=lambda item: (norm_text(item.get("date")), norm_text(item.get("start_time")), to_int(item.get("task_id"), 0), to_int(item.get("item_id"), 0)),
    )
    if ordered:
        return _item_plan_id(ordered[0])
    return plan_ids[0]


def _first_item_date(items: list[CalendarItem]) -> str:
    dates = sorted({norm_text(item.get("date")) for item in items if norm_text(item.get("date"))})
    return dates[0] if dates else ""


def _fetch_plan_summaries(user_id: str, plan_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not plan_ids:
        return {}
    placeholders = ", ".join(["?"] * len(plan_ids))
    rows = fetch_all(
        f"""
        SELECT plan_id, user_id, plan_name, plan_type, plan_days, plan_summary,
               milestone_summary, milestone_list, created_at, updated_at
        FROM milk_plan
        WHERE user_id = ?
          AND plan_id IN ({placeholders})
        ORDER BY plan_id ASC
        """,
        [user_id, *plan_ids],
    )
    return {int(row["plan_id"]): _normalize_plan_summary_row(row) for row in rows if row.get("plan_id") is not None}


def _normalize_plan_summary_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    milestone_list = norm_text(result.get("milestone_list"))
    if milestone_list:
        try:
            result["milestone_list"] = json.loads(milestone_list)
        except json.JSONDecodeError:
            result["milestone_list"] = []
    else:
        result["milestone_list"] = []
    return result


def _normalize_calendar_row(row: dict[str, Any]) -> CalendarItem:
    item: CalendarItem = {
        "item_id": int(row.get("item_id") or 0),
        "user_id": str(row.get("user_id") or ""),
        "plan_id": row.get("plan_id"),
        "date": str(row.get("date") or ""),
        "task_id": row.get("task_id"),
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "content": row.get("content"),
        "type": row.get("type") or CALENDAR_TYPE_CUSTOM,
        "source": row.get("source") or "系统生成",
        "is_milk_pump": to_bool(row.get("is_milk_pump")),
        "finish": to_bool(row.get("finish")),
        "created_at": row.get("created_at"),
        "modified_at": row.get("modified_at"),
        "time": hhmm(row.get("start_time")),
        "time_point": hhmm(row.get("start_time")),
        }
    return item


def _calendar_conflicts(
    *,
    user_id: str,
    target_date: str,
    start_at: str,
    end_at: str,
    plan_id: int | None,
) -> list[CalendarItem]:
    rows = fetch_all(
        """
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE user_id = ?
          AND date = ?
          AND (? IS NULL OR COALESCE(plan_id, 0) = COALESCE(?, 0))
          AND start_time < ?
          AND COALESCE(end_time, start_time) > ?
        ORDER BY start_time ASC, task_id ASC, item_id ASC
        """,
        (user_id, target_date, plan_id, plan_id, end_at, start_at),
    )
    return [_normalize_calendar_row(row) for row in rows]


def _build_adjustments_for_event(*, conflicts: list[CalendarItem], event_end_time: str) -> list[dict[str, Any]]:
    next_start = parse_datetime(event_end_time)
    if next_start is None:
        return []
    updates: list[dict[str, Any]] = []
    for item in conflicts:
        if not _is_milk_schedule_item(item):
            continue
        old_start = parse_datetime(item.get("start_time"))
        old_end = parse_datetime(item.get("end_time"))
        duration = int((old_end - old_start).total_seconds() / 60) if old_start is not None and old_end is not None else 15
        duration = max(duration, 1)
        new_start = next_start
        new_end = new_start + timedelta(minutes=duration)
        updates.append(
            {
                "item_id": item.get("item_id"),
                "task_id": item.get("task_id"),
                "content": item.get("content"),
                "type": item.get("type"),
                "old_start_time": item.get("start_time"),
                "old_end_time": item.get("end_time"),
                "new_start_time": _db_time(new_start),
                "new_end_time": _db_time(new_end),
            }
        )
        next_start = new_end
    return updates


def _resolve_calendar_type(item_type: Any, content: Any) -> str:
    token = norm_text(item_type)
    if token in VALID_CALENDAR_TYPES:
        return token
    text = norm_text(content).lower()
    if "亲喂" in text or "nursing" in text or "breastfeeding" in text:
        return CALENDAR_TYPE_NURSING
    if "吸奶" in text or "排奶" in text or "泵奶" in text or "pump" in text:
        return CALENDAR_TYPE_PUMP
    return CALENDAR_TYPE_CUSTOM


def _is_milk_schedule_item(item: CalendarItem) -> bool:
    item_type = item.get("type")
    return bool(item.get("is_milk_pump")) or item_type in {CALENDAR_TYPE_PUMP, CALENDAR_TYPE_NURSING}


def _calendar_datetime(target_date: str, value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    date = _date_text(target_date)
    if not date:
        return ""
    if _has_date(value):
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    return f"{date} {parsed.hour:02d}:{parsed.minute:02d}:00"


def _date_text(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.date().isoformat() if parsed is not None else norm_text(value)[:10]


def _has_date(value: Any) -> bool:
    token = norm_text(value)
    return len(token) >= 10 and token[4:5] in {"-", "/"} and token[7:8] in {"-", "/"}


def _add_minutes(value: Any, minutes: int) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return (parsed + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _db_time(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _idempotent_calendar_adjustment_item_id(*, user_id: str, idempotency_key: str) -> int:
    row = fetch_one(
        """
        SELECT resource_id
        FROM tool_idempotency_log
        WHERE user_id = ?
          AND tool_name = 'milk_calendar_mutate.apply_adjustment'
          AND idempotency_key = ?
        LIMIT 1
        """,
        (user_id, idempotency_key),
    )
    return to_int(row.get("resource_id"), 0) if row else 0


def _idempotent_calendar_reschedule_applied(*, user_id: str, idempotency_key: str) -> bool:
    row = fetch_one(
        """
        SELECT resource_id
        FROM tool_idempotency_log
        WHERE user_id = ?
          AND tool_name = 'milk_calendar_mutate.apply_reschedule'
          AND idempotency_key = ?
        LIMIT 1
        """,
        (user_id, idempotency_key),
    )
    return row is not None


def _calendar_item_by_id(item_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
               content, type, source, is_milk_pump, finish, created_at, modified_at
        FROM calendar
        WHERE item_id = ?
        """,
        (int(item_id),),
    )


def _ensure_idempotency_table(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_idempotency_log (
            user_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            resource_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id, tool_name, idempotency_key)
        )
        """
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
