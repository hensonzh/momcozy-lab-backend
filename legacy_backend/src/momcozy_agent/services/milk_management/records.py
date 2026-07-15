from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .db import fetch_all, transaction
from .feeding import estimate_breastfeeding_milk
from .schemas import ServiceResult, error_result, norm_text, ok_result, parse_datetime, to_int

FEED_TYPE_NURSING = "亲喂"
FEED_TYPE_BREASTMILK_BOTTLE = "瓶喂母乳"
FEED_TYPE_FORMULA_BOTTLE = "配方奶"

RECORD_KIND_PUMPING = "pumping"
RECORD_KIND_NURSING = "nursing"
RECORD_KIND_BREASTMILK_BOTTLE = "breastmilk_bottle"
RECORD_KIND_FORMULA_BOTTLE = "formula_bottle"

RECORD_SCOPES = {
    "all",
    "milk_output",
    "feeding",
    RECORD_KIND_PUMPING,
    RECORD_KIND_NURSING,
    RECORD_KIND_BREASTMILK_BOTTLE,
    RECORD_KIND_FORMULA_BOTTLE,
}

FEED_TYPE_BY_RECORD_KIND = {
    RECORD_KIND_NURSING: FEED_TYPE_NURSING,
    RECORD_KIND_BREASTMILK_BOTTLE: FEED_TYPE_BREASTMILK_BOTTLE,
    RECORD_KIND_FORMULA_BOTTLE: FEED_TYPE_FORMULA_BOTTLE,
}


