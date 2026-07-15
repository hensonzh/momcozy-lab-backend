from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import data_store
from .calendar import get_calendar_day
from .db import fetch_all, transaction
from .schemas import CALENDAR_TYPE_PUMP, ServiceResult, error_result, norm_text, ok_result, parse_datetime, to_int


def get_today_overview(
    *,
    user_id: str,
    target_date: str | None = None,
    plan_id: int | None = None,
) -> ServiceResult:
    uid = norm_text(user_id)
    date = _target_date(target_date)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法查看今日安排。")

    day_result = get_calendar_day(user_id=uid, target_date=date, plan_id=plan_id)
    if not day_result.get("ok"):
        return day_result
    day_data = day_result.get("data") if isinstance(day_result.get("data"), dict) else {}
    items = day_data.get("items") if isinstance(day_data.get("items"), list) else []
    summary = _calendar_summary(items)
    return ok_result(
        "today_overview_loaded",
        f"{date} 共 {summary['total_count']} 项任务，已完成 {summary['completed_count']} 项，未完成 {summary['pending_count']} 项。",
        {
            "user_id": uid,
            "target_date": date,
            "plan_id": plan_id,
            "summary": summary,
            "items": items,
            "plan_context": day_data.get("plan_context"),
        },
    )


def get_today_summary(
    *,
    user_id: str,
    target_date: str | None = None,
    plan_id: int | None = None,
) -> ServiceResult:
    uid = norm_text(user_id)
    date = _target_date(target_date)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法查看今日日结。")

    overview = get_today_overview(user_id=uid, target_date=date, plan_id=plan_id)
    if not overview.get("ok"):
        return overview
    overview_data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
    items = overview_data.get("items") if isinstance(overview_data.get("items"), list) else []
    calendar_summary = overview_data.get("summary") if isinstance(overview_data.get("summary"), dict) else {}
    pumping_summary = _pumping_summary(uid, date)
    feeding_summary = _feeding_summary(uid, date)
    completion_rate = to_int(calendar_summary.get("completion_rate"), 0)
    summary_text = (
        f"{date} 日结：完成 {calendar_summary.get('completed_count', 0)}/{calendar_summary.get('total_count', 0)} 项，"
        f"完成率 {completion_rate}%；吸奶 {pumping_summary['pumping_count']} 次，记录奶量约 {pumping_summary['total_ml']} ml；"
        f"喂养记录 {feeding_summary['feeding_count']} 次，其中亲喂 {feeding_summary['breastfeeding_count']} 次。"
    )
    return ok_result(
        "today_summary_loaded",
        summary_text,
        {
            "user_id": uid,
            "target_date": date,
            "plan_id": plan_id,
            "calendar_summary": calendar_summary,
            "plan_context": overview_data.get("plan_context"),
            "pumping_summary": pumping_summary,
            "feeding_summary": feeding_summary,
            "items": items,
            "encouragement": _encouragement(calendar_summary, pumping_summary, feeding_summary),
        },
    )


def get_today_milk_activity_summary(*, user_id: str, target_date: str | None = None) -> ServiceResult:
    uid = norm_text(user_id)
    date = _target_date(target_date)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法查看今日奶量记录。")

    pumping_summary = _pumping_summary(uid, date)
    feeding_summary = _feeding_summary(uid, date)
    return ok_result(
        "today_milk_activity_loaded",
        (
            f"{date} 喂养 {feeding_summary['feeding_count']} 次，"
            f"吸奶 {pumping_summary['pumping_count']} 次。"
        ),
        {
            "user_id": uid,
            "target_date": date,
            "pumping_summary": pumping_summary,
            "feeding_summary": feeding_summary,
        },
    )


def get_today_daily_summary_fallback(*, user_id: str, target_date: str | None = None) -> ServiceResult:
    uid = norm_text(user_id)
    date = _target_date(target_date)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法生成今日日结。")

    pumping = _pumping_summary(uid, date)
    feeding = _feeding_summary(uid, date)
    feeding_count = to_int(feeding.get("feeding_count"), 0)
    nursing_count = to_int(feeding.get("breastfeeding_count"), 0)
    bottle_count = to_int(feeding.get("bottle_count"), 0)
    pumped_ml = float(pumping.get("total_ml") or 0.0)
    bottle_ml = float(feeding.get("total_bottle_ml") or 0.0)
    message = [
        f"今日喂养 {feeding_count} 次｜亲喂 {nursing_count} 次｜瓶喂 {bottle_count} 次｜瓶喂 {_format_number(bottle_ml)} ml",
        f"吸奶 {to_int(pumping.get('pumping_count'), 0)} 次｜吸奶总量 {_format_number(pumped_ml)} ml",
        "今日记录较少，暂无法完整判断节律" if feeding_count <= 1 else "今日喂养记录已同步，建议结合身体感受观察",
        "暂未发现明确风险提示",
        "建议继续记录吸奶和亲喂时间，便于明天判断节律",
    ]
    return ok_result(
        "today_daily_summary_fallback_loaded",
        "\n".join(message),
        {
            "user_id": uid,
            "target_date": date,
            "message": message,
            "pumping_summary": pumping,
            "feeding_summary": feeding,
        },
    )


