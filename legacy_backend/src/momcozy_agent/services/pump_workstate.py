from __future__ import annotations

from typing import Any

from . import data_store


VALID_WORKSTATES = {0, 1, 2, 3, 4}
VALID_MODES = {"", "stimulate", "deep", "mix"}
VALID_STEPS = {"start", "running", "stop", "pause", "offline"}


def validate_pump_workstate_payload(payload: Any) -> tuple[bool, str, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return False, "request body must be a JSON object", None

    user_id = str(payload.get("user_id", "") or "").strip()
    if not user_id:
        return False, "user_id is required", None

    normalized = dict(payload)
    normalized["user_id"] = user_id
    for side in ("device_left", "device_right"):
        device_payload = payload.get(side)
        if not isinstance(device_payload, dict):
            return False, f"{side} must be an object", None
        try:
            device = _normalize_workstate_device(side, device_payload)
        except ValueError as exc:
            return False, str(exc), None
        normalized[side] = device

    return True, "", normalized


def get_latest_pump_workstate_event(user_id: str) -> dict[str, Any] | None:
    return data_store.latest_workstate(user_id)


def get_pump_workstate_reply_state(user_id: str) -> dict[str, Any]:
    return data_store.get_pump_workstate_reply_state(user_id)


def record_pump_workstate_update(
    payload: dict[str, Any],
    *,
    reply_state: dict[str, Any] | None = None,
    job_operation: dict[str, Any] | None = None,
) -> None:
    user_id = str(payload.get("user_id", "") or "").strip()
    data_store.record_workstate(user_id, payload)
    data_store.set_pump_workstate_reply_state(user_id, reply_state)
    if isinstance(job_operation, dict) and job_operation.get("mode"):
        data_store.add_pending_reply(
            user_id,
            str(job_operation.get("mode") or ""),
            str(job_operation.get("output") or ""),
            job_operation.get("payload") if isinstance(job_operation.get("payload"), dict) else {},
        )


def build_pump_workstate_reply(
    *,
    current_payload: dict[str, Any],
    previous_event: dict[str, Any] | None,
    previous_reply_state: dict[str, Any] | None,
) -> dict[str, Any]:
    user_id = str(current_payload.get("user_id", "") or "").strip()
    state = dict(previous_reply_state or {})
    current_modes = _active_modes(current_payload)
    previous_modes = _active_modes(previous_event or {})
    mode_signature = ",".join(f"{side}:{mode}" for side, mode in sorted(current_modes.items()))

    state.update(
        {
            "last_mode_signature": mode_signature,
            "last_active_modes": current_modes,
        }
    )

    if not current_modes:
        state["last_reply_code"] = ""
        return _reply(updated_reply_state=state)

    if current_modes != previous_modes and mode_signature != previous_reply_state_or_empty(previous_reply_state).get("last_mode_signature"):
        if len(set(current_modes.values())) == 1:
            mode = next(iter(current_modes.values()))
            output = _mode_output(mode)
            return _reply(
                need_reply=True,
                output=output,
                reply_code=f"workstate_{mode}_started",
                reply_side="both" if len(current_modes) == 2 else next(iter(current_modes.keys())),
                updated_reply_state={**state, "last_reply_code": f"workstate_{mode}_started"},
                job_operation={"mode": mode, "output": output, "payload": {"user_id": user_id, "mode": mode, "sides": list(current_modes)}},
            )

        output = "左右两侧吸乳模式不一致，请确认当前设置是否符合预期。"
        return _reply(
            need_reply=True,
            output=output,
            reply_code="workstate_mode_mismatch",
            reply_side="global",
            updated_reply_state={**state, "last_reply_code": "workstate_mode_mismatch"},
            job_operation={"mode": "mix", "output": output, "payload": {"user_id": user_id, "modes": current_modes}},
        )

    return _reply(updated_reply_state=state)


def _normalize_workstate_device(field: str, payload: dict[str, Any]) -> dict[str, Any]:
    item = dict(payload)
    state = _int(payload.get("state"), f"{field}.state")
    if state not in VALID_WORKSTATES:
        raise ValueError(f"{field}.state must be one of 0, 1, 2, 3, 4")
    item["state"] = state

    mode = str(payload.get("mode", "") or "").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"{field}.mode must be one of stimulate, deep, mix")
    item["mode"] = mode

    step = str(payload.get("step", "") or "").strip().lower()
    if not step:
        step = "running" if state == 1 and mode else "stop"
    if step not in VALID_STEPS:
        raise ValueError(f"{field}.step must be one of start, running, stop, pause, offline")
    item["step"] = step
    return item


def _active_modes(payload: dict[str, Any]) -> dict[str, str]:
    modes: dict[str, str] = {}
    for side in ("device_left", "device_right"):
        device = payload.get(side) if isinstance(payload, dict) else None
        if not isinstance(device, dict):
            continue
        step = str(device.get("step", "") or "").lower()
        mode = str(device.get("mode", "") or "").lower()
        state = int(device.get("state", 0) or 0)
        if state == 1 and mode in {"stimulate", "deep", "mix"} and step not in {"stop", "offline"}:
            modes["left" if side == "device_left" else "right"] = mode
    return modes


def previous_reply_state_or_empty(value: dict[str, Any] | None) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mode_output(mode: str) -> str:
    if mode == "stimulate":
        return "当前进入刺激模式，保持舒适节奏并观察出奶情况。"
    if mode == "deep":
        return "当前进入深吸模式，如有不适请及时调低档位或暂停。"
    return "当前进入混合模式，请根据体感调整吸乳节奏。"


def _reply(
    *,
    need_reply: bool = False,
    output: str = "",
    reply_code: str = "",
    reply_side: str = "",
    direct_rich_text: dict[str, Any] | None = None,
    updated_reply_state: dict[str, Any] | None = None,
    job_operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "need_reply": bool(need_reply),
        "output": str(output or ""),
        "reply_code": str(reply_code or ""),
        "reply_side": str(reply_side or ""),
        "direct_rich_text": direct_rich_text if isinstance(direct_rich_text, dict) else None,
        "updated_reply_state": updated_reply_state if isinstance(updated_reply_state, dict) else {},
        "job_operation": job_operation,
    }


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