def get_records_range(
    *,
    user_id: str,
    start_at: str,
    end_at: str,
    record_scope: str = "all",
    include_raw_records: bool = True,
    summary_granularity: str = "daily",
    limit: int = 200,
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法读取奶量记录。")

    window = _normalize_window(start_at, end_at)
    if window.get("error"):
        return error_result("invalid_time_range", window["error"])
    scope = _normalize_scope(record_scope)
    if scope not in RECORD_SCOPES:
        return error_result("invalid_record_scope", f"Unsupported record_scope: {record_scope}")

    start_text = _db_time(window["start_dt"])
    end_text = _db_time(window["end_dt"])
    pumping_rows = fetch_all(
        """
        SELECT pumping_id, user_id, pump_start_time, pump_end_time, pump_milk_volum,
               pump_type, pump_milk_duration, pump_source, pump_title, created_at
        FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time >= ?
          AND pump_start_time < ?
        ORDER BY pump_start_time ASC, pumping_id ASC
        """,
        (uid, start_text, end_text),
    )
    feeding_rows = fetch_all(
        """
        SELECT feeding_id, user_id, infant_id, feed_time, feed_milk_volum,
               feed_type, feed_action, feeding_title, created_at
        FROM feeding_log
        WHERE user_id = ?
          AND feed_time >= ?
          AND feed_time < ?
        ORDER BY feed_time ASC, feeding_id ASC
        """,
        (uid, start_text, end_text),
    )

    pumping_records = [_normalize_pumping_record(row) for row in pumping_rows if _include_pumping_row(row, scope)]
    feeding_records = [_normalize_feeding_record(row) for row in feeding_rows if _include_feeding_row(row, scope)]
    summary = _summarize_records(
        user_id=uid,
        start_dt=window["start_dt"],
        end_dt=window["end_dt"],
        pumping_rows=pumping_rows,
        feeding_rows=feeding_rows,
        scope=scope,
        summary_granularity=summary_granularity,
    )

    raw_limit = min(max(to_int(limit, 200), 1), 500)
    if include_raw_records:
        pumping_records = pumping_records[:raw_limit]
        feeding_records = feeding_records[:raw_limit]
    else:
        pumping_records = []
        feeding_records = []

    return ok_result(
        "milk_records_range_loaded",
        f"已读取 {summary['window']['start_at']} 到 {summary['window']['end_at']} 的奶量记录。",
        {
            "user_id": uid,
            "record_scope": scope,
            "summary": summary,
            "records": {
                "pumping": pumping_records,
                "feeding": feeding_records,
            },
            "record_count": len(pumping_records) + len(feeding_records),
            "raw_record_limit": raw_limit,
        },
    )


def create_record(
    *,
    user_id: str,
    record_kind: str,
    occurred_at: str,
    amount_ml: float | None = None,
    duration_minutes: int | None = None,
    infant_id: int | None = None,
    title: str | None = None,
    idempotency_key: str,
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法新增奶量记录。")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")
    kind = _canonical_record_kind(record_kind)
    if not kind:
        return error_result("invalid_record_kind", f"Unsupported record_kind: {record_kind}")
    occurred_dt = parse_datetime(occurred_at)
    if occurred_dt is None:
        return error_result("invalid_occurred_at", "occurred_at 必须是有效日期时间。")

    duration = _optional_int(duration_minutes)
    amount = _optional_float(amount_ml)
    title_text = norm_text(title)
    occurred_text = _db_time(occurred_dt)
    end_text = _db_time(occurred_dt + timedelta(minutes=duration)) if duration and duration > 0 else occurred_text

    with transaction() as conn:
        if kind == RECORD_KIND_PUMPING:
            cursor = conn.execute(
                """
                INSERT INTO pumping_log(user_id, pump_start_time, pump_end_time, pump_milk_volum,
                                        pump_type, pump_milk_duration, pump_source, pump_title, created_at)
                VALUES (?, ?, ?, ?, 0, ?, 1, ?, CURRENT_TIMESTAMP)
                """,
                (uid, occurred_text, end_text, amount, duration, title_text),
            )
            record = {"record_kind": kind, "pumping_id": int(cursor.lastrowid or 0)}
            return ok_result("milk_record_created", "吸奶记录已新增。", {"record": record})

        stored_infant_id = _resolve_infant_id(conn, uid, infant_id)
        feed_type = FEED_TYPE_BY_RECORD_KIND[kind]
        feed_value = amount if amount is not None else duration
        cursor = conn.execute(
            """
            INSERT INTO feeding_log(user_id, infant_id, feed_time, feed_type, feed_milk_volum,
                                    feed_action, feeding_title, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, CURRENT_TIMESTAMP)
            """,
            (uid, stored_infant_id, occurred_text, feed_type, feed_value, title_text),
        )
        feeding_id = int(cursor.lastrowid or 0)
        pumping_id = 0
        if kind == RECORD_KIND_NURSING:
            cursor = conn.execute(
                """
                INSERT INTO pumping_log(user_id, pump_start_time, pump_end_time, pump_milk_volum,
                                        pump_type, pump_milk_duration, pump_source, pump_title, created_at)
                VALUES (?, ?, ?, NULL, 2, ?, 1, ?, CURRENT_TIMESTAMP)
                """,
                (uid, occurred_text, end_text, duration, title_text),
            )
            pumping_id = int(cursor.lastrowid or 0)

    return ok_result(
        "milk_record_created",
        "喂养记录已新增。",
        {"record": {"record_kind": kind, "feeding_id": feeding_id, "synced_pumping_id": pumping_id}},
    )


def update_record(
    *,
    user_id: str,
    record_kind: str,
    record_id: int,
    patch: dict[str, Any] | str,
    idempotency_key: str,
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法修改奶量记录。")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")
    kind = _canonical_record_kind(record_kind)
    if not kind:
        return error_result("invalid_record_kind", f"Unsupported record_kind: {record_kind}")
    rid = to_int(record_id, 0)
    if rid <= 0:
        return error_result("missing_record_id", "缺少有效 record_id。")
    patch_data = _parse_json_object(patch)
    if not patch_data:
        return error_result("empty_patch", "没有可更新的记录字段。")

    if kind == RECORD_KIND_PUMPING:
        return _update_pumping_record(user_id=uid, pumping_id=rid, patch=patch_data)
    return _update_feeding_record(user_id=uid, feeding_id=rid, current_kind=kind, patch=patch_data)


def delete_record(
    *,
    user_id: str,
    record_kind: str,
    record_id: int,
    idempotency_key: str,
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法删除奶量记录。")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")
    kind = _canonical_record_kind(record_kind)
    if not kind:
        return error_result("invalid_record_kind", f"Unsupported record_kind: {record_kind}")
    rid = to_int(record_id, 0)
    if rid <= 0:
        return error_result("missing_record_id", "缺少有效 record_id。")

    with transaction() as conn:
        if kind == RECORD_KIND_PUMPING:
            cursor = conn.execute(
                "DELETE FROM pumping_log WHERE user_id = ? AND pumping_id = ? AND COALESCE(pump_type, 0) != 2",
                (uid, rid),
            )
            deleted_count = int(cursor.rowcount or 0)
            return ok_result("milk_record_deleted", "吸奶记录已删除。", {"deleted_count": deleted_count, "record_kind": kind, "record_id": rid})

        row = conn.execute(
            """
            SELECT feeding_id, feed_time, feed_type, feeding_title
            FROM feeding_log
            WHERE user_id = ? AND feeding_id = ?
            """,
            (uid, rid),
        ).fetchone()
        if row is None:
            return ok_result("milk_record_already_absent", "该喂养记录已不存在。", {"deleted_count": 0, "record_kind": kind, "record_id": rid})
        synced_deleted = 0
        if _feeding_kind(row["feed_type"]) == RECORD_KIND_NURSING:
            synced_deleted = _delete_synced_nursing_pumps(conn, user_id=uid, feed_time=row["feed_time"], title=row["feeding_title"])
        cursor = conn.execute("DELETE FROM feeding_log WHERE user_id = ? AND feeding_id = ?", (uid, rid))
        deleted_count = int(cursor.rowcount or 0)

    return ok_result(
        "milk_record_deleted",
        "喂养记录已删除。",
        {"deleted_count": deleted_count, "synced_pumping_deleted_count": synced_deleted, "record_kind": kind, "record_id": rid},
    )


def _update_pumping_record(*, user_id: str, pumping_id: int, patch: dict[str, Any]) -> ServiceResult:
    with transaction() as conn:
        current = conn.execute(
            """
            SELECT pumping_id, pump_start_time, pump_end_time, pump_type
            FROM pumping_log
            WHERE user_id = ? AND pumping_id = ?
            """,
            (user_id, pumping_id),
        ).fetchone()
        if current is None or int(current["pump_type"] or 0) == 2:
            return error_result("pumping_record_not_found", "未找到可修改的吸奶记录。")

        fields: list[str] = []
        params: list[Any] = []
        start_text = str(current["pump_start_time"] or "")
        if "occurred_at" in patch:
            parsed = parse_datetime(patch.get("occurred_at"))
            if parsed is None:
                return error_result("invalid_occurred_at", "occurred_at 必须是有效日期时间。")
            start_text = _db_time(parsed)
            fields.append("pump_start_time = ?")
            params.append(start_text)
        if "ended_at" in patch:
            parsed = parse_datetime(patch.get("ended_at"))
            if parsed is None:
                return error_result("invalid_ended_at", "ended_at 必须是有效日期时间。")
            fields.append("pump_end_time = ?")
            params.append(_db_time(parsed))
        if "duration_minutes" in patch:
            duration = _optional_int(patch.get("duration_minutes"))
            fields.append("pump_milk_duration = ?")
            params.append(duration)
            if "ended_at" not in patch:
                start_dt = parse_datetime(start_text)
                if start_dt is not None and duration and duration > 0:
                    fields.append("pump_end_time = ?")
                    params.append(_db_time(start_dt + timedelta(minutes=duration)))
        if "amount_ml" in patch:
            fields.append("pump_milk_volum = ?")
            params.append(_optional_float(patch.get("amount_ml")))
        if "title" in patch:
            fields.append("pump_title = ?")
            params.append(norm_text(patch.get("title")))
        if not fields:
            return error_result("empty_patch", "没有可更新的吸奶记录字段。")

        params.extend([user_id, pumping_id])
        cursor = conn.execute(
            f"UPDATE pumping_log SET {', '.join(fields)} WHERE user_id = ? AND pumping_id = ?",
            params,
        )
        updated_count = int(cursor.rowcount or 0)

    return ok_result("milk_record_updated", "吸奶记录已更新。", {"updated_count": updated_count, "record_kind": RECORD_KIND_PUMPING, "record_id": pumping_id})


def _update_feeding_record(*, user_id: str, feeding_id: int, current_kind: str, patch: dict[str, Any]) -> ServiceResult:
    with transaction() as conn:
        current = conn.execute(
            """
            SELECT feeding_id, user_id, infant_id, feed_time, feed_milk_volum,
                   feed_type, feed_action, feeding_title
            FROM feeding_log
            WHERE user_id = ? AND feeding_id = ?
            """,
            (user_id, feeding_id),
        ).fetchone()
        if current is None:
            return error_result("feeding_record_not_found", "未找到可修改的喂养记录。")

        old_kind = _feeding_kind(current["feed_type"])
        if current_kind != old_kind:
            return error_result("record_kind_mismatch", "record_kind 与当前记录类型不匹配，请先读取记录后再修改。")

        new_kind = _canonical_record_kind(patch.get("record_kind")) if "record_kind" in patch else old_kind
        if new_kind == RECORD_KIND_PUMPING or not new_kind:
            return error_result("invalid_record_kind", "喂养记录只能改为亲喂、母乳瓶喂或奶粉瓶喂。")

        fields: list[str] = []
        params: list[Any] = []
        feed_time = str(current["feed_time"] or "")
        duration = _optional_int(patch.get("duration_minutes")) if "duration_minutes" in patch else None
        title = str(current["feeding_title"] or "")
        if "occurred_at" in patch:
            parsed = parse_datetime(patch.get("occurred_at"))
            if parsed is None:
                return error_result("invalid_occurred_at", "occurred_at 必须是有效日期时间。")
            feed_time = _db_time(parsed)
            fields.append("feed_time = ?")
            params.append(feed_time)
        if "record_kind" in patch:
            fields.append("feed_type = ?")
            params.append(FEED_TYPE_BY_RECORD_KIND[new_kind])
        if "infant_id" in patch:
            fields.append("infant_id = ?")
            params.append(_resolve_infant_id(conn, user_id, patch.get("infant_id")))
        if "title" in patch:
            title = norm_text(patch.get("title"))
            fields.append("feeding_title = ?")
            params.append(title)
        if "feed_action" in patch:
            fields.append("feed_action = ?")
            params.append(to_int(patch.get("feed_action"), 0))
        if "amount_ml" in patch or "duration_minutes" in patch:
            amount = _optional_float(patch.get("amount_ml")) if "amount_ml" in patch else None
            feed_value = amount if amount is not None else duration
            fields.append("feed_milk_volum = ?")
            params.append(feed_value)
        if not fields:
            return error_result("empty_patch", "没有可更新的喂养记录字段。")

        params.extend([user_id, feeding_id])
        cursor = conn.execute(
            f"UPDATE feeding_log SET {', '.join(fields)} WHERE user_id = ? AND feeding_id = ?",
            params,
        )
        updated_count = int(cursor.rowcount or 0)

        synced_pumping_id = 0
        if old_kind == RECORD_KIND_NURSING and new_kind != RECORD_KIND_NURSING:
            _delete_synced_nursing_pumps(conn, user_id=user_id, feed_time=current["feed_time"], title=current["feeding_title"])
        elif old_kind == RECORD_KIND_NURSING and new_kind == RECORD_KIND_NURSING:
            synced_pumping_id = _update_or_create_synced_nursing_pump(
                conn,
                user_id=user_id,
                old_feed_time=current["feed_time"],
                old_title=current["feeding_title"],
                feed_time=feed_time,
                title=title,
                duration_minutes=duration,
            )
        elif old_kind != RECORD_KIND_NURSING and new_kind == RECORD_KIND_NURSING:
            synced_pumping_id = _create_synced_nursing_pump(
                conn,
                user_id=user_id,
                feed_time=feed_time,
                title=title,
                duration_minutes=duration,
            )

    return ok_result(
        "milk_record_updated",
        "喂养记录已更新。",
        {"updated_count": updated_count, "record_kind": new_kind, "record_id": feeding_id, "synced_pumping_id": synced_pumping_id},
    )


def _summarize_records(
    *,
    user_id: str,
    start_dt: datetime,
    end_dt: datetime,
    pumping_rows: list[dict[str, Any]],
    feeding_rows: list[dict[str, Any]],
    scope: str,
    summary_granularity: str,
) -> dict[str, Any]:
    pumping_for_scope = [row for row in pumping_rows if _include_pumping_row(row, scope)]
    feeding_for_scope = [row for row in feeding_rows if _include_feeding_row(row, scope)]
    non_nursing_pumps = [row for row in pumping_for_scope if int(row.get("pump_type") or 0) != 2]
    nursing_feeds = [row for row in feeding_for_scope if _feeding_kind(row.get("feed_type")) == RECORD_KIND_NURSING]
    breastmilk_bottles = [row for row in feeding_for_scope if _feeding_kind(row.get("feed_type")) == RECORD_KIND_BREASTMILK_BOTTLE]
    formula_bottles = [row for row in feeding_for_scope if _feeding_kind(row.get("feed_type")) == RECORD_KIND_FORMULA_BOTTLE]

    pumped_ml = round(sum(_float(row.get("pump_milk_volum")) for row in non_nursing_pumps), 1)
    estimated_per_nursing_ml = estimate_breastfeeding_milk(user_id=user_id, as_of_time=_db_time(end_dt))
    estimated_nursing_ml = (
        round(float(estimated_per_nursing_ml) * len(nursing_feeds), 1)
        if estimated_per_nursing_ml is not None and nursing_feeds
        else None
    )
    total_output_ml = round(pumped_ml + float(estimated_nursing_ml or 0.0), 1) if estimated_nursing_ml is not None else None

    summary: dict[str, Any] = {
        "window": {
            "start_at": _db_time(start_dt),
            "end_at": _db_time(end_dt),
            "end_exclusive": True,
        },
        "record_counts": {
            "pumping": len([row for row in pumping_for_scope if int(row.get("pump_type") or 0) != 2]),
            "feeding": len(feeding_for_scope),
            "nursing": len([row for row in feeding_for_scope if _feeding_kind(row.get("feed_type")) == RECORD_KIND_NURSING]),
            "breastmilk_bottle": len([row for row in feeding_for_scope if _feeding_kind(row.get("feed_type")) == RECORD_KIND_BREASTMILK_BOTTLE]),
            "formula_bottle": len([row for row in feeding_for_scope if _feeding_kind(row.get("feed_type")) == RECORD_KIND_FORMULA_BOTTLE]),
        },
        "milk_output": {
            "pumped_ml": pumped_ml,
            "pumping_count": len(non_nursing_pumps),
            "nursing_count": len(nursing_feeds),
            "estimated_per_nursing_ml": estimated_per_nursing_ml,
            "estimated_nursing_ml": estimated_nursing_ml,
            "total_estimated_output_ml": total_output_ml,
            "estimated_fields": ["estimated_per_nursing_ml", "estimated_nursing_ml", "total_estimated_output_ml"] if estimated_nursing_ml is not None else [],
        },
        "feeding": {
            "total_count": len(feeding_for_scope),
            "nursing_count": len(nursing_feeds),
            "breastmilk_bottle_count": len(breastmilk_bottles),
            "formula_bottle_count": len(formula_bottles),
            "bottle_total_ml": round(sum(_float(row.get("feed_milk_volum")) for row in breastmilk_bottles + formula_bottles), 1),
            "breastmilk_bottle_ml": round(sum(_float(row.get("feed_milk_volum")) for row in breastmilk_bottles), 1),
            "formula_bottle_ml": round(sum(_float(row.get("feed_milk_volum")) for row in formula_bottles), 1),
        },
    }
    if norm_text(summary_granularity) == "daily":
        summary["daily"] = _daily_summary(pumping_for_scope, feeding_for_scope)
    return summary


def _daily_summary(pumping_rows: list[dict[str, Any]], feeding_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {}
    for row in pumping_rows:
        if int(row.get("pump_type") or 0) == 2:
            continue
        day = _date_key(row.get("pump_start_time"))
        if not day:
            continue
        slot = _day_slot(by_day, day)
        slot["pumped_ml"] += _float(row.get("pump_milk_volum"))
        slot["pumping_count"] += 1
    for row in feeding_rows:
        day = _date_key(row.get("feed_time"))
        if not day:
            continue
        kind = _feeding_kind(row.get("feed_type"))
        slot = _day_slot(by_day, day)
        slot["feeding_count"] += 1
        if kind == RECORD_KIND_NURSING:
            slot["nursing_count"] += 1
        elif kind == RECORD_KIND_BREASTMILK_BOTTLE:
            slot["breastmilk_bottle_count"] += 1
            slot["breastmilk_bottle_ml"] += _float(row.get("feed_milk_volum"))
        elif kind == RECORD_KIND_FORMULA_BOTTLE:
            slot["formula_bottle_count"] += 1
            slot["formula_bottle_ml"] += _float(row.get("feed_milk_volum"))
    return [
        {
            **slot,
            "pumped_ml": round(slot["pumped_ml"], 1),
            "breastmilk_bottle_ml": round(slot["breastmilk_bottle_ml"], 1),
            "formula_bottle_ml": round(slot["formula_bottle_ml"], 1),
        }
        for _, slot in sorted(by_day.items())
    ]


def _day_slot(by_day: dict[str, dict[str, Any]], day: str) -> dict[str, Any]:
    return by_day.setdefault(
        day,
        {
            "date": day,
            "pumped_ml": 0.0,
            "pumping_count": 0,
            "feeding_count": 0,
            "nursing_count": 0,
            "breastmilk_bottle_count": 0,
            "formula_bottle_count": 0,
            "breastmilk_bottle_ml": 0.0,
            "formula_bottle_ml": 0.0,
        },
    )


def _normalize_pumping_record(row: dict[str, Any]) -> dict[str, Any]:
    pump_type = int(row.get("pump_type") or 0)
    return {
        "record_id": int(row.get("pumping_id") or 0),
        "record_table": "pumping_log",
        "record_kind": RECORD_KIND_NURSING if pump_type == 2 else RECORD_KIND_PUMPING,
        "occurred_at": row.get("pump_start_time"),
        "ended_at": row.get("pump_end_time"),
        "amount_ml": _nullable_float(row.get("pump_milk_volum")),
        "duration_minutes": _optional_int(row.get("pump_milk_duration")),
        "title": row.get("pump_title"),
        "source": row.get("pump_source"),
        "created_at": row.get("created_at"),
    }


def _normalize_feeding_record(row: dict[str, Any]) -> dict[str, Any]:
    kind = _feeding_kind(row.get("feed_type"))
    amount = _nullable_float(row.get("feed_milk_volum"))
    return {
        "record_id": int(row.get("feeding_id") or 0),
        "record_table": "feeding_log",
        "record_kind": kind,
        "infant_id": row.get("infant_id"),
        "occurred_at": row.get("feed_time"),
        "amount_ml": amount if kind != RECORD_KIND_NURSING else None,
        "duration_minutes": int(amount) if kind == RECORD_KIND_NURSING and amount is not None else None,
        "feed_type": row.get("feed_type"),
        "feed_action": row.get("feed_action"),
        "title": row.get("feeding_title"),
        "created_at": row.get("created_at"),
    }


def _include_pumping_row(row: dict[str, Any], scope: str) -> bool:
    pump_type = int(row.get("pump_type") or 0)
    if pump_type == 2:
        return False
    return scope in {"all", "milk_output", RECORD_KIND_PUMPING}


def _include_feeding_row(row: dict[str, Any], scope: str) -> bool:
    kind = _feeding_kind(row.get("feed_type"))
    if scope in {"all", "feeding"}:
        return True
    if scope == "milk_output":
        return kind == RECORD_KIND_NURSING
    return scope == kind


def _normalize_window(start_at: Any, end_at: Any) -> dict[str, Any]:
    start_dt = parse_datetime(start_at)
    end_dt = parse_datetime(end_at)
    if start_dt is None or end_dt is None:
        return {"error": "start_at 和 end_at 必须是有效日期或日期时间。"}
    if _is_date_only(end_at):
        end_dt = end_dt + timedelta(days=1)
    if end_dt <= start_dt:
        return {"error": "end_at 必须晚于 start_at。"}
    return {"start_dt": start_dt, "end_dt": end_dt}


def _is_date_only(value: Any) -> bool:
    token = norm_text(value)
    return len(token) == 10 and token[4:5] in {"-", "/"} and token[7:8] in {"-", "/"}


def _normalize_scope(value: Any) -> str:
    token = norm_text(value) or "all"
    aliases = {
        "母乳产出": "milk_output",
        "吸奶": RECORD_KIND_PUMPING,
        "亲喂": RECORD_KIND_NURSING,
        "母乳瓶喂": RECORD_KIND_BREASTMILK_BOTTLE,
        "瓶喂母乳": RECORD_KIND_BREASTMILK_BOTTLE,
        "奶粉瓶喂": RECORD_KIND_FORMULA_BOTTLE,
        "配方奶": RECORD_KIND_FORMULA_BOTTLE,
    }
    return aliases.get(token, token)


def _canonical_record_kind(value: Any) -> str:
    token = norm_text(value)
    aliases = {
        "吸奶": RECORD_KIND_PUMPING,
        "pump": RECORD_KIND_PUMPING,
        "pumping": RECORD_KIND_PUMPING,
        "亲喂": RECORD_KIND_NURSING,
        "nursing": RECORD_KIND_NURSING,
        "breastfeeding": RECORD_KIND_NURSING,
        "母乳瓶喂": RECORD_KIND_BREASTMILK_BOTTLE,
        "瓶喂母乳": RECORD_KIND_BREASTMILK_BOTTLE,
        "breastmilk_bottle": RECORD_KIND_BREASTMILK_BOTTLE,
        "奶粉瓶喂": RECORD_KIND_FORMULA_BOTTLE,
        "配方奶": RECORD_KIND_FORMULA_BOTTLE,
        "formula_bottle": RECORD_KIND_FORMULA_BOTTLE,
    }
    return aliases.get(token, "")


def _feeding_kind(feed_type: Any) -> str:
    token = norm_text(feed_type).lower()
    if "亲喂" in token or "breastfeeding" in token or token == "direct":
        return RECORD_KIND_NURSING
    if "母乳" in token and ("瓶" in token or "bottle" in token):
        return RECORD_KIND_BREASTMILK_BOTTLE
    if "奶粉" in token or "配方" in token or "formula" in token:
        return RECORD_KIND_FORMULA_BOTTLE
    return "feeding_other"


def _resolve_infant_id(conn: Any, user_id: str, infant_id: Any) -> int:
    requested = to_int(infant_id, 0)
    if requested > 0:
        row = conn.execute(
            "SELECT infant_id FROM infant_profile WHERE user_id = ? AND infant_id = ?",
            (user_id, requested),
        ).fetchone()
        if row is not None:
            return int(row["infant_id"] or 0)
    row = conn.execute(
        "SELECT infant_id FROM infant_profile WHERE user_id = ? ORDER BY infant_id LIMIT 1",
        (user_id,),
    ).fetchone()
    return int(row["infant_id"] or 0) if row is not None else 0


def _update_or_create_synced_nursing_pump(
    conn: Any,
    *,
    user_id: str,
    old_feed_time: Any,
    old_title: Any,
    feed_time: str,
    title: str,
    duration_minutes: int | None,
) -> int:
    pumping_id = _find_synced_nursing_pump(conn, user_id=user_id, feed_time=old_feed_time, title=old_title)
    if pumping_id <= 0:
        return _create_synced_nursing_pump(conn, user_id=user_id, feed_time=feed_time, title=title, duration_minutes=duration_minutes)
    start_dt = parse_datetime(feed_time)
    end_time = _db_time(start_dt + timedelta(minutes=duration_minutes)) if start_dt and duration_minutes and duration_minutes > 0 else feed_time
    conn.execute(
        """
        UPDATE pumping_log
        SET pump_start_time = ?,
            pump_end_time = ?,
            pump_milk_duration = ?,
            pump_title = ?
        WHERE user_id = ? AND pumping_id = ?
        """,
        (feed_time, end_time, duration_minutes, title, user_id, pumping_id),
    )
    return pumping_id


def _create_synced_nursing_pump(conn: Any, *, user_id: str, feed_time: str, title: str, duration_minutes: int | None) -> int:
    start_dt = parse_datetime(feed_time)
    end_time = _db_time(start_dt + timedelta(minutes=duration_minutes)) if start_dt and duration_minutes and duration_minutes > 0 else feed_time
    cursor = conn.execute(
        """
        INSERT INTO pumping_log(user_id, pump_start_time, pump_end_time, pump_milk_volum,
                                pump_type, pump_milk_duration, pump_source, pump_title, created_at)
        VALUES (?, ?, ?, NULL, 2, ?, 1, ?, CURRENT_TIMESTAMP)
        """,
        (user_id, feed_time, end_time, duration_minutes, title),
    )
    return int(cursor.lastrowid or 0)


def _delete_synced_nursing_pumps(conn: Any, *, user_id: str, feed_time: Any, title: Any) -> int:
    clauses = ["user_id = ?", "pump_start_time = ?", "pump_type = 2"]
    params: list[Any] = [user_id, str(feed_time or "")]
    if norm_text(title):
        clauses.append("COALESCE(pump_title, '') = ?")
        params.append(norm_text(title))
    cursor = conn.execute(f"DELETE FROM pumping_log WHERE {' AND '.join(clauses)}", params)
    return int(cursor.rowcount or 0)


def _find_synced_nursing_pump(conn: Any, *, user_id: str, feed_time: Any, title: Any) -> int:
    clauses = ["user_id = ?", "pump_start_time = ?", "pump_type = 2"]
    params: list[Any] = [user_id, str(feed_time or "")]
    if norm_text(title):
        clauses.append("COALESCE(pump_title, '') = ?")
        params.append(norm_text(title))
    row = conn.execute(
        f"""
        SELECT pumping_id
        FROM pumping_log
        WHERE {' AND '.join(clauses)}
        ORDER BY pumping_id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return int(row["pumping_id"] or 0) if row is not None else 0


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


def _date_key(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.date().isoformat() if parsed is not None else ""


def _db_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _nullable_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    return _nullable_float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = to_int(value, 0)
    return parsed if parsed >= 0 else None