def shift_today_tasks(
    *,
    user_id: str,
    target_date: str | None = None,
    shift_minutes: int = 30,
    from_time: str | None = None,
    plan_id: int | None = None,
    idempotency_key: str,
) -> ServiceResult:
    uid = norm_text(user_id)
    date = _target_date(target_date)
    minutes = to_int(shift_minutes, 30)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法顺延今日任务。")
    if minutes == 0:
        return error_result("invalid_shift_minutes", "shift_minutes 不能为 0。")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")

    threshold = _minute_of_day(from_time)
    changed: list[dict[str, Any]] = []
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT item_id, task_id, start_time, end_time, content
            FROM calendar
            WHERE user_id = ?
              AND date = ?
              AND (? IS NULL OR COALESCE(plan_id, 0) = COALESCE(?, 0))
            ORDER BY start_time ASC, task_id ASC
            """,
            (uid, date, plan_id, plan_id),
        ).fetchall()
        for row in rows:
            start_dt = parse_datetime(row["start_time"])
            if start_dt is None:
                continue
            if threshold is not None and start_dt.hour * 60 + start_dt.minute < threshold:
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
                WHERE item_id = ?
                  AND user_id = ?
                """,
                (_db_time(new_start), _db_time(new_end) if new_end is not None else None, int(row["item_id"]), uid),
            )
            changed.append(
                {
                    "item_id": int(row["item_id"]),
                    "task_id": row["task_id"],
                    "content": row["content"],
                    "old_start_time": row["start_time"],
                    "new_start_time": _db_time(new_start),
                    "new_end_time": _db_time(new_end) if new_end is not None else None,
                }
            )

    refreshed = get_today_overview(user_id=uid, target_date=date, plan_id=plan_id)
    return ok_result(
        "today_tasks_shifted",
        f"已顺延 {len(changed)} 个今日任务。",
        {
            "user_id": uid,
            "target_date": date,
            "shift_minutes": minutes,
            "changed_items": changed,
            "calendar": refreshed.get("data") if isinstance(refreshed.get("data"), dict) else {},
        },
    )


def confirm_today_tasks(
    *,
    user_id: str,
    target_date: str | None = None,
    plan_id: int | None = None,
    idempotency_key: str,
) -> ServiceResult:
    uid = norm_text(user_id)
    date = _target_date(target_date)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法确认今日任务。")
    if not norm_text(idempotency_key):
        return error_result("missing_idempotency_key", "缺少 idempotency_key。")

    with transaction() as conn:
        rows_to_confirm = conn.execute(
            """
            SELECT item_id, user_id, date, task_id, start_time, end_time,
                   content, type, source, is_milk_pump, finish
            FROM calendar
            WHERE user_id = ?
              AND date = ?
              AND type = ?
              AND finish != 'true'
              AND (? IS NULL OR COALESCE(plan_id, 0) = COALESCE(?, 0))
            """,
            (uid, date, CALENDAR_TYPE_PUMP, plan_id, plan_id),
        ).fetchall()
        synced_logs = []
        updated_count = 0
        for row in rows_to_confirm:
            cursor = conn.execute(
                """
                UPDATE calendar
                SET finish = 'true',
                    modified_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                  AND item_id = ?
                """,
                (uid, int(row["item_id"] or 0)),
            )
            updated_count += int(cursor.rowcount or 0)
            updated = conn.execute(
                """
                SELECT item_id, user_id, date, task_id, start_time, end_time,
                       content, type, source, is_milk_pump, finish
                FROM calendar
                WHERE user_id = ?
                  AND item_id = ?
                """,
                (uid, int(row["item_id"] or 0)),
            ).fetchone()
            synced_logs.append(data_store._sync_completed_calendar_item_logs(conn, updated))
    refreshed = get_today_overview(user_id=uid, target_date=date, plan_id=plan_id)
    return ok_result(
        "today_tasks_confirmed",
        f"已确认完成 {updated_count} 个今日吸奶任务。",
        {
            "user_id": uid,
            "target_date": date,
            "updated_count": updated_count,
            "synced_logs": synced_logs,
            "calendar": refreshed.get("data") if isinstance(refreshed.get("data"), dict) else {},
        },
    )


