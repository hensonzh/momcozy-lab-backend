from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any


END_REASON_TEXT = {
    "user-confirm": "手动确认结束",
    "device-offline-ended-single": "检测到设备离线后结束",
    "device-offline-ended-both": "两侧设备均离线后自动结束",
    "pause-timeout-ended": "暂停超时后自动结束",
}


def build_pump_session_summary(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = _text(payload.get("user_id"))
    if not user_id:
        return _error("missing_user_id", "user_id is required")

    left = _normalize_side(_first_present(payload, "left", "device_left", "process_left"))
    right = _normalize_side(_first_present(payload, "right", "device_right", "process_right"))
    ended_at = _text(_first_present(payload, "ended_at", "endedAt", "at")) or _now_iso()
    started_at = _text(_first_present(payload, "started_at", "startedAt"))
    duration_seconds = _duration_seconds(payload, started_at=started_at, ended_at=ended_at, left=left, right=right)
    end_reason = _text(_first_present(payload, "end_reason", "reason")) or "unknown"
    process_all = _number(_first_present(payload, "process_all", "processAll"))
    if process_all is None:
        process_all = _process_from_sides(left, right)

    left_milk = left.get("milk_ml")
    right_milk = right.get("milk_ml")
    milk_values = [value for value in (left_milk, right_milk) if value is not None]
    total_milk = _number(_first_present(payload, "total_milk_ml", "totalMilkMl"))
    if total_milk is None and milk_values:
        total_milk = round(sum(milk_values), 1)

    result_overview = _result_overview(
        end_reason=end_reason,
        duration_seconds=duration_seconds,
        total_milk_ml=total_milk,
    )
    side_summary = _side_summary(left=left, right=right, total_milk_ml=total_milk)
    process_summary = _process_summary(process_all)
    letdown_summary = _letdown_summary(left=left, right=right)
    safety_note = (
        "如出现发热、明显红肿、剧痛或硬块加重，建议及时寻求专业帮助。"
    )
    summary_parts = [part for part in (side_summary, process_summary) if part]
    summary = "".join(summary_parts)
    content = "".join([result_overview, summary, safety_note])
    event_id = _text(_first_present(payload, "event_id", "eventId", "idempotency_key"))
    if not event_id:
        event_id = f"pump-summary-{_compact_timestamp(ended_at)}"

    session = {
        "event_id": event_id,
        "ended_at": ended_at,
        "end_reason": end_reason,
        "duration_seconds": _json_safe_value(duration_seconds),
        "total_milk_ml": _json_safe_value(total_milk),
        "left_milk_ml": _json_safe_value(left_milk),
        "right_milk_ml": _json_safe_value(right_milk),
        "process_all": _json_safe_value(process_all),
    }
    context_event = {
        key: value
        for key, value in session.items()
        if key != "event_id"
    }
    context_text = _agent_context_text(context_event)
    timestamp = _display_time(ended_at)
    analysis_card = _analysis_card(
        end_reason=end_reason,
        duration_seconds=duration_seconds,
        total_milk_ml=total_milk,
        left_milk_ml=left_milk,
        right_milk_ml=right_milk,
        process_all=process_all,
        side_summary=side_summary,
        process_summary=process_summary,
        letdown_summary=letdown_summary,
        safety_note=safety_note,
    )

    return {
        "ok": True,
        "status": "pump_session_summary_ready",
        "context_text": context_text,
        "data": {
            "error": 0,
            "session": session,
            "chat_message": {
                "id": event_id,
                "role": "mai",
                "content": content,
                "timestamp": timestamp,
                "cardType": "report",
                "cardData": {
                    "kind": "pump-session-summary",
                    "event_id": event_id,
                    "analysisCard": analysis_card,
                },
            },
        },
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "status": code, "data": {"error": -1, "message": message}}


def _normalize_side(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"connected": False, "milk_ml": None, "process": None, "mode": "", "level": None, "duration_seconds": None}
    return {
        "connected": _bool(value.get("connected"), default=True),
        "milk_ml": _number(
            _first_present(value, "milk_ml", "milkMl", "final_milk_ml", "finalMilkMl", "milk")
        ),
        "process": _number(value.get("process")),
        "mode": _text(_first_present(value, "mode", "pumpMode")),
        "level": _number(_first_present(value, "level", "gear")),
        "duration_seconds": _number(_first_present(value, "duration_seconds", "duration")),
        "has_milk": _optional_bool(value.get("has_milk") if "has_milk" in value else value.get("hasMilk")),
        "has_letdown": _optional_bool(value.get("has_letdown") if "has_letdown" in value else value.get("hasLetdown")),
    }


def _duration_seconds(payload: dict[str, Any], *, started_at: str, ended_at: str, left: dict[str, Any], right: dict[str, Any]) -> int | None:
    direct = _number(_first_present(payload, "duration_seconds", "durationSeconds"))
    if direct is not None:
        return max(int(round(direct)), 0)

    side_durations = [value for value in (left.get("duration_seconds"), right.get("duration_seconds")) if value is not None]
    if side_durations:
        return max(int(round(max(side_durations))), 0)

    start_dt = _parse_datetime(started_at)
    end_dt = _parse_datetime(ended_at)
    if start_dt is None or end_dt is None:
        return None
    return max(int(round((end_dt - start_dt).total_seconds())), 0)


def _process_from_sides(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    values = [value for value in (left.get("process"), right.get("process")) if value is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _result_overview(*, end_reason: str, duration_seconds: int | None, total_milk_ml: float | None) -> str:
    reason_text = END_REASON_TEXT.get(end_reason, "本次吸奶已结束")
    if end_reason in END_REASON_TEXT:
        prefix = f"{reason_text}，"
    else:
        prefix = "本次吸奶已结束，"
    duration = f"用时 {_format_duration(duration_seconds)}，" if duration_seconds is not None else ""
    if total_milk_ml is None:
        return f"{prefix}{duration}暂未获取到奶量数据。"
    return f"{prefix}{duration}总奶量约 {_format_ml(total_milk_ml)}。"


def _side_summary(*, left: dict[str, Any], right: dict[str, Any], total_milk_ml: float | None) -> str:
    left_milk = left.get("milk_ml")
    right_milk = right.get("milk_ml")
    if left_milk is not None and right_milk is not None:
        diff = abs(float(left_milk) - float(right_milk))
        base = f"左侧 {_format_ml(left_milk)}，右侧 {_format_ml(right_milk)}，"
        if diff <= 15:
            return f"{base}左右比较接近。"
        return f"{base}左右奶量有些差异，下次可以留意贴合、姿势和法兰舒适度。"
    if left_milk is not None:
        return f"本次主要记录到左侧吸奶数据，左侧约 {_format_ml(left_milk)}。"
    if right_milk is not None:
        return f"本次主要记录到右侧吸奶数据，右侧约 {_format_ml(right_milk)}。"
    if total_milk_ml is not None:
        return "暂未获取到左右分侧奶量。"
    return ""


def _process_summary(process_all: float | None) -> str:
    if process_all is None:
        return "可以先休息、补水，并按身体感受观察。"
    if process_all >= 100:
        return "本次吸奶进程完成度较高，可以先休息、补水。"
    if process_all >= 80:
        return "本次吸奶已接近目标，可以按身体感受休息。"
    return "本次吸奶提前结束，不需要强行补足时长，按身体感受休息即可。"


def _analysis_card(
    *,
    end_reason: str,
    duration_seconds: int | None,
    total_milk_ml: float | None,
    left_milk_ml: float | None,
    right_milk_ml: float | None,
    process_all: float | None,
    side_summary: str,
    process_summary: str,
    letdown_summary: str,
    safety_note: str,
) -> dict[str, Any]:
    status_label, status_tone = _status_badge(process_all=process_all, total_milk_ml=total_milk_ml)
    return {
        "kind": "pump_session_summary",
        "title": "吸奶小结",
        "status": "normal" if status_tone == "normal" else "attention",
        "status_label": status_label,
        "status_tone": status_tone,
        "sections": [
            {
                "id": "overview",
                "title": "本次概览",
                "tone": "overview",
                "metrics": [
                    {"label": "结束方式", "value": END_REASON_TEXT.get(end_reason, "本次吸奶已结束")},
                    {"label": "用时", "value": _format_duration(duration_seconds) if duration_seconds is not None else "未获取"},
                    {"label": "总奶量", "value": _format_optional_ml(total_milk_ml)},
                ],
            },
            {
                "id": "milk",
                "title": "左右奶量",
                "tone": "milk",
                "metrics": [
                    {"label": "左侧", "value": _format_optional_ml(left_milk_ml)},
                    {"label": "右侧", "value": _format_optional_ml(right_milk_ml)},
                    {"label": "左右对比", "value": _side_balance_label(left_milk_ml=left_milk_ml, right_milk_ml=right_milk_ml)},
                ],
                "body": side_summary,
            },
            {
                "id": "rhythm",
                "title": "完成度和奶阵",
                "tone": "rhythm",
                "metrics": [
                    {"label": "完成度", "value": _format_percent(process_all)},
                    {"label": "奶阵", "value": letdown_summary},
                ],
                "body": process_summary,
            },
            {
                "id": "next",
                "title": "接下来",
                "tone": "next",
                "items": [safety_note],
            },
        ],
    }


def _status_badge(*, process_all: float | None, total_milk_ml: float | None) -> tuple[str, str]:
    if total_milk_ml is None and process_all is None:
        return "数据不完整", "insufficient"
    if process_all is None:
        return "已结束", "normal"
    if process_all >= 100:
        return "完成度高", "normal"
    if process_all >= 80:
        return "接近完成", "normal"
    return "提前结束", "attention"


def _letdown_summary(*, left: dict[str, Any], right: dict[str, Any]) -> str:
    left_letdown = left.get("has_letdown")
    right_letdown = right.get("has_letdown")
    if left_letdown is True and right_letdown is True:
        return "双侧已检测到"
    if left_letdown is True and right_letdown is False:
        return "左侧已检测到，右侧未检测到"
    if left_letdown is False and right_letdown is True:
        return "右侧已检测到，左侧未检测到"
    if left_letdown is True:
        return "左侧已检测到"
    if right_letdown is True:
        return "右侧已检测到"
    if left_letdown is False and right_letdown is False:
        return "未检测到"
    return "未获取"


def _side_balance_label(*, left_milk_ml: float | None, right_milk_ml: float | None) -> str:
    if left_milk_ml is None or right_milk_ml is None:
        return "未完整记录"
    diff = abs(float(left_milk_ml) - float(right_milk_ml))
    if diff <= 15:
        return "比较接近"
    return f"相差 {_format_ml(diff)}"


def _format_optional_ml(value: float | int | None) -> str:
    if value is None:
        return "未获取"
    return _format_ml(value)


def _format_percent(value: float | int | None) -> str:
    if value is None:
        return "未获取"
    numeric = float(value)
    if math.isclose(numeric, round(numeric)):
        return f"{int(round(numeric))}%"
    return f"{round(numeric, 1):g}%"


def _agent_context_text(event: dict[str, Any]) -> str:
    facts = {
        key: _json_safe_value(value)
        for key, value in event.items()
        if value is not None and str(value).strip() != ""
    }
    detail = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    return f"pump_session_ended {detail}"


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, float) and math.isclose(value, round(value)):
        return int(round(value))
    return value


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    minutes = int(round(seconds / 60))
    if minutes <= 0:
        return f"{seconds} 秒"
    return f"{minutes} 分钟"


def _format_ml(value: float | int) -> str:
    numeric = float(value)
    if math.isclose(numeric, round(numeric)):
        return f"{int(round(numeric))} ml"
    return f"{round(numeric, 1):g} ml"


def _display_time(value: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return datetime.now().strftime("%H:%M")
    return parsed.strftime("%H:%M")


def _compact_timestamp(value: str) -> str:
    text = _text(value)
    return "".join(ch for ch in text if ch.isdigit())[:14] or str(int(datetime.now().timestamp()))


def _parse_datetime(value: Any) -> datetime | None:
    token = _text(value)
    if not token:
        return None
    normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(token, fmt)
            except ValueError:
                continue
    return None


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return round(parsed, 1)


def _bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "online", "connected"}


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "y"}:
        return True
    if token in {"0", "false", "no", "n"}:
        return False
    return None
