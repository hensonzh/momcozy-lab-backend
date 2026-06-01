from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict

CALENDAR_TYPE_PUMP = "吸奶"
CALENDAR_TYPE_NURSING = "亲喂"
CALENDAR_TYPE_CUSTOM = "自定义"

CALENDAR_SOURCE_SYSTEM = "系统生成"
CALENDAR_SOURCE_USER = "用户输入"

PLAN_TYPE_INCREASE = "increase_milk"
PLAN_TYPE_MAINTAIN = "maintain_milk"
PLAN_TYPE_DECREASE = "decrease_milk"

CalendarType = Literal["吸奶", "亲喂", "自定义"]
CalendarSource = Literal["系统生成", "用户输入"]
PlanType = Literal["increase_milk", "maintain_milk", "decrease_milk"]


class ServiceResult(TypedDict, total=False):
    ok: bool
    status: str
    summary: str
    data: dict[str, Any]
    card: dict[str, Any]
    error: str


class CalendarItem(TypedDict, total=False):
    item_id: int
    user_id: str
    plan_id: int | None
    date: str
    task_id: int | None
    start_time: str | None
    end_time: str | None
    content: str | None
    type: CalendarType
    source: CalendarSource
    is_milk_pump: bool
    finish: bool
    created_at: str | None
    modified_at: str | None
    time: str
    time_point: str


def ok_result(status: str, summary: str = "", data: dict[str, Any] | None = None) -> ServiceResult:
    result: ServiceResult = {"ok": True, "status": status}
    if summary:
        result["summary"] = summary
    if data is not None:
        result["data"] = data
    return result


def error_result(status: str, summary: str, *, error: str | None = None, data: dict[str, Any] | None = None) -> ServiceResult:
    result: ServiceResult = {"ok": False, "status": status, "summary": summary}
    if error:
        result["error"] = error
    if data is not None:
        result["data"] = data
    return result


def norm_text(value: Any) -> str:
    return str(value or "").strip()


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    token = norm_text(value).lower()
    if token in {"1", "true", "yes", "y", "on", "finish", "done", "completed", "complete"}:
        return True
    if token in {"0", "false", "no", "n", "off", "unfinished", "pending", "not_done"}:
        return False
    return bool(token)


def parse_datetime(value: Any) -> datetime | None:
    token = norm_text(value)
    if not token:
        return None
    if token.endswith("Z"):
        token = token[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(token)
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%H:%M:%S",
        "%H:%M",
    ):
        try:
            return datetime.strptime(token, fmt)
        except ValueError:
            continue
    return None


def hhmm(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return f"{parsed.hour:02d}:{parsed.minute:02d}"