def _calendar_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    completed = len([item for item in items if item.get("finish")])
    pending = max(total - completed, 0)
    pump_count = len([item for item in items if norm_text(item.get("type")) == CALENDAR_TYPE_PUMP])
    return {
        "total_count": total,
        "completed_count": completed,
        "pending_count": pending,
        "completion_rate": int((completed * 100) / total) if total > 0 else 0,
        "pump_task_count": pump_count,
    }


def _pumping_summary(user_id: str, target_date: str) -> dict[str, Any]:
    rows = fetch_all(
        """
        SELECT pump_start_time, pump_milk_volum, pump_type
        FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time >= ?
          AND pump_start_time < ?
        ORDER BY pump_start_time ASC
        """,
        (user_id, f"{target_date} 00:00:00", _next_day_start(target_date)),
    )
    total = 0.0
    pump_lines = []
    breastfeeding_times = []
    for row in rows:
        start_dt = parse_datetime(row.get("pump_start_time"))
        time_text = start_dt.strftime("%H:%M") if start_dt else ""
        pump_type = to_int(row.get("pump_type"), 0)
        if pump_type == 2:
            if time_text:
                breastfeeding_times.append(time_text)
            continue
        milk = float(row.get("pump_milk_volum") or 0.0)
        total += milk
        if time_text:
            pump_lines.append({"time": time_text, "milk_ml": round(milk, 1)})
    return {
        "total_ml": round(total, 1),
        "pumping_count": len(pump_lines),
        "pump_lines": pump_lines,
        "breastfeeding_times_from_pumping_log": breastfeeding_times,
    }


def _feeding_summary(user_id: str, target_date: str) -> dict[str, Any]:
    rows = fetch_all(
        """
        SELECT feed_time, feed_milk_volum, feed_type
        FROM feeding_log
        WHERE user_id = ?
          AND feed_time >= ?
          AND feed_time < ?
        ORDER BY feed_time ASC
        """,
        (user_id, f"{target_date} 00:00:00", _next_day_start(target_date)),
    )
    total_bottle = 0.0
    breastfeeding_count = 0
    bottle_count = 0
    type_counts: dict[str, int] = {}
    for row in rows:
        feed_type = norm_text(row.get("feed_type")) or "unknown"
        type_counts[feed_type] = type_counts.get(feed_type, 0) + 1
        if "亲喂" in feed_type or "breast" in feed_type.lower():
            breastfeeding_count += 1
        else:
            bottle_count += 1
            total_bottle += float(row.get("feed_milk_volum") or 0.0)
    return {
        "feeding_count": len(rows),
        "breastfeeding_count": breastfeeding_count,
        "bottle_count": bottle_count,
        "total_bottle_ml": round(total_bottle, 1),
        "type_counts": type_counts,
    }


def _encouragement(
    calendar_summary: dict[str, Any],
    pumping_summary: dict[str, Any],
    feeding_summary: dict[str, Any],
) -> str:
    rate = to_int(calendar_summary.get("completion_rate"), 0)
    has_record = pumping_summary.get("pumping_count") or feeding_summary.get("feeding_count")
    if rate >= 90:
        return "今天执行得很稳，继续保持这个节奏就很好。"
    if rate >= 50:
        return "今天已经完成了不少安排，按身体状态稳稳推进就好。"
    if has_record:
        return "今天虽然节奏还在调整，但已经有记录和行动，后面可以继续一点点补齐。"
    return "今天先把节奏稳住，能完成一小步也很有价值。"


def _target_date(value: str | None) -> str:
    parsed = parse_datetime(value)
    return parsed.date().isoformat() if parsed is not None else datetime.now().date().isoformat()


def _next_day_start(target_date: str) -> str:
    parsed = parse_datetime(target_date)
    base = parsed.date() if parsed is not None else datetime.now().date()
    return (base + timedelta(days=1)).isoformat() + " 00:00:00"


def _minute_of_day(value: Any) -> int | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return parsed.hour * 60 + parsed.minute


def _db_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _format_number(value: float) -> str:
    rounded = round(float(value or 0), 1)
    return str(int(rounded)) if rounded.is_integer() else str(rounded)
