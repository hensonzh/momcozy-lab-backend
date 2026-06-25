from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

from ...config import get_openai_client_options, load_project_env, openai_service_tier_request_kwargs
from .db import fetch_one
from .records import get_records_range
from .schemas import ServiceResult, error_result, norm_text, ok_result, parse_datetime, to_int
from .today import get_today_daily_summary_fallback

"""
基于LLM的吸奶和喂养日结服务，生成简短、温和、非诊断的中文日结，帮助妈妈了解当天的节奏和状态。
LLM失败时提供数据驱动的 fallback （today），总结吸奶和喂养记录，给出鼓励和建议，确保即使数据不足也能提供有用的信息。
"""

LONG_INTERVAL_MINUTES = 240
LATEST_RECORD_FALLBACK_DAYS = 7

DAILY_SUMMARY_PROMPT = """
你是 Momcozy 的母婴喂养日结助手。请基于输入的当天喂养、吸奶、亲喂、间隔和风险数据，生成温和、自然、非诊断的中文日结，像给妈妈的一段轻量提醒，不要像数据报表。

输出要求：
- 只返回 JSON：{"message":["...","...","...","...","..."]}
- message 必须恰好 5 条，依次对应：今日概览、关键数据、节律情况、风险提示、一句话建议。
- 条目间不要重复，用自然语言表达，数字要自然融入句子。
- 第 1 条以"今日"开头，总结喂养/亲喂/瓶喂/预估摄入，并给出轻量评估。
- 第 2 条总结吸奶次数和吸奶总量，并给出轻量评估。
- 第 3 条依据 rhythm.events 和 rhythm.intervals，评估吸奶与亲喂组成的排乳节律。
- 第 4 条说明是否有需要关注的风险；有则指出具体风险，没有则温和说明暂未发现明显风险。
- 第 5 条给出一句话建议，提出明日改进或保持方向。
- 每条尽量控制在 45 个中文字符以内，直接给结论，不加标题、不加项目符号。
- 保留关键数字和单位，如 次、ml、小时。
- 语气亲和、稳定、减少焦虑；不要诊断，不承诺奶量一定足够或不足。
- 数据不足时说明“记录较少/暂无法判断”，并建议继续记录。
""".strip()


