from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import data_store
from .records import (
    RECORD_KIND_BREASTMILK_BOTTLE,
    RECORD_KIND_FORMULA_BOTTLE,
    RECORD_KIND_NURSING,
    RECORD_KIND_PUMPING,
)
from .schemas import (
    CALENDAR_TYPE_CUSTOM,
    CALENDAR_TYPE_NURSING,
    CALENDAR_TYPE_PUMP,
    ServiceResult,
    error_result,
    hhmm,
    norm_text,
    ok_result,
    parse_datetime,
    to_bool,
    to_int,
)

RECORD_KIND_NONE = "none"
TASK_OPERATIONS = {"complete", "cancel_complete", "skip"}
TASK_RECORD_KINDS = {
    RECORD_KIND_PUMPING,
    RECORD_KIND_NURSING,
    RECORD_KIND_BREASTMILK_BOTTLE,
    RECORD_KIND_FORMULA_BOTTLE,
    RECORD_KIND_NONE,
}


def complete_milk_task(
    *,
    user_id: str,
    operation: str,
    target_date: str | None = None,
    task_id: int | None = None,
    item_id: int | None = None,
    record_kind: str | None = None,
    amount_ml: float | None = None,
    duration_minutes: int | None = None,
    occurred_at: str | None = None,
    title: str | None = None,
    delete_linked_record: bool = True,
    idempotency_key: str = "",
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法更新任务完成状态。")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")

    op = norm_text(operation)
    if op not in TASK_OPERATIONS:
        return error_result("invalid_task_operation", f"Unsupported milk_task_complete operation: {operation}")

    date_text = _date_text(target_date)
    task_key = {
        "item_id": to_int(item_id, 0) if item_id is not None else 0,
        "task_id": to_int(task_id, 0) if task_id is not None else 0,
        "target_date": date_text,
    }
    if task_key["item_id"] <= 0 and task_key["task_id"] <= 0:
        return error_result("missing_task_identifier", "需要 item_id，或 target_date + task_id 来定位任务。")

    requested_kind = _record_kind(record_kind)
    if requested_kind is None and norm_text(record_kind):
        return error_result("invalid_record_kind", f"Unsupported record_kind: {record_kind}")
    explicit_record_kind = requested_kind is not None

    event_time_error = ""
    record_changes: list[dict[str, Any]] = []
    warnings: list[str] = []
    with _transaction() as conn:
        task = _load_task(conn, uid, item_id=task_key["item_id"], target_date=task_key["target_date"], task_id=task_key["task_id"])
        if not task:
            return error_result("calendar_task_not_found", "未找到对应的计划任务。", data={"task": task_key})

        event_time = _event_time(occurred_at, task)
        if not event_time:
            event_time_error = "发生时间无效，无法同步记录。"
            event_time = norm_text(task.get("start_time"))
        provided_duration = _optional_int(duration_minutes)
        task_title = norm_text(title) or norm_text(task.get("content"))
        effective_kind = requested_kind or _infer_record_kind(task)

        if op == "complete":
            if not event_time:
                warnings.append(event_time_error or "任务缺少开始时间，本次只更新完成状态。")
            elif effective_kind == RECORD_KIND_NONE:
                warnings.append("该任务没有可同步的吸奶/喂养记录类型，本次只更新完成状态。")
            else:
                created = _ensure_completion_record(
                    conn,
                    user_id=uid,
                    task=task,
                    record_kind=effective_kind,
                    event_time=event_time,
                    title=task_title,
                    amount_ml=_optional_float(amount_ml),
                    duration_minutes=provided_duration,
                )
                record_changes.extend(created["changes"])
                warnings.extend(created["warnings"])
            _set_task_finish(conn, uid, int(task["item_id"]), "true")
            status = "milk_task_completed"
            summary = "计划任务已标记完成。"

        elif op == "cancel_complete":
            if delete_linked_record and event_time:
                record_changes.extend(
                    _delete_linked_records(
                        conn,
                        user_id=uid,
                        task=task,
                        record_kind=effective_kind,
                        explicit_record_kind=explicit_record_kind,
                        event_time=event_time,
                        title=task_title,
                    )
                )
            _set_task_finish(conn, uid, int(task["item_id"]), "false")
            status = "milk_task_completion_cancelled"
            summary = "计划任务已取消完成。"

        else:
            if delete_linked_record and event_time:
                record_changes.extend(
                    _delete_linked_records(
                        conn,
                        user_id=uid,
                        task=task,
                        record_kind=effective_kind,
                        explicit_record_kind=explicit_record_kind,
                        event_time=event_time,
                        title=task_title,
                    )
                )
            _set_task_finish(conn, uid, int(task["item_id"]), "jump")
            status = "milk_task_skipped"
            summary = "计划任务已标记跳过。"

        updated = _load_task(conn, uid, item_id=int(task["item_id"]), target_date="", task_id=0) or task

    task_list = data_store.query_plan_tasks(user_id=uid, target_date=str(updated.get("date") or task_key["target_date"])) or {}
    return ok_result(
        status,
        summary,
        {
            "task": _normalize_task(updated),
            "task_list": task_list.get("task_list", []),
            "plan_type": task_list.get("plan_type", "None"),
            "record_changes": record_changes,
            "warnings": warnings,
        },
    )


