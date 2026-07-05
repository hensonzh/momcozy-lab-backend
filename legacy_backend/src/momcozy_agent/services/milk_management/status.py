from __future__ import annotations

from datetime import datetime
from typing import Any

from .. import data_store
from .feeding import assess_feeding_demand_reference
from .schemas import ServiceResult, error_result, norm_text, ok_result, parse_datetime, to_int
######################################已废弃，后续会删除######################################
STATUS_SECTIONS = {"all", "overview", "today", "trend", "growth", "tasks"}


def query_milk_status(
    *,
    user_id: str,
    section: str = "all",
    target_date: str | None = None,
    trend_days: int = 30,
    growth_history_limit: int = 10,
    include_tasks: bool = True,
) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法读取奶量状态。")

    selected_section = norm_text(section) or "all"
    if selected_section not in STATUS_SECTIONS:
        return error_result("invalid_status_section", f"Unsupported status section: {section}")

    date_text = _date_text(target_date)
    days = min(max(to_int(trend_days, 30), 1), 30)
    history_limit = min(max(to_int(growth_history_limit, 10), 1), 50)

    data: dict[str, Any] = {
        "user_id": uid,
        "section": selected_section,
        "target_date": date_text,
        "missing": [],
    }

    if selected_section in {"all", "overview"}:
        info = data_store.get_mom_baby_info(uid)
        data["mom_baby_info"] = info or {}
        if not info:
            data["missing"].append("mom_baby_info")
        data["feeding_reference"] = assess_feeding_demand_reference(user_id=uid, as_of_date=date_text)

    if selected_section in {"all", "today"}:
        today = data_store.get_mom_baby_today_summary(uid, date_text)
        data["today"] = today or {}
        if today is None:
            data["missing"].append("today_summary")
        data["feeding_reference"] = assess_feeding_demand_reference(user_id=uid, as_of_date=date_text)

    if selected_section in {"all", "trend"}:
        pump_info = data_store.pump_info(uid)
        trend_items = list(pump_info.get("lactation_info_list") or [])[-days:]
        data["pump_info"] = {key: value for key, value in pump_info.items() if key != "lactation_info_list"}
        data["trend"] = {
            "days": len(trend_items),
            "items": trend_items,
        }

    if selected_section in {"all", "growth"}:
        history = data_store.growth_records_for_user(uid)
        data["growth"] = {
            "latest": data_store.latest_growth_record_for_user(uid) or {},
            "history": history[:history_limit],
            "history_count": len(history),
            "history_limit": history_limit,
        }
        if not history:
            data["missing"].append("growth_history")

    if include_tasks or selected_section == "tasks":
        tasks = data_store.query_plan_tasks(user_id=uid, target_date=date_text)
        data["tasks"] = tasks or {"plan_type": "None", "task_list": []}
        if tasks is None:
            data["missing"].append("tasks")

    loaded = sorted(key for key in data.keys() if key not in {"user_id", "section", "target_date", "missing"})
    if selected_section == "all":
        data["status_page_tabs"] = _status_page_tabs(data)

    result = ok_result(
        "milk_status_loaded",
        "已读取奶量状态聚合信息。",
        {
            **data,
            "loaded_sections": loaded,
        },
    )
    if selected_section == "all":
        result["card"] = _status_page_card(data)
    return result


def _date_text(value: Any) -> str:
    parsed = parse_datetime(value) if value else datetime.now()
    if parsed is None:
        token = norm_text(value)
        return token[:10] if token else datetime.now().date().isoformat()
    return parsed.date().isoformat()


def _status_page_card(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"mom-baby-status:{data.get('user_id')}:{data.get('target_date')}",
        "card_type": "mom_baby_status_card",
        "schema_version": "1.0",
        "card_json": {
            "title": "母婴状态",
            "subtitle": str(data.get("target_date") or ""),
            "tabs": _status_page_tabs(data),
        },
    }