def create_daily_summary(*, user_id: str, target_date: str | None = None) -> ServiceResult:
    uid = norm_text(user_id)
    if not uid:
        return error_result("missing_user_id", "缺少 user_id，无法生成今日日结。")

    requested_day = _target_date(target_date)
    day = requested_day
    records_result = _load_daily_records(uid, day)
    if not records_result.get("ok"):
        return records_result
    if not norm_text(target_date) and not _has_records(records_result):
        latest_day = _latest_record_date(uid, requested_day, lookback_days=LATEST_RECORD_FALLBACK_DAYS)
        if latest_day and latest_day != requested_day:
            day = latest_day
            records_result = _load_daily_records(uid, day)
            if not records_result.get("ok"):
                return records_result

    data = records_result.get("data") if isinstance(records_result.get("data"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    feeding = summary.get("feeding") if isinstance(summary.get("feeding"), dict) else {}
    milk_output = summary.get("milk_output") if isinstance(summary.get("milk_output"), dict) else {}
    records = data.get("records") if isinstance(data.get("records"), dict) else {}
    pumping_records = records.get("pumping") if isinstance(records.get("pumping"), list) else []
    feeding_records = records.get("feeding") if isinstance(records.get("feeding"), list) else []

    feeding_count = to_int(feeding.get("total_count"), 0)
    nursing_count = to_int(feeding.get("nursing_count"), 0)
    bottle_count = to_int(feeding.get("breastmilk_bottle_count"), 0) + to_int(feeding.get("formula_bottle_count"), 0)
    pumping_count = to_int(milk_output.get("pumping_count"), 0)
    pumped_ml = _number(milk_output.get("pumped_ml"))
    bottle_ml = _number(feeding.get("bottle_total_ml"))
    estimated_nursing_ml = _optional_number(milk_output.get("estimated_nursing_ml"))
    estimated_intake_ml = bottle_ml + (estimated_nursing_ml or 0.0)
    today_pumping = _today_pumping_summary(pumping_records, pumping_count=pumping_count, pumped_ml=pumped_ml)
    today_feeding = _today_feeding_summary(
        feeding_records,
        feeding_count=feeding_count,
        nursing_count=nursing_count,
        bottle_count=bottle_count,
        bottle_ml=bottle_ml,
    )

    rhythm_events = _milk_removal_events(pumping_records, feeding_records)
    intervals = _event_intervals(rhythm_events)
    long_intervals = [item for item in intervals if item["minutes"] > LONG_INTERVAL_MINUTES]
    today_feeding_normal = feeding_count >= 2 and not long_intervals

    message = _request_llm_daily_summary(
        _build_llm_payload(
            user_id=uid,
            target_date=day,
            feeding_count=feeding_count,
            nursing_count=nursing_count,
            pumping_count=pumping_count,
            bottle_count=bottle_count,
            pumped_ml=pumped_ml,
            bottle_ml=bottle_ml,
            estimated_nursing_ml=estimated_nursing_ml,
            estimated_intake_ml=estimated_intake_ml,
            rhythm_events=rhythm_events,
            intervals=intervals,
            long_intervals=long_intervals,
            today_feeding_normal=today_feeding_normal,
            today_pumping=today_pumping,
            today_feeding=today_feeding,
        )
    )
    if message is None:
        fallback = get_today_daily_summary_fallback(user_id=uid, target_date=day)
        fallback_data = fallback.get("data") if isinstance(fallback.get("data"), dict) else {}
        message = fallback_data.get("message") if isinstance(fallback_data.get("message"), list) else []
    used_latest_available_record = day != requested_day
    message = _apply_record_date_context(
        message,
        target_date=day,
        used_latest_available_record=used_latest_available_record,
    )
    return ok_result(
        "daily_summary_created",
        "\n".join(message),
        {
            "message": message,
            "today_feeding_normal": today_feeding_normal,
            "target_date": day,
            "requested_target_date": requested_day,
            "used_latest_available_record": used_latest_available_record,
            "record_date_label": _display_date(day),
            "long_interval_count": len(long_intervals),
        },
    )


def _load_daily_records(user_id: str, target_date: str) -> ServiceResult:
    return get_records_range(
        user_id=user_id,
        start_at=f"{target_date} 00:00:00",
        end_at=_next_day_start(target_date),
        record_scope="all",
        include_raw_records=True,
        summary_granularity="daily",
        limit=500,
    )


def _has_records(records_result: ServiceResult) -> bool:
    data = records_result.get("data") if isinstance(records_result.get("data"), dict) else {}
    return to_int(data.get("record_count"), 0) > 0


def _latest_record_date(user_id: str, target_date: str, *, lookback_days: int) -> str | None:
    target_dt = parse_datetime(target_date)
    if target_dt is None:
        return None
    end_at = _next_day_start(target_date)
    start_date = target_dt.date() - timedelta(days=max(1, lookback_days) - 1)
    start_at = f"{start_date.isoformat()} 00:00:00"
    row = fetch_one(
        """
        SELECT MAX(record_date) AS latest_date
        FROM (
            SELECT date(pump_start_time) AS record_date
            FROM pumping_log
            WHERE user_id = ?
              AND pump_start_time >= ?
              AND pump_start_time < ?
            UNION ALL
            SELECT date(feed_time) AS record_date
            FROM feeding_log
            WHERE user_id = ?
              AND feed_time >= ?
              AND feed_time < ?
        )
        """,
        (user_id, start_at, end_at, user_id, start_at, end_at),
    )
    latest = norm_text(row.get("latest_date") if row else "")
    return latest or None


def _build_llm_payload(
    *,
    user_id: str,
    target_date: str,
    feeding_count: int,
    nursing_count: int,
    pumping_count: int,
    bottle_count: int,
    pumped_ml: float,
    bottle_ml: float,
    estimated_nursing_ml: float | None,
    estimated_intake_ml: float,
    rhythm_events: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
    long_intervals: list[dict[str, Any]],
    today_feeding_normal: bool,
    today_pumping: dict[str, Any],
    today_feeding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "target_date": target_date,
        "today": {
            "feeding_count": feeding_count,
            "nursing_count": nursing_count,
            "bottle_count": bottle_count,
            "pumping_count": pumping_count,
            "pumped_ml": pumped_ml,
            "bottle_ml": bottle_ml,
            "estimated_nursing_ml": estimated_nursing_ml,
            "estimated_intake_ml": round(estimated_intake_ml, 1),
            "today_feeding_normal": today_feeding_normal,
        },
        "today_activity_summary": {
            "pumping": today_pumping,
            "feeding": today_feeding,
        },
        "rhythm": {
            "basis": "milk_removal_events_from_pumping_and_nursing_records",
            "events": [_event_payload(item) for item in rhythm_events],
            "intervals": [_interval_payload(item) for item in intervals],
            "long_interval_minutes": LONG_INTERVAL_MINUTES,
            "long_intervals": [_interval_payload(item) for item in long_intervals],
            "long_interval_count": len(long_intervals),
        },
    }


def _request_llm_daily_summary(payload: dict[str, Any]) -> list[str] | None:
    load_project_env()
    try:
        from openai import OpenAI
    except ImportError:
        return None

    try:
        client = OpenAI(**get_openai_client_options())
        response = client.responses.create(
            model=os.getenv("DAILY_SUMMARY_MODEL", os.getenv("STATUS_ADVICE_MODEL", "gpt-5.4-mini")),
            instructions=DAILY_SUMMARY_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(payload, ensure_ascii=False),
                        }
                    ],
                }
            ],
            reasoning={"effort": "low"},
            text={"format": {"type": "text"}, "verbosity": "low"},
            store=False,
            prompt_cache_key="momcozy-daily-summary-v1",
            **openai_service_tier_request_kwargs(),
        )
    except Exception:
        return None
    return _parse_daily_summary_response(response)


