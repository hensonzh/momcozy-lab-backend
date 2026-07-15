from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SUPPORTED_MILK_PLAN_DIRECTIONS = frozenset({"increase", "maintain", "decrease"})
DEFAULT_PUMPING_TIMES = ("07:00", "10:00", "13:00", "16:00", "19:00", "22:00")
MAX_PUMPING_TIMES = 10


class MilkPlanDraftError(ValueError):
    pass


def build_milk_plan_draft(
    *,
    analysis_context: dict[str, Any],
    direction: str,
    timezone_name: str = "UTC",
    start_date: str = "",
    days: int = 7,
    target_daily_ml: float | None = None,
    preferred_pumping_times: list[str] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in SUPPORTED_MILK_PLAN_DIRECTIONS:
        raise MilkPlanDraftError("unsupported_milk_plan_direction")
    normalized_days = _bounded_days(days)
    base_date = today or datetime.now(_timezone(timezone_name)).date()
    normalized_start_date = _start_date(start_date, today=base_date)
    current_daily_ml = _current_daily_ml(analysis_context)
    normalized_target_daily_ml = _target_daily_ml(
        direction=normalized_direction,
        current_daily_ml=current_daily_ml,
        explicit_target=target_daily_ml,
    )

    explicit_times = _normalized_times(preferred_pumping_times or [])
    recent_times = _recent_pumping_times(analysis_context, timezone_name=timezone_name)
    base_times = explicit_times or recent_times or list(DEFAULT_PUMPING_TIMES)
    pumping_times = _direction_times(base_times, direction=normalized_direction)
    tasks = [
        {
            "title": _task_title(normalized_direction),
            "time": task_time,
            "task_type": "pumping",
            "description": _task_description(normalized_direction),
        }
        for task_time in pumping_times
    ]
    labels = {
        "increase": ("追奶", "在近期可执行节奏上温和增加 1 个吸奶时点"),
        "maintain": ("稳奶", "沿用近期可执行节奏，保持记录和复盘"),
        "decrease": ("温和减奶", "在近期节奏上先减少 1 个吸奶时点并观察舒适度"),
    }
    plan_label, strategy_summary = labels[normalized_direction]
    goal = {
        "basis": "measured_pumping_average_7d",
        "current_daily_ml": current_daily_ml,
        "target_daily_ml": normalized_target_daily_ml,
    }
    payload = {
        "direction": normalized_direction,
        "start_date": normalized_start_date.isoformat(),
        "days": normalized_days,
        "tasks": tasks,
        "goal": goal,
        "strategy_summary": strategy_summary,
        "checkpoints": [day for day in (3, 7) if day <= normalized_days],
        "observation_items": ["宝宝尿布和精神状态", "妈妈乳房舒适度", "实际奶量与执行负担"],
        "safety_notes": [
            "一次只调整一个节奏点，先观察身体和宝宝反应。",
            "如出现发热、寒战、红肿热痛加重，或宝宝摄入与精神状态异常，暂停计划并寻求医生或 IBCLC 支持。",
        ],
        "generation": {
            "mode": "runtime_deterministic",
            "timezone": _timezone_name(timezone_name),
        },
    }
    summary = _summary(
        plan_label=plan_label,
        days=normalized_days,
        strategy_summary=strategy_summary,
        current_daily_ml=current_daily_ml,
        target_daily_ml=normalized_target_daily_ml,
    )
    return {
        "title": f"{normalized_days} 天{plan_label}计划",
        "summary": summary,
        "payload": payload,
    }


def _bounded_days(value: Any) -> int:
    if isinstance(value, bool):
        raise MilkPlanDraftError("invalid_milk_plan_days")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MilkPlanDraftError("invalid_milk_plan_days") from exc
    if parsed < 1 or parsed > 30:
        raise MilkPlanDraftError("invalid_milk_plan_days")
    return parsed


def _start_date(value: str, *, today: date) -> date:
    if not str(value or "").strip():
        return today + timedelta(days=1)
    try:
        parsed = date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise MilkPlanDraftError("invalid_milk_plan_start_date") from exc
    if parsed < today:
        raise MilkPlanDraftError("milk_plan_start_date_in_past")
    return parsed


def _current_daily_ml(analysis_context: dict[str, Any]) -> float:
    snapshot = analysis_context.get("records_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    volumes = snapshot.get("volumes")
    volumes = volumes if isinstance(volumes, dict) else {}
    value = volumes.get("average_daily_pumped_volume_ml")
    if isinstance(value, bool):
        return 0.0
    try:
        return round(max(float(value or 0), 0.0), 1)
    except (TypeError, ValueError):
        return 0.0


def _target_daily_ml(*, direction: str, current_daily_ml: float, explicit_target: float | None) -> float:
    if explicit_target is not None:
        if isinstance(explicit_target, bool):
            raise MilkPlanDraftError("invalid_milk_plan_target")
        try:
            target = float(explicit_target)
        except (TypeError, ValueError) as exc:
            raise MilkPlanDraftError("invalid_milk_plan_target") from exc
        if target < 0 or target > 5000:
            raise MilkPlanDraftError("invalid_milk_plan_target")
        if current_daily_ml > 0:
            if direction == "increase" and target <= current_daily_ml:
                raise MilkPlanDraftError("milk_plan_target_direction_mismatch")
            if direction == "decrease" and target >= current_daily_ml:
                raise MilkPlanDraftError("milk_plan_target_direction_mismatch")
            if direction == "maintain" and abs(target - current_daily_ml) > 50:
                raise MilkPlanDraftError("milk_plan_target_direction_mismatch")
        return round(target, 1)
    if current_daily_ml <= 0:
        return 0.0
    adjustment = max(50.0, round(current_daily_ml * 0.1, 1))
    if direction == "increase":
        return round(current_daily_ml + adjustment, 1)
    if direction == "decrease":
        return round(max(current_daily_ml - adjustment, 0.0), 1)
    return current_daily_ml


def _recent_pumping_times(analysis_context: dict[str, Any], *, timezone_name: str) -> list[str]:
    snapshot = analysis_context.get("records_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    pumpings = snapshot.get("recent_pumpings")
    if not isinstance(pumpings, list):
        return []
    timezone_info = _timezone(timezone_name)
    values_by_date: dict[date, list[str]] = {}
    for pumping in pumpings:
        if not isinstance(pumping, dict):
            continue
        raw = str(pumping.get("pump_start_time") or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone_info)
        values_by_date.setdefault(parsed.date(), []).append(parsed.strftime("%H:%M"))
    if not values_by_date:
        return []
    representative_date = max(
        values_by_date,
        key=lambda item: (len(values_by_date[item]), item),
    )
    return sorted(set(values_by_date[representative_date]))[:MAX_PUMPING_TIMES]


def _normalized_times(values: list[str]) -> list[str]:
    normalized: set[str] = set()
    for value in values:
        parts = str(value or "").strip().split(":")
        if len(parts) != 2:
            raise MilkPlanDraftError("invalid_preferred_pumping_time")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise MilkPlanDraftError("invalid_preferred_pumping_time") from exc
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise MilkPlanDraftError("invalid_preferred_pumping_time")
        normalized.add(f"{hour:02d}:{minute:02d}")
    if len(normalized) > MAX_PUMPING_TIMES:
        raise MilkPlanDraftError("too_many_preferred_pumping_times")
    return sorted(normalized)


def _direction_times(base_times: list[str], *, direction: str) -> list[str]:
    values = _normalized_times(base_times)
    if direction == "maintain" or not values:
        return values or list(DEFAULT_PUMPING_TIMES)
    if direction == "increase":
        if len(values) >= MAX_PUMPING_TIMES:
            return values
        occupied = [_minutes(value) for value in values]
        candidates = range(6 * 60, 23 * 60, 30)
        selected = max(candidates, key=lambda candidate: (min(abs(candidate - value) for value in occupied), -candidate))
        return _normalized_times([*values, _hhmm(selected)])
    if len(values) <= 1:
        return values
    removable = next((value for value in reversed(values) if _minutes(value) >= 21 * 60 or _minutes(value) < 6 * 60), values[-1])
    return [value for value in values if value != removable]


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _hhmm(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _task_title(direction: str) -> str:
    return {"increase": "追奶吸奶", "maintain": "稳奶吸奶", "decrease": "舒适减奶"}[direction]


def _task_description(direction: str) -> str:
    return {
        "increase": "以舒适、可执行为准，不因单次奶量波动额外加码。",
        "maintain": "保持近期可执行节奏，并记录实际完成和身体感受。",
        "decrease": "只缓解胀痛，不追求排空；身体不适时暂停减少。",
    }[direction]


def _summary(
    *,
    plan_label: str,
    days: int,
    strategy_summary: str,
    current_daily_ml: float,
    target_daily_ml: float,
) -> str:
    measured = (
        f"近 7 天实测吸奶日均约 {current_daily_ml:g} ml，阶段目标约 {target_daily_ml:g} ml。"
        if current_daily_ml > 0
        else "近期实测奶量不足以给出可靠数字目标，本轮先按节奏和身体反应复盘。"
    )
    return f"{days} 天{plan_label}安排：{strategy_summary}。{measured}"


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _timezone_name(value: str) -> str:
    timezone_info = _timezone(value)
    return timezone_info.key
