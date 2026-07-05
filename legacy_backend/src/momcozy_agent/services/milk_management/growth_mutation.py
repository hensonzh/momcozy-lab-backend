from __future__ import annotations

from datetime import datetime
from typing import Any

from .db import fetch_all, fetch_one, transaction
from .schemas import ServiceResult, error_result, norm_text, ok_result, parse_datetime, to_int


def mutate_infant_growth(
    *,
    user_id: str,
    operation: str,
    growth_id: int | None = None,
    infant_id: int | None = None,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    head_cm: float | None = None,
    target_date: str | None = None,
    history_limit: int = 10,
    idempotency_key: str = "",
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法更新宝宝生长记录。")
    idem_key = norm_text(idempotency_key)
    if not idem_key:
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")

    op = norm_text(operation)
    if op not in {"create", "update", "upsert_today"}:
        return error_result("invalid_growth_operation", f"Unsupported infant_growth_mutate operation: {operation}")

    limit = min(max(to_int(history_limit, 10), 1), 50)
    replay_growth_id = _idempotency_growth_id(uid, idem_key)
    if replay_growth_id > 0:
        return _growth_result(uid, replay_growth_id, "infant_growth_idempotent_replay", "宝宝生长记录已处理过，本次返回已有结果。", limit)
    measured_at = _measurement_time(target_date)
    values = {
        "height_cm": _optional_float(height_cm),
        "weight_kg": _optional_float(weight_kg),
        "head_cm": _optional_float(head_cm),
    }

    if op == "create":
        resolved_infant_id = _resolve_infant_id(uid, infant_id)
        if resolved_infant_id <= 0:
            return error_result("infant_profile_not_found", "未找到宝宝档案，无法新增生长记录。")
        missing = [key for key, value in values.items() if value is None]
        if missing:
            return error_result("missing_growth_metrics", "新增生长记录需要同时提供身高、体重和头围。", data={"missing_fields": missing})
        written_id = _insert_growth(uid, resolved_infant_id, values, measured_at=measured_at)
        _remember_idempotency_growth_id(uid, idem_key, written_id)
        return _growth_result(uid, written_id, "infant_growth_created", "宝宝生长记录已新增。", limit)

    if op == "update":
        gid = to_int(growth_id, 0)
        if gid <= 0:
            return error_result("missing_growth_id", "修改生长记录需要 growth_id。")
        current = _growth_row(uid, gid)
        if not current:
            return error_result("growth_record_not_found", "未找到要修改的宝宝生长记录。")
        if infant_id is not None and int(current.get("infant_id") or 0) != int(infant_id):
            return error_result("growth_record_not_found", "指定宝宝下未找到该生长记录。")
        if all(value is None for value in values.values()):
            return error_result("empty_growth_update", "没有可更新的身高、体重或头围。")
        _update_growth(uid, gid, values, measured_at=measured_at)
        _remember_idempotency_growth_id(uid, idem_key, gid)
        return _growth_result(uid, gid, "infant_growth_updated", "宝宝生长记录已更新。", limit)

    resolved_infant_id = _resolve_infant_id(uid, infant_id)
    if resolved_infant_id <= 0:
        return error_result("infant_profile_not_found", "未找到宝宝档案，无法更新今日生长记录。")
    if all(value is None for value in values.values()):
        return error_result("empty_growth_update", "没有可更新的身高、体重或头围。")
    latest = _latest_growth_for_infant(uid, resolved_infant_id)
    if latest and _record_date(latest) == measured_at[:10]:
        gid = int(latest.get("growth_id") or 0)
        _update_growth(uid, gid, values, measured_at=measured_at)
        _remember_idempotency_growth_id(uid, idem_key, gid)
        return _growth_result(uid, gid, "infant_growth_updated", "今日宝宝生长记录已更新。", limit)

    missing = [key for key, value in values.items() if value is None]
    if missing:
        return error_result("missing_growth_metrics", "今天还没有生长记录，新增时需要同时提供身高、体重和头围。", data={"missing_fields": missing})
    written_id = _insert_growth(uid, resolved_infant_id, values, measured_at=measured_at)
    _remember_idempotency_growth_id(uid, idem_key, written_id)
    return _growth_result(uid, written_id, "infant_growth_created", "今日宝宝生长记录已新增。", limit)


def _resolve_infant_id(user_id: str, infant_id: int | None) -> int:
    params: list[Any] = [user_id]
    clauses = ["user_id = ?"]
    if infant_id is not None:
        clauses.append("infant_id = ?")
        params.append(int(infant_id))
    row = fetch_one(
        f"""
        SELECT infant_id
        FROM infant_profile
        WHERE {" AND ".join(clauses)}
        ORDER BY infant_id ASC
        LIMIT 1
        """,
        params,
    )
    return int(row.get("infant_id") or 0) if row else 0


def _insert_growth(user_id: str, infant_id: int, values: dict[str, float | None], *, measured_at: str) -> int:
    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO infant_growth_log(
                user_id, infant_id, height_cm, weight_kg, head_cm,
                height_measured_at, weight_measured_at, head_measured_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                int(infant_id),
                values["height_cm"],
                values["weight_kg"],
                values["head_cm"],
                measured_at,
                measured_at,
                measured_at,
                _now(),
            ),
        )
        return int(cursor.lastrowid or 0)


