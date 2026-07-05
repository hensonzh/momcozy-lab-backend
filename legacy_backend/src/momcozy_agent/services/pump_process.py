from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import data_store


PROCESS_REST_THRESHOLD = 100
PROCESS_ENERGY_UPPER_VALUE = 100
PROCESS_ENERGY_LOWER_VALUE = 80


def validate_pump_process_payload(payload: Any) -> tuple[bool, str, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return False, "request body must be a JSON object", None

    user_id = str(payload.get("user_id", "") or "").strip()
    if not user_id:
        return False, "user_id is required", None

    normalized = dict(payload)
    normalized["user_id"] = user_id
    for side in ("process_left", "process_right"):
        item = payload.get(side)
        if not isinstance(item, dict):
            return False, f"{side} must be an object", None
        try:
            normalized[side] = _normalize_process_side(side, item)
        except ValueError as exc:
            return False, str(exc), None

    return True, "", normalized


def get_pump_process_reply_state(user_id: str) -> dict[str, Any]:
    return data_store.get_pump_process_reply_state(user_id)


def append_pump_process_points(payload: dict[str, Any], *, reply_state: dict[str, Any] | None = None) -> None:
    user_id = str(payload.get("user_id", "") or "").strip()
    data_store.record_pump_process(user_id, payload)
    data_store.set_pump_process_reply_state(user_id, reply_state)


def build_pump_process_reply(
    *,
    current_payload: dict[str, Any],
    previous_reply_state: dict[str, Any] | None,
    current_workstate_event: dict[str, Any] | None,
) -> dict[str, Any]:
    left = current_payload.get("process_left") if isinstance(current_payload.get("process_left"), dict) else {}
    right = current_payload.get("process_right") if isinstance(current_payload.get("process_right"), dict) else {}
    max_process = max(int(left.get("process", 0) or 0), int(right.get("process", 0) or 0))
    state = dict(previous_reply_state or {})
    state["last_process"] = max_process
    state["last_workstate"] = current_workstate_event if isinstance(current_workstate_event, dict) else {}

    if max_process >= PROCESS_REST_THRESHOLD:
        return _deduped_reply(
            state,
            code="process_rest_after_20s_at_100",
            output="当前吸乳进度已达到目标值，可以根据体感准备休息。",
            side="global",
        )
    if max_process >= PROCESS_ENERGY_LOWER_VALUE:
        return _deduped_reply(
            state,
            code="process_target_80",
            output="当前吸乳进度已接近目标值，请留意舒适度并准备结束。",
            side="global",
        )

    letdown_sides = [side for side, item in (("left", left), ("right", right)) if item.get("has_letdown")]
    if letdown_sides:
        return _deduped_reply(
            state,
            code="letdown_detected",
            output="检测到奶阵信号，当前节奏有效，请在舒适范围内保持。",
            side="both" if len(letdown_sides) == 2 else letdown_sides[0],
        )

    state["last_reply_code"] = ""
    return _reply(updated_reply_state=state)


def _normalize_process_side(field: str, payload: dict[str, Any]) -> dict[str, Any]:
    item = dict(payload)
    process = _int(payload.get("process"), f"{field}.process")
    if process < 0 or process > PROCESS_REST_THRESHOLD:
        raise ValueError(f"{field}.process must be between 0 and {PROCESS_REST_THRESHOLD}")
    item["process"] = process

    milk_reel = _int(payload.get("milk_reel"), f"{field}.milk_reel")
    if milk_reel < 0:
        raise ValueError(f"{field}.milk_reel must be greater than or equal to 0")
    item["milk_reel"] = milk_reel
    item["has_milk"] = bool(milk_reel & 0b1)
    item["has_letdown"] = bool(milk_reel & 0b10)
    item["time"] = _normalize_utc_time(payload.get("time"), f"{field}.time")
    return item


def _deduped_reply(state: dict[str, Any], *, code: str, output: str, side: str) -> dict[str, Any]:
    if state.get("last_reply_code") == code:
        return _reply(updated_reply_state=state)
    state["last_reply_code"] = code
    return _reply(need_reply=True, output=output, reply_code=code, reply_side=side, updated_reply_state=state)


def _reply(
    *,
    need_reply: bool = False,
    output: str = "",
    reply_code: str = "",
    reply_side: str = "",
    direct_rich_text: dict[str, Any] | None = None,
    updated_reply_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "need_reply": bool(need_reply),
        "output": str(output or ""),
        "reply_code": str(reply_code or ""),
        "reply_side": str(reply_side or ""),
        "direct_rich_text": direct_rich_text if isinstance(direct_rich_text, dict) else None,
        "updated_reply_state": updated_reply_state if isinstance(updated_reply_state, dict) else {},
    }


def _normalize_utc_time(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"{field} must be a valid UTC time string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
