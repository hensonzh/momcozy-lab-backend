from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .models import PumpingRecord


MIN_LIST_LIMIT = 1
MAX_LIST_LIMIT = 100
MIN_TREND_DAYS = 1
MAX_TREND_DAYS = 90
GROWTH_UPDATE_FIELDS = frozenset({"infant_id", "measured_at", "height_cm", "weight_kg", "head_cm"})
FEEDING_UPDATE_FIELDS = frozenset(
    {
        "plan_task_id",
        "infant_id",
        "feed_time",
        "feed_type",
        "feed_action",
        "volume_ml",
        "duration_seconds",
        "title",
    }
)
PUMPING_UPDATE_FIELDS = frozenset(
    {
        "plan_task_id",
        "pump_start_time",
        "pump_end_time",
        "milk_volume_ml",
        "pump_type",
        "duration_seconds",
        "source",
        "title",
    }
)


@dataclass(frozen=True)
class MeasuredMilkTrendDay:
    date: date
    pumped_milk_volume_ml: float
    pumping_count: int


def is_valid_list_limit(limit: int) -> bool:
    return MIN_LIST_LIMIT <= limit <= MAX_LIST_LIMIT


def is_valid_trend_days(days: int) -> bool:
    return MIN_TREND_DAYS <= days <= MAX_TREND_DAYS


def has_feeding_measurement(*, volume_ml: float | None, duration_seconds: int | None) -> bool:
    return volume_ml is not None or duration_seconds is not None


def has_pumping_measurement(*, milk_volume_ml: float | None, duration_seconds: int | None) -> bool:
    return milk_volume_ml is not None or duration_seconds is not None


def has_growth_measurement(*, height_cm: float | None, weight_kg: float | None, head_cm: float | None) -> bool:
    return height_cm is not None or weight_kg is not None or head_cm is not None


def unsupported_growth_update_fields(updates: dict[str, Any]) -> set[str]:
    return set(updates) - GROWTH_UPDATE_FIELDS


def unsupported_feeding_update_fields(updates: dict[str, Any]) -> set[str]:
    return set(updates) - FEEDING_UPDATE_FIELDS


def unsupported_pumping_update_fields(updates: dict[str, Any]) -> set[str]:
    return set(updates) - PUMPING_UPDATE_FIELDS


def growth_measurements_after_update(
    *,
    current_height_cm: float | None,
    current_weight_kg: float | None,
    current_head_cm: float | None,
    updates: dict[str, Any],
) -> tuple[float | None, float | None, float | None]:
    return (
        updates.get("height_cm", current_height_cm),
        updates.get("weight_kg", current_weight_kg),
        updates.get("head_cm", current_head_cm),
    )


def default_trend_start_date(*, now: datetime, days: int, include_today: bool) -> date:
    today = _as_utc_date(now)
    end_day = today if include_today else today - timedelta(days=1)
    return end_day - timedelta(days=days - 1)


def trend_datetime_window(*, first_day: date, days: int) -> tuple[datetime, datetime]:
    start_at = datetime.combine(first_day, time.min, tzinfo=timezone.utc)
    return start_at, start_at + timedelta(days=days)


def build_measured_milk_trend_days(
    *,
    pumpings: list[PumpingRecord],
    first_day: date,
    days: int,
) -> list[MeasuredMilkTrendDay]:
    totals_by_day: dict[date, float] = {}
    counts_by_day: dict[date, int] = {}
    last_day = first_day + timedelta(days=days)
    for pumping in pumpings:
        pump_day = _as_utc_date(pumping.pump_start_time)
        if pump_day < first_day or pump_day >= last_day:
            continue
        totals_by_day[pump_day] = totals_by_day.get(pump_day, 0.0) + float(pumping.milk_volume_ml or 0)
        counts_by_day[pump_day] = counts_by_day.get(pump_day, 0) + 1

    return [
        MeasuredMilkTrendDay(
            date=first_day + timedelta(days=offset),
            pumped_milk_volume_ml=round(totals_by_day.get(first_day + timedelta(days=offset), 0.0), 2),
            pumping_count=counts_by_day.get(first_day + timedelta(days=offset), 0),
        )
        for offset in range(days)
    ]


def _as_utc_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()