def _update_growth(user_id: str, growth_id: int, values: dict[str, float | None], *, measured_at: str) -> None:
    fields: list[str] = []
    params: list[Any] = []
    for field_name, measured_field in (
        ("height_cm", "height_measured_at"),
        ("weight_kg", "weight_measured_at"),
        ("head_cm", "head_measured_at"),
    ):
        value = values.get(field_name)
        if value is None:
            continue
        fields.append(f"{field_name} = ?")
        params.append(value)
        fields.append(f"{measured_field} = ?")
        params.append(measured_at)
    if not fields:
        return
    params.extend([user_id, int(growth_id)])
    with transaction() as conn:
        conn.execute(
            f"UPDATE infant_growth_log SET {', '.join(fields)} WHERE user_id = ? AND growth_id = ?",
            params,
        )


def _growth_result(user_id: str, growth_id: int, status: str, summary: str, history_limit: int) -> ServiceResult:
    record = _growth_row(user_id, growth_id) or {}
    infant_id = int(record.get("infant_id") or 0)
    history = _growth_history(user_id, infant_id, history_limit)
    return ok_result(
        status,
        summary,
        {
            "record": record,
            "latest": history[0] if history else record,
            "history": history,
            "history_limit": history_limit,
        },
    )


def _growth_row(user_id: str, growth_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT growth_id, user_id, infant_id, height_cm, weight_kg, head_cm,
               height_measured_at, weight_measured_at, head_measured_at, created_at
        FROM infant_growth_log
        WHERE user_id = ? AND growth_id = ?
        """,
        (user_id, int(growth_id)),
    )


def _latest_growth_for_infant(user_id: str, infant_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT growth_id, user_id, infant_id, height_cm, weight_kg, head_cm,
               height_measured_at, weight_measured_at, head_measured_at, created_at
        FROM infant_growth_log
        WHERE user_id = ? AND infant_id = ?
        ORDER BY growth_id DESC
        LIMIT 1
        """,
        (user_id, int(infant_id)),
    )


def _growth_history(user_id: str, infant_id: int, limit: int) -> list[dict[str, Any]]:
    if infant_id <= 0:
        return []
    return fetch_all(
        """
        SELECT growth_id, user_id, infant_id, height_cm, weight_kg, head_cm,
               height_measured_at, weight_measured_at, head_measured_at, created_at
        FROM infant_growth_log
        WHERE user_id = ? AND infant_id = ?
        ORDER BY growth_id DESC
        LIMIT ?
        """,
        (user_id, int(infant_id), int(limit)),
    )


def _record_date(record: dict[str, Any]) -> str:
    for key in ("created_at", "weight_measured_at", "height_measured_at", "head_measured_at"):
        value = norm_text(record.get(key))
        if len(value) >= 10:
            return value[:10]
    return ""


def _idempotency_growth_id(user_id: str, idempotency_key: str) -> int:
    row = fetch_one(
        """
        SELECT resource_id
        FROM tool_idempotency_log
        WHERE user_id = ?
          AND tool_name = 'infant_growth_mutate'
          AND idempotency_key = ?
        LIMIT 1
        """,
        (user_id, idempotency_key),
    )
    return int(row.get("resource_id") or 0) if row else 0


def _remember_idempotency_growth_id(user_id: str, idempotency_key: str, growth_id: int) -> None:
    with transaction() as conn:
        _ensure_idempotency_table(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO tool_idempotency_log(user_id, tool_name, idempotency_key, resource_id, created_at)
            VALUES (?, 'infant_growth_mutate', ?, ?, ?)
            """,
            (user_id, idempotency_key, int(growth_id), _now()),
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


def _measurement_time(value: Any) -> str:
    parsed = parse_datetime(value) if value else datetime.now()
    if parsed is None:
        parsed = datetime.now()
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