def _parse_daily_summary_response(response: object) -> list[str] | None:
    text = _response_text(response)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    message = parsed.get("message")
    if not isinstance(message, list):
        return None
    cleaned = [_clean_line(item) for item in message]
    cleaned = [item for item in cleaned if item]
    if len(cleaned) != 5:
        return None
    return _normalize_message_lines(cleaned)


def _response_text(response: object) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text

    output = response.get("output", []) if isinstance(response, dict) else getattr(response, "output", [])
    if not isinstance(output, list):
        return ""

    parts: list[str] = []
    for item in output:
        item_type = _item_value(item, "type")
        if item_type == "message":
            content = _item_value(item, "content") or []
            if not isinstance(content, list):
                continue
            for content_item in content:
                if _item_value(content_item, "type") in {"output_text", "text"}:
                    text = _item_value(content_item, "text")
                    if isinstance(text, str):
                        parts.append(text)
        elif item_type == "output_text":
            text = _item_value(item, "text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _item_value(item: object, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _clean_line(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = text.lstrip("•-·、. ")
    return text[:60]


def _normalize_message_lines(message: list[str]) -> list[str]:
    if not message:
        return message
    first = message[0].strip()
    if first and not first.startswith("今日"):
        first = f"今日{first}"
    message[0] = first
    return message


def _apply_record_date_context(
    message: Any,
    *,
    target_date: str,
    used_latest_available_record: bool,
) -> list[str]:
    lines = [str(item).strip() for item in message] if isinstance(message, list) else []
    lines = [line for line in lines if line]
    if not used_latest_available_record or not lines:
        return lines

    first = lines[0]
    if first.startswith("今日"):
        first = first[2:].lstrip("，,：: ")
    prefix = f"最近记录（{_display_date(target_date)}）"
    lines[0] = f"{prefix}：{first}" if first else f"{prefix}已同步。"
    return lines


def _milk_removal_events(pumping_records: list[Any], feeding_records: list[Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in pumping_records:
        if not isinstance(record, dict):
            continue
        parsed = parse_datetime(record.get("occurred_at") or record.get("pump_start_time") or record.get("time"))
        if parsed is not None:
            events.append({"time": parsed, "kind": "pumping", "label": "吸奶"})
    for record in feeding_records:
        if not isinstance(record, dict):
            continue
        kind = norm_text(record.get("record_kind") or record.get("feed_type"))
        if kind not in {"nursing", "亲喂"}:
            continue
        parsed = parse_datetime(record.get("occurred_at") or record.get("feed_time") or record.get("time"))
        if parsed is not None:
            events.append({"time": parsed, "kind": "nursing", "label": "亲喂"})
    events.sort(key=lambda item: item["time"])
    return events


def _today_pumping_summary(pumping_records: list[Any], *, pumping_count: int, pumped_ml: float) -> dict[str, Any]:
    pump_lines = []
    for record in pumping_records:
        if not isinstance(record, dict):
            continue
        parsed = parse_datetime(record.get("occurred_at"))
        time_text = parsed.strftime("%H:%M") if parsed is not None else ""
        if time_text:
            pump_lines.append({"time": time_text, "milk_ml": _number(record.get("amount_ml"))})
    return {
        "total_ml": round(float(pumped_ml or 0), 1),
        "pumping_count": int(pumping_count),
        "pump_lines": pump_lines,
    }


def _today_feeding_summary(
    feeding_records: list[Any],
    *,
    feeding_count: int,
    nursing_count: int,
    bottle_count: int,
    bottle_ml: float,
) -> dict[str, Any]:
    type_counts: dict[str, int] = {}
    for record in feeding_records:
        if not isinstance(record, dict):
            continue
        feed_type = norm_text(record.get("feed_type") or record.get("record_kind")) or "unknown"
        type_counts[feed_type] = type_counts.get(feed_type, 0) + 1
    return {
        "feeding_count": int(feeding_count),
        "breastfeeding_count": int(nursing_count),
        "bottle_count": int(bottle_count),
        "total_bottle_ml": round(float(bottle_ml or 0), 1),
        "type_counts": type_counts,
    }


def _event_intervals(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    times: list[dict[str, Any]] = []
    for event in events:
        parsed = event.get("time")
        if isinstance(parsed, datetime):
            times.append(event)
    intervals = []
    for previous, current in zip(times, times[1:]):
        previous_time = previous["time"]
        current_time = current["time"]
        minutes = int((current_time - previous_time).total_seconds() / 60)
        if minutes > 0:
            intervals.append(
                {
                    "start": previous_time,
                    "end": current_time,
                    "minutes": minutes,
                    "from_kind": previous.get("kind"),
                    "to_kind": current.get("kind"),
                    "from_label": previous.get("label"),
                    "to_label": current.get("label"),
                }
            )
    return intervals


def _event_payload(item: dict[str, Any]) -> dict[str, Any]:
    event_time = item.get("time")
    return {
        "time": event_time.strftime("%H:%M") if isinstance(event_time, datetime) else "",
        "kind": str(item.get("kind") or ""),
        "label": str(item.get("label") or ""),
        "period": _period_text(event_time),
    }


def _interval_payload(item: dict[str, Any]) -> dict[str, Any]:
    start = item.get("start")
    end = item.get("end")
    return {
        "start": start.strftime("%H:%M") if isinstance(start, datetime) else "",
        "end": end.strftime("%H:%M") if isinstance(end, datetime) else "",
        "minutes": int(item.get("minutes") or 0),
        "period": _period_text(end),
        "from": str(item.get("from_label") or ""),
        "to": str(item.get("to_label") or ""),
    }


def _period_text(value: Any) -> str:
    if isinstance(value, datetime):
        hour = value.hour
    else:
        parsed = parse_datetime(value)
        hour = parsed.hour if parsed is not None else -1
    if 5 <= hour < 12:
        return "上午"
    if 12 <= hour < 18:
        return "下午"
    if 18 <= hour < 24:
        return "晚上"
    return "夜间"


def _target_date(value: str | None) -> str:
    parsed = parse_datetime(value)
    return parsed.date().isoformat() if parsed is not None else datetime.now().date().isoformat()


def _next_day_start(target_date: str) -> str:
    parsed = parse_datetime(target_date)
    base = parsed.date() if parsed is not None else datetime.now().date()
    return (base + timedelta(days=1)).isoformat() + " 00:00:00"


def _display_date(value: str) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return value
    return f"{parsed.month}月{parsed.day}日"


def _number(value: Any) -> float:
    try:
        return round(float(value or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    return _number(value)


def _format_number(value: float) -> str:
    rounded = round(float(value or 0), 1)
    return str(int(rounded)) if rounded.is_integer() else str(rounded)
