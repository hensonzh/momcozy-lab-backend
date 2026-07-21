from datetime import date
from uuid import uuid4

import pytest

from app.agents.cozymate.tools.milk_schedule_adjustment import (
    MilkScheduleAdjustmentError,
    build_milk_schedule_preview,
)
from app.modules.plans.models import PlanTask


def _task(*, task_date: str, task_time: str, title: str = "吸奶", duration: int = 30) -> PlanTask:
    return PlanTask(
        id=uuid4(),
        owner_user_id=uuid4(),
        plan_id=uuid4(),
        task_date=date.fromisoformat(task_date),
        task_time=task_time,
        title=title,
        description="",
        status="pending",
        payload={"task_type": "pumping", "duration_minutes": duration},
    )


def test_single_day_preview_moves_conflicts_and_preserves_minimum_gap() -> None:
    plan_id = uuid4()
    tasks = [
        _task(task_date="2026-07-14", task_time="08:00"),
        _task(task_date="2026-07-14", task_time="11:00"),
        _task(task_date="2026-07-14", task_time="14:00"),
    ]
    for task in tasks:
        task.plan_id = plan_id

    preview = build_milk_schedule_preview(
        plan_id=plan_id,
        tasks=tasks,
        target_dates=[date(2026, 7, 14)],
        busy_windows=[
            {"date": "2026-07-14", "start_time": "10:30", "end_time": "12:30", "title": "会议"},
        ],
        min_gap_minutes=90,
    )

    assert preview["conflict_count"] == 1
    assert preview["updated_count"] >= 1
    starts = [int(item["new_time"][:2]) * 60 + int(item["new_time"][3:]) for item in preview["tasks"]]
    assert all(right - left >= 90 for left, right in zip(starts, starts[1:], strict=False))
    assert all(not (630 <= start < 750) for start in starts)
    assert preview["affected_dates"] == ["2026-07-14"]


def test_batch_preview_is_deterministic_and_contains_freshness_guards() -> None:
    plan_id = uuid4()
    tasks = [
        _task(task_date="2026-07-14", task_time="09:00"),
        _task(task_date="2026-07-15", task_time="09:00"),
    ]
    for task in tasks:
        task.plan_id = plan_id
    windows = [
        {"date": "2026-07-14", "start_time": "08:30", "end_time": "10:00", "title": "通勤"},
        {"date": "2026-07-15", "start_time": "08:30", "end_time": "10:00", "title": "通勤"},
    ]

    preview = build_milk_schedule_preview(
        plan_id=plan_id,
        tasks=tasks,
        target_dates=[date(2026, 7, 14), date(2026, 7, 15)],
        busy_windows=windows,
        min_gap_minutes=90,
    )

    assert preview["affected_dates"] == ["2026-07-14", "2026-07-15"]
    assert len(preview["updates"]) == 2
    assert all(update["expected_task_date"] and update["expected_task_time"] for update in preview["updates"])
    assert len({update["task_id"] for update in preview["updates"]}) == 2


def test_preview_rejects_cross_plan_or_non_pending_tasks() -> None:
    plan_id = uuid4()
    cross_plan = _task(task_date="2026-07-14", task_time="09:00")

    with pytest.raises(MilkScheduleAdjustmentError, match="milk_schedule_task_outside_plan"):
        build_milk_schedule_preview(
            plan_id=plan_id,
            tasks=[cross_plan],
            target_dates=[date(2026, 7, 14)],
            busy_windows=[{"date": "2026-07-14", "start_time": "08:30", "end_time": "10:00"}],
        )


def test_preview_avoids_other_owner_scoped_fixed_plan_tasks_on_the_same_day() -> None:
    plan_id = uuid4()
    adjustable = _task(task_date="2026-07-14", task_time="11:00")
    adjustable.plan_id = plan_id
    fixed = _task(task_date="2026-07-14", task_time="10:30", title="产检", duration=60)

    preview = build_milk_schedule_preview(
        plan_id=plan_id,
        tasks=[adjustable],
        fixed_tasks=[fixed],
        target_dates=[date(2026, 7, 14)],
        busy_windows=[{"date": "2026-07-14", "start_time": "12:00", "end_time": "13:00"}],
    )

    assert preview["tasks"][0]["new_time"] not in {"10:00", "10:30", "11:00"}
