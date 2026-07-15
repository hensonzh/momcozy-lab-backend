from datetime import date

import pytest

from production_backend.app.modules.plans.milk_plan_schedule import (
    MAX_MILK_PLAN_SCHEDULED_TASKS,
    MilkPlanScheduleValidationError,
    normalize_milk_plan_payload,
    scheduled_task_dates,
)


def test_daily_templates_expand_from_an_explicit_plan_range() -> None:
    normalized, tasks = normalize_milk_plan_payload(
        {
            "start_date": "2026-07-13",
            "days": 2,
            "direction": "maintain",
            "tasks": [
                {"title": "晨间吸奶", "time": "8:05", "task_type": "pumping", "duration_minutes": 20},
                {"title": "晚间喂养", "time": "20:30", "task_type": "feeding"},
            ],
        }
    )

    assert normalized["start_date"] == "2026-07-13"
    assert normalized["days"] == 2
    assert normalized["tasks"][0]["time"] == "08:05"
    assert len(tasks) == 4
    assert [(task.task_date.isoformat(), task.task_time, task.task_type) for task in tasks] == [
        ("2026-07-13", "08:05", "pumping"),
        ("2026-07-13", "20:30", "feeding"),
        ("2026-07-14", "08:05", "pumping"),
        ("2026-07-14", "20:30", "feeding"),
    ]
    assert scheduled_task_dates(tasks) == ["2026-07-13", "2026-07-14"]


def test_task_day_and_date_target_only_the_requested_plan_day() -> None:
    _, tasks = normalize_milk_plan_payload(
        {
            "start_date": "2026-07-13",
            "days": 3,
            "tasks": [
                {"title": "第二天吸奶", "time": "09:00", "task_type": "pumping", "day": 2},
                {"title": "第三天复盘", "time": "21:00", "task_type": "other", "date": "2026-07-15"},
            ],
        }
    )

    assert [(task.task_date.isoformat(), task.title) for task in tasks] == [
        ("2026-07-14", "第二天吸奶"),
        ("2026-07-15", "第三天复盘"),
    ]


def test_missing_start_date_is_resolved_once_before_confirmation() -> None:
    normalized, tasks = normalize_milk_plan_payload(
        {"days": 1, "tasks": [{"title": "吸奶", "time": "09:00", "task_type": "pumping"}]},
        today=date(2026, 7, 12),
    )

    assert normalized["start_date"] == "2026-07-13"
    assert tasks[0].task_date == date(2026, 7, 13)


def test_runtime_generation_metadata_is_bounded_and_preserved() -> None:
    normalized, _ = normalize_milk_plan_payload(
        {
            "direction": "maintain",
            "start_date": "2026-07-13",
            "days": 7,
            "tasks": [{"title": "稳奶吸奶", "time": "08:00", "task_type": "pumping"}],
            "goal": {
                "basis": "measured_pumping_average_7d",
                "current_daily_ml": 500.0,
                "target_daily_ml": 500.0,
            },
            "strategy_summary": "沿用近期可执行节奏",
            "checkpoints": [3, 7],
            "observation_items": ["宝宝尿布和精神状态"],
            "safety_notes": ["有红旗症状时暂停计划。"],
            "generation": {"mode": "runtime_deterministic", "timezone": "Asia/Shanghai"},
            "ignored": "not persisted",
        },
        today=date(2026, 7, 12),
    )

    assert normalized["goal"] == {
        "basis": "measured_pumping_average_7d",
        "current_daily_ml": 500.0,
        "target_daily_ml": 500.0,
    }
    assert normalized["strategy_summary"] == "沿用近期可执行节奏"
    assert normalized["checkpoints"] == [3, 7]
    assert normalized["observation_items"] == ["宝宝尿布和精神状态"]
    assert normalized["safety_notes"] == ["有红旗症状时暂停计划。"]
    assert normalized["generation"] == {"mode": "runtime_deterministic", "timezone": "Asia/Shanghai"}
    assert "ignored" not in normalized


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tasks": []},
        {"start_date": "tomorrow", "tasks": [{"title": "吸奶", "time": "09:00", "task_type": "pumping"}]},
        {"days": 1.5, "tasks": [{"title": "吸奶", "time": "09:00", "task_type": "pumping"}]},
        {"start_date": "2026-07-13", "days": 1, "tasks": [{"title": "吸奶", "task_type": "pumping"}]},
        {
            "start_date": "2026-07-13",
            "days": 1,
            "tasks": [{"title": "吸奶", "time": "25:00", "task_type": "pumping"}],
        },
        {
            "start_date": "2026-07-13",
            "days": 1,
            "tasks": [{"title": "越界", "time": "09:00", "task_type": "other", "date": "2026-07-14"}],
        },
    ],
)
def test_invalid_or_unschedulable_templates_fail_before_confirmation(payload: dict) -> None:
    with pytest.raises(MilkPlanScheduleValidationError):
        normalize_milk_plan_payload(payload, today=date(2026, 7, 12))


def test_expansion_has_a_hard_total_task_limit() -> None:
    with pytest.raises(MilkPlanScheduleValidationError) as exc_info:
        normalize_milk_plan_payload(
            {
                "start_date": "2026-07-13",
                "days": 30,
                "tasks": [{"title": f"任务 {index}", "time": f"{index:02d}:00", "task_type": "other"} for index in range(9)],
            }
        )

    assert str(MAX_MILK_PLAN_SCHEDULED_TASKS) in str(exc_info.value)
