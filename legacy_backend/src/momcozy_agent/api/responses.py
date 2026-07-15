from __future__ import annotations

from typing import Any


def basic_sync_response(*, status: int, message: str, error: int) -> dict[str, Any]:
    return {"status": int(status), "message": str(message), "data": {"error": int(error)}}


def pump_reply_response(
    *,
    status: int,
    message: str,
    error: int,
    need_reply: bool = False,
    output: str = "",
    reply_code: str = "",
    reply_side: str = "",
    direct_rich_text: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": int(status),
        "message": str(message),
        "data": {
            "error": int(error),
            "need_reply": bool(need_reply),
            "output": str(output or ""),
            "reply_code": str(reply_code or ""),
            "reply_side": str(reply_side or ""),
            "direct_rich_text": direct_rich_text if isinstance(direct_rich_text, dict) else None,
        },
    }


def pump_threshold_response(
    *,
    status: int,
    message: str,
    error: int,
    stimulate_level_l: int | None = None,
    deep_level_l: int | None = None,
    stimulate_level_r: int | None = None,
    deep_level_r: int | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"error": int(error)}
    if None not in (stimulate_level_l, deep_level_l, stimulate_level_r, deep_level_r):
        data.update(
            {
                "stimulate_level_l": int(stimulate_level_l or 0),
                "deep_level_l": int(deep_level_l or 0),
                "stimulate_level_r": int(stimulate_level_r or 0),
                "deep_level_r": int(deep_level_r or 0),
            }
        )
    return {"status": int(status), "message": str(message), "data": data}


def pump_health_response(*, error: int, health_l: int | None = None, health_r: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": int(error) == 0, "error": int(error)}
    if health_l is not None:
        payload["health_l"] = int(health_l)
    if health_r is not None:
        payload["health_r"] = int(health_r)
    return payload