def _status_page_tabs(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [_mom_status_tab(data), _baby_status_tab(data)]


def _mom_status_tab(data: dict[str, Any]) -> dict[str, Any]:
    info = _dict(data.get("mom_baby_info"))
    today = _dict(data.get("today"))
    trend = _dict(data.get("trend"))
    tasks = _dict(data.get("tasks"))

    return {
        "id": "mom",
        "title": "妈妈数字分身",
        "summary": _clean_item(info.get("lactation_advice")) or "先看今日产奶、排乳趋势和计划执行。",
        "sections": [
            {
                "id": "mom_today",
                "title": "今日泌乳",
                "tone": "milk",
                "metrics": [
                    _metric("今日产奶", _ml(today.get("pump_milk_volum"))),
                    _metric("30日趋势", _trend_days_text(trend)),
                    _metric("今日吸奶任务", _task_count_text(tasks, is_mom_task=True)),
                ],
                "items": _items(info.get("lactation_advice")),
            },
            {
                "id": "mom_plan",
                "title": "计划执行",
                "tone": "next",
                "items": _task_items(tasks, is_mom_task=True),
            },
        ],
    }


def _baby_status_tab(data: dict[str, Any]) -> dict[str, Any]:
    info = _dict(data.get("mom_baby_info"))
    today = _dict(data.get("today"))
    growth = _dict(data.get("growth"))
    tasks = _dict(data.get("tasks"))
    latest_growth = _dict(growth.get("latest"))

    return {
        "id": "baby",
        "title": "宝宝数字分身",
        "summary": _clean_item(info.get("feeding_advice")) or "先看今日摄入、亲喂估算和最近成长记录。",
        "sections": [
            {
                "id": "baby_today",
                "title": "今日喂养",
                "tone": "feeding",
                "metrics": [
                    _metric("今日摄入", _ml(today.get("feeding_volum"))),
                    _metric("亲喂预估", _ml(today.get("feeding_forecast_volum"))),
                    _metric("今日喂养任务", _task_count_text(tasks, is_mom_task=False)),
                ],
                "items": _items(info.get("feeding_advice")),
            },
            {
                "id": "baby_growth",
                "title": "成长记录",
                "tone": "growth",
                "metrics": [
                    _metric("体重", _unit(latest_growth.get("weight_kg"), "kg")),
                    _metric("身长", _unit(latest_growth.get("height_cm"), "cm")),
                    _metric("头围", _unit(latest_growth.get("head_cm"), "cm")),
                ],
                "items": _growth_items(growth),
            },
        ],
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _metric(label: str, value: str, detail: str = "") -> dict[str, str]:
    return {"label": label, "value": value, "detail": detail}


def _items(value: Any) -> list[str]:
    item = _clean_item(value)
    return [item] if item else []


def _clean_item(value: Any) -> str:
    return " ".join(str(value or "").split())


def _ml(value: Any) -> str:
    return f"{_number(value)} ml"


def _unit(value: Any, unit: str) -> str:
    token = _number(value)
    return f"{token} {unit}" if token != "—" else token


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number <= 0:
        return "0"
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def _trend_days_text(trend: dict[str, Any]) -> str:
    days = to_int(trend.get("days"), 0)
    return f"{days} 天" if days > 0 else "—"


def _task_count_text(tasks: dict[str, Any], *, is_mom_task: bool) -> str:
    selected = _filtered_tasks(tasks, is_mom_task=is_mom_task)
    if not selected:
        return "0 项"
    finished = sum(1 for item in selected if _task_finished(item))
    return f"{finished}/{len(selected)} 项"


def _task_items(tasks: dict[str, Any], *, is_mom_task: bool) -> list[str]:
    selected = _filtered_tasks(tasks, is_mom_task=is_mom_task)
    if not selected:
        return ["今天暂无相关计划任务。"]
    return [_task_text(item) for item in selected[:4]]


def _filtered_tasks(tasks: dict[str, Any], *, is_mom_task: bool) -> list[dict[str, Any]]:
    items = tasks.get("task_list")
    if not isinstance(items, list):
        return []
    if is_mom_task:
        return [item for item in items if isinstance(item, dict) and _is_mom_task(item)]
    return [item for item in items if isinstance(item, dict) and _is_baby_task(item)]


def _is_mom_task(item: dict[str, Any]) -> bool:
    text = f"{item.get('type') or ''} {item.get('content') or ''} {item.get('calendar_title') or ''}"
    return _truthy(item.get("is_milk_pump")) or any(token in text for token in ("吸奶", "排乳", "泵奶"))


def _is_baby_task(item: dict[str, Any]) -> bool:
    text = f"{item.get('type') or ''} {item.get('content') or ''} {item.get('calendar_title') or ''}"
    return any(token in text for token in ("亲喂", "喂养", "瓶喂", "配方奶", "母乳瓶喂"))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "done", "completed", "complete", "finish"}


def _task_finished(item: dict[str, Any]) -> bool:
    token = str(item.get("finish") or "").strip().lower()
    return token in {"1", "true", "yes", "done", "completed", "complete", "finish"}


def _task_text(item: dict[str, Any]) -> str:
    time = str(item.get("time") or item.get("time_point") or item.get("start_time") or "").strip()
    title = str(item.get("content") or item.get("calendar_title") or item.get("type") or "计划任务").strip()
    status = "已完成" if _task_finished(item) else "待完成"
    return f"{time[:16]} {title} · {status}".strip()


def _growth_items(growth: dict[str, Any]) -> list[str]:
    latest = _dict(growth.get("latest"))
    if not latest:
        return ["暂无宝宝成长记录。"]
    measured_at = (
        latest.get("weight_measured_at")
        or latest.get("height_measured_at")
        or latest.get("head_measured_at")
        or latest.get("created_at")
        or ""
    )
    measured_text = str(measured_at or "").strip()[:10]
    if measured_text:
        return [f"最近一次记录：{measured_text}。"]
    return ["已读取最近一次宝宝成长记录。"]
