from datetime import date

import pytest

from production_backend.app.modules.plans.milk_plan_builder import (
    MilkPlanDraftError,
    build_milk_plan_draft,
)


def _analysis_context() -> dict:
    return {
        "records_snapshot": {
            "window": {"days": 7},
            "volumes": {"average_daily_pumped_volume_ml": 500.0},
            "recent_pumpings": [
                {"pump_start_time": "2026-07-14T00:00:00+00:00"},
                {"pump_start_time": "2026-07-14T03:00:00+00:00"},
                {"pump_start_time": "2026-07-14T06:00:00+00:00"},
                {"pump_start_time": "2026-07-14T09:00:00+00:00"},
                {"pump_start_time": "2026-07-14T12:00:00+00:00"},
                {"pump_start_time": "2026-07-14T15:00:00+00:00"},
            ],
        }
    }


def test_maintain_draft_reuses_recent_rhythm_in_the_users_timezone() -> None:
    draft = build_milk_plan_draft(
        analysis_context=_analysis_context(),
        direction="maintain",
        timezone_name="Asia/Shanghai",
        today=date(2026, 7, 15),
    )

    assert draft["title"] == "7 天稳奶计划"
    assert draft["payload"]["start_date"] == "2026-07-16"
    assert draft["payload"]["days"] == 7
    assert [task["time"] for task in draft["payload"]["tasks"] if task["task_type"] == "pumping"] == [
        "08:00",
        "11:00",
        "14:00",
        "17:00",
        "20:00",
        "23:00",
    ]
    assert draft["payload"]["goal"] == {
        "basis": "measured_pumping_average_7d",
        "current_daily_ml": 500.0,
        "target_daily_ml": 500.0,
    }
    assert draft["payload"]["generation"] == {"mode": "runtime_deterministic", "timezone": "Asia/Shanghai"}


def test_increase_and_decrease_change_only_one_daily_pumping_slot() -> None:
    increase = build_milk_plan_draft(
        analysis_context=_analysis_context(),
        direction="increase",
        timezone_name="Asia/Shanghai",
        today=date(2026, 7, 15),
    )
    decrease = build_milk_plan_draft(
        analysis_context=_analysis_context(),
        direction="decrease",
        timezone_name="Asia/Shanghai",
        today=date(2026, 7, 15),
    )

    increase_times = [task["time"] for task in increase["payload"]["tasks"] if task["task_type"] == "pumping"]
    decrease_times = [task["time"] for task in decrease["payload"]["tasks"] if task["task_type"] == "pumping"]
    assert len(increase_times) == 7
    assert len(decrease_times) == 5
    assert increase["payload"]["goal"]["target_daily_ml"] == 550.0
    assert decrease["payload"]["goal"]["target_daily_ml"] == 450.0
    assert increase["payload"]["safety_notes"]
    assert decrease["payload"]["safety_notes"]


def test_explicit_preferred_times_are_normalized_and_bounded() -> None:
    draft = build_milk_plan_draft(
        analysis_context=_analysis_context(),
        direction="maintain",
        preferred_pumping_times=["7:00", "13:30", "22:00", "13:30"],
        timezone_name="Asia/Shanghai",
        today=date(2026, 7, 15),
    )

    assert [task["time"] for task in draft["payload"]["tasks"] if task["task_type"] == "pumping"] == [
        "07:00",
        "13:30",
        "22:00",
    ]


def test_recent_rhythm_uses_one_representative_day_instead_of_all_unique_weekly_times() -> None:
    context = _analysis_context()
    context["records_snapshot"]["recent_pumpings"] = [
        *[
            {"pump_start_time": f"2026-07-13T{hour:02d}:{minute:02d}:00+00:00"}
            for hour, minute in ((0, 1), (3, 2), (6, 3), (9, 4), (12, 5), (15, 6))
        ],
        {"pump_start_time": "2026-07-14T00:15:00+00:00"},
        {"pump_start_time": "2026-07-14T03:15:00+00:00"},
    ]

    draft = build_milk_plan_draft(
        analysis_context=context,
        direction="maintain",
        timezone_name="UTC",
        today=date(2026, 7, 15),
    )

    assert [task["time"] for task in draft["payload"]["tasks"]] == [
        "00:01",
        "03:02",
        "06:03",
        "09:04",
        "12:05",
        "15:06",
    ]


@pytest.mark.parametrize("direction", ["", "observe", "unknown"])
def test_draft_rejects_non_actionable_direction(direction: str) -> None:
    with pytest.raises(MilkPlanDraftError, match="unsupported_milk_plan_direction"):
        build_milk_plan_draft(
            analysis_context=_analysis_context(),
            direction=direction,
            timezone_name="Asia/Shanghai",
            today=date(2026, 7, 15),
        )