def _ensure_completion_record(
    conn: Any,
    *,
    user_id: str,
    task: dict[str, Any],
    record_kind: str,
    event_time: str,
    title: str,
    amount_ml: float | None,
    duration_minutes: int | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    changes: list[dict[str, Any]] = []
    if record_kind == RECORD_KIND_PUMPING:
        if amount_ml is None:
            warnings.append("未提供吸奶量，本次仅更新任务完成状态。")
            return {"changes": changes, "warnings": warnings}
        pumping_id, action = _ensure_pumping_log(
            conn,
            user_id=user_id,
            pump_time=event_time,
            pump_end_time=_end_time(event_time, task, duration_minutes),
            pump_type=0,
            milk_ml=amount_ml,
            duration_minutes=duration_minutes,
            title=title,
        )
        changes.append({"record_kind": record_kind, "action": action, "pumping_id": pumping_id})
        return {"changes": changes, "warnings": warnings}

    infant_id = _resolve_infant_id(conn, user_id)
    if infant_id <= 0:
        warnings.append("未找到宝宝档案，无法同步喂养记录；已仅更新任务完成状态。")
        return {"changes": changes, "warnings": warnings}

    if record_kind == RECORD_KIND_NURSING:
        feed_value = duration_minutes if duration_minutes is not None else amount_ml
        if feed_value is None:
            warnings.append("未提供亲喂时长，本次仅更新任务完成状态。")
            return {"changes": changes, "warnings": warnings}
        feeding_id, feeding_action = _ensure_feeding_log(
            conn,
            user_id=user_id,
            infant_id=infant_id,
            feed_time=event_time,
            feed_type="亲喂",
            feed_value=float(feed_value),
            title=title,
        )
        pumping_id, pumping_action = _ensure_pumping_log(
            conn,
            user_id=user_id,
            pump_time=event_time,
            pump_end_time=_end_time(event_time, task, duration_minutes),
            pump_type=2,
            milk_ml=None,
            duration_minutes=duration_minutes,
            title=title,
        )
        changes.append({"record_kind": record_kind, "action": feeding_action, "feeding_id": feeding_id})
        changes.append({"record_kind": "nursing_output_sync", "action": pumping_action, "pumping_id": pumping_id})
        return {"changes": changes, "warnings": warnings}

    if amount_ml is None:
        warnings.append("未提供瓶喂奶量，本次仅更新任务完成状态。")
        return {"changes": changes, "warnings": warnings}
    feed_type = "瓶喂母乳" if record_kind == RECORD_KIND_BREASTMILK_BOTTLE else "配方奶"
    feeding_id, action = _ensure_feeding_log(
        conn,
        user_id=user_id,
        infant_id=infant_id,
        feed_time=event_time,
        feed_type=feed_type,
        feed_value=float(amount_ml),
        title=title,
    )
    changes.append({"record_kind": record_kind, "action": action, "feeding_id": feeding_id})
    return {"changes": changes, "warnings": warnings}


def _ensure_pumping_log(
    conn: Any,
    *,
    user_id: str,
    pump_time: str,
    pump_end_time: str,
    pump_type: int,
    milk_ml: float | None,
    duration_minutes: int | None,
    title: str,
) -> tuple[int, str]:
    row = conn.execute(
        """
        SELECT pumping_id, pump_milk_volum, pump_milk_duration
        FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time = ?
          AND pump_type = ?
          AND pump_source = 2
          AND COALESCE(pump_title, '') = ?
        ORDER BY pumping_id DESC
        LIMIT 1
        """,
        (user_id, pump_time, int(pump_type), title),
    ).fetchone()
    if row:
        fields: list[str] = []
        params: list[Any] = []
        if milk_ml is not None:
            fields.append("pump_milk_volum = ?")
            params.append(float(milk_ml))
        if duration_minutes is not None:
            fields.append("pump_milk_duration = ?")
            params.append(int(duration_minutes))
            fields.append("pump_end_time = ?")
            params.append(pump_end_time)
        if fields:
            params.extend([user_id, int(row["pumping_id"])])
            conn.execute(f"UPDATE pumping_log SET {', '.join(fields)} WHERE user_id = ? AND pumping_id = ?", params)
            return int(row["pumping_id"] or 0), "updated"
        return int(row["pumping_id"] or 0), "existing"

    cursor = conn.execute(
        """
        INSERT INTO pumping_log(user_id, pump_start_time, pump_end_time, pump_milk_volum,
                                pump_type, pump_milk_duration, pump_source, pump_title, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 2, ?, CURRENT_TIMESTAMP)
        """,
        (user_id, pump_time, pump_end_time or pump_time, milk_ml, int(pump_type), duration_minutes, title),
    )
    return int(cursor.lastrowid or 0), "created"


def _ensure_feeding_log(
    conn: Any,
    *,
    user_id: str,
    infant_id: int,
    feed_time: str,
    feed_type: str,
    feed_value: float,
    title: str,
) -> tuple[int, str]:
    row = conn.execute(
        """
        SELECT feeding_id, feed_milk_volum
        FROM feeding_log
        WHERE user_id = ?
          AND infant_id = ?
          AND feed_time = ?
          AND feed_type = ?
          AND feed_action = 1
          AND COALESCE(feeding_title, '') = ?
        ORDER BY feeding_id DESC
        LIMIT 1
        """,
        (user_id, int(infant_id), feed_time, feed_type, title),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE feeding_log SET feed_milk_volum = ? WHERE user_id = ? AND feeding_id = ?",
            (float(feed_value), user_id, int(row["feeding_id"])),
        )
        return int(row["feeding_id"] or 0), "updated"

    cursor = conn.execute(
        """
        INSERT INTO feeding_log(user_id, infant_id, feed_time, feed_type, feed_milk_volum,
                                feed_action, feeding_title, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
        """,
        (user_id, int(infant_id), feed_time, feed_type, float(feed_value), title),
    )
    return int(cursor.lastrowid or 0), "created"


def _delete_linked_records(
    conn: Any,
    *,
    user_id: str,
    task: dict[str, Any],
    record_kind: str,
    explicit_record_kind: bool,
    event_time: str,
    title: str,
) -> list[dict[str, Any]]:
    kinds = _delete_kinds(record_kind, task, explicit_record_kind=explicit_record_kind)
    deleted: list[dict[str, Any]] = []
    if RECORD_KIND_PUMPING in kinds:
        deleted.extend(_delete_pumping_logs(conn, user_id=user_id, pump_time=event_time, pump_type=0, title=title))
    if RECORD_KIND_NURSING in kinds:
        deleted.extend(_delete_feeding_logs(conn, user_id=user_id, feed_time=event_time, feed_type="亲喂", title=title, record_kind=RECORD_KIND_NURSING))
        deleted.extend(_delete_pumping_logs(conn, user_id=user_id, pump_time=event_time, pump_type=2, title=title))
    if RECORD_KIND_BREASTMILK_BOTTLE in kinds:
        deleted.extend(
            _delete_feeding_logs(
                conn,
                user_id=user_id,
                feed_time=event_time,
                feed_type="瓶喂母乳",
                title=title,
                record_kind=RECORD_KIND_BREASTMILK_BOTTLE,
            )
        )
    if RECORD_KIND_FORMULA_BOTTLE in kinds:
        deleted.extend(
            _delete_feeding_logs(
                conn,
                user_id=user_id,
                feed_time=event_time,
                feed_type="配方奶",
                title=title,
                record_kind=RECORD_KIND_FORMULA_BOTTLE,
            )
        )
    return deleted


def _delete_pumping_logs(conn: Any, *, user_id: str, pump_time: str, pump_type: int, title: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT pumping_id
        FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time = ?
          AND pump_type = ?
          AND pump_source = 2
          AND COALESCE(pump_title, '') = ?
        """,
        (user_id, pump_time, int(pump_type), title),
    ).fetchall()
    ids = [int(row["pumping_id"] or 0) for row in rows]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM pumping_log WHERE user_id = ? AND pumping_id IN ({placeholders})", [user_id, *ids])
    return [{"record_kind": RECORD_KIND_PUMPING if pump_type == 0 else "nursing_output_sync", "action": "deleted", "pumping_id": rid} for rid in ids]


def _delete_feeding_logs(
    conn: Any,
    *,
    user_id: str,
    feed_time: str,
    feed_type: str,
    title: str,
    record_kind: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT feeding_id
        FROM feeding_log
        WHERE user_id = ?
          AND feed_time = ?
          AND feed_type = ?
          AND feed_action = 1
          AND COALESCE(feeding_title, '') = ?
        """,
        (user_id, feed_time, feed_type, title),
    ).fetchall()
    ids = [int(row["feeding_id"] or 0) for row in rows]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM feeding_log WHERE user_id = ? AND feeding_id IN ({placeholders})", [user_id, *ids])
    return [{"record_kind": record_kind, "action": "deleted", "feeding_id": rid} for rid in ids]


def _load_task(conn: Any, user_id: str, *, item_id: int, target_date: str, task_id: int) -> dict[str, Any] | None:
    if item_id > 0:
        row = conn.execute(
            """
            SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
                   content, type, source, is_milk_pump, finish, created_at, modified_at
            FROM calendar
            WHERE user_id = ? AND item_id = ?
            """,
            (user_id, int(item_id)),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
                   content, type, source, is_milk_pump, finish, created_at, modified_at
            FROM calendar
            WHERE user_id = ? AND date = ? AND task_id = ?
            ORDER BY item_id ASC
            LIMIT 1
            """,
            (user_id, target_date, int(task_id)),
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def _set_task_finish(conn: Any, user_id: str, item_id: int, finish: str) -> None:
    conn.execute(
        """
        UPDATE calendar
        SET finish = ?,
            modified_at = CURRENT_TIMESTAMP
        WHERE user_id = ? AND item_id = ?
        """,
        (finish, user_id, int(item_id)),
    )


def _normalize_task(task: dict[str, Any]) -> dict[str, Any]:
    finish = _api_task_done(task.get("finish"))
    item_type = norm_text(task.get("type")) or CALENDAR_TYPE_CUSTOM
    return {
        "item_id": int(task.get("item_id") or 0),
        "task_id": int(task.get("task_id") or task.get("item_id") or 0),
        "date": norm_text(task.get("date")),
        "task_time": hhmm(task.get("start_time")),
        "task_content": norm_text(task.get("content")),
        "task_type": _api_task_type(item_type, task.get("is_milk_pump")),
        "task_source": "Mai" if norm_text(task.get("source")) == "系统生成" else "手动",
        "task_done": finish,
        "finish": finish,
        "start_time": task.get("start_time"),
        "end_time": task.get("end_time"),
        "type": item_type,
    }


def _record_kind(value: Any) -> str | None:
    token = norm_text(value)
    if not token:
        return None
    return token if token in TASK_RECORD_KINDS else None


def _infer_record_kind(task: dict[str, Any]) -> str:
    item_type = norm_text(task.get("type"))
    content = norm_text(task.get("content")).lower()
    if to_bool(task.get("is_milk_pump")) or item_type == CALENDAR_TYPE_PUMP:
        return RECORD_KIND_PUMPING
    if item_type == CALENDAR_TYPE_NURSING or "亲喂" in content or "nursing" in content or "breastfeeding" in content:
        return RECORD_KIND_NURSING
    if "配方" in content or "奶粉" in content or "formula" in content:
        return RECORD_KIND_FORMULA_BOTTLE
    if "瓶喂母乳" in content or "母乳瓶喂" in content or "breastmilk" in content:
        return RECORD_KIND_BREASTMILK_BOTTLE
    return RECORD_KIND_NONE


def _delete_kinds(record_kind: str, task: dict[str, Any], *, explicit_record_kind: bool) -> set[str]:
    if record_kind != RECORD_KIND_NONE:
        return {record_kind}
    if explicit_record_kind:
        return set()
    inferred = _infer_record_kind(task)
    if inferred != RECORD_KIND_NONE:
        return {inferred}
    return {RECORD_KIND_PUMPING, RECORD_KIND_NURSING, RECORD_KIND_BREASTMILK_BOTTLE, RECORD_KIND_FORMULA_BOTTLE}


def _resolve_infant_id(conn: Any, user_id: str) -> int:
    row = conn.execute(
        "SELECT infant_id FROM infant_profile WHERE user_id = ? ORDER BY infant_id ASC LIMIT 1",
        (user_id,),
    ).fetchone()
    return int(row["infant_id"] or 0) if row else 0


def _event_time(value: Any, task: dict[str, Any]) -> str:
    if value:
        parsed = parse_datetime(value)
        if parsed is None:
            return ""
        if _has_date(value):
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        date = norm_text(task.get("date")) or datetime.now().date().isoformat()
        return f"{date} {parsed.hour:02d}:{parsed.minute:02d}:00"
    start_time = norm_text(task.get("start_time"))
    if start_time:
        return start_time
    date = norm_text(task.get("date"))
    return f"{date} 00:00:00" if date else ""


def _end_time(event_time: str, task: dict[str, Any], duration_minutes: int | None) -> str:
    if duration_minutes is not None and duration_minutes > 0:
        parsed = parse_datetime(event_time)
        if parsed is not None:
            return (parsed + timedelta(minutes=duration_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    return norm_text(task.get("end_time")) or event_time


def _duration_minutes(value: Any, task: dict[str, Any]) -> int | None:
    provided = _optional_int(value)
    if provided is not None:
        return provided
    start = parse_datetime(task.get("start_time"))
    end = parse_datetime(task.get("end_time"))
    if start is None or end is None:
        return None
    minutes = int((end - start).total_seconds() / 60)
    return minutes if minutes > 0 else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _date_text(value: Any) -> str:
    parsed = parse_datetime(value) if value else datetime.now()
    if parsed is None:
        token = norm_text(value)
        return token[:10] if token else datetime.now().date().isoformat()
    return parsed.date().isoformat()


def _has_date(value: Any) -> bool:
    token = norm_text(value)
    return len(token) >= 10 and token[4:5] in {"-", "/"} and token[7:8] in {"-", "/"}


def _api_task_type(raw_type: Any, is_milk_pump: Any) -> int:
    token = norm_text(raw_type)
    if to_bool(is_milk_pump) or token == CALENDAR_TYPE_PUMP:
        return 0
    if token == CALENDAR_TYPE_NURSING:
        return 1
    return 2


def _api_task_done(raw_finish: Any) -> str:
    token = norm_text(raw_finish).lower()
    if token == "jump":
        return "jump"
    if token in {"1", "true", "yes", "done", "completed"}:
        return "true"
    return "false"


def _transaction() -> Any:
    from .db import transaction

    return transaction()
