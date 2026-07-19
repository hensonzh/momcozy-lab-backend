import asyncio
from datetime import date
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.plans.models import Plan, PlanTask
from app.modules.plans.service import PlansService


def _fixture() -> tuple[PlansService, "MemoryPlansRepository", Plan, list[PlanTask]]:
    owner_user_id = uuid4()
    plan = Plan(
        id=uuid4(),
        owner_user_id=owner_user_id,
        plan_type="milk_management",
        title="稳奶计划",
        summary="",
        source="agent_action",
        payload={},
    )
    tasks = [
        PlanTask(
            id=uuid4(),
            owner_user_id=owner_user_id,
            plan_id=plan.id,
            task_date=date(2026, 7, 14),
            task_time=time,
            title="吸奶",
            status="pending",
            payload={"task_type": "pumping"},
        )
        for time in ("08:00", "11:00")
    ]
    repository = MemoryPlansRepository(plan=plan, tasks=tasks)
    return PlansService(repository=repository), repository, plan, tasks


def _update(task: PlanTask, *, new_time: str) -> dict:
    return {
        "task_id": str(task.id),
        "expected_plan_id": str(task.plan_id),
        "expected_task_date": task.task_date.isoformat(),
        "expected_task_time": task.task_time,
        "new_task_date": task.task_date.isoformat(),
        "new_task_time": new_time,
    }


def test_reschedule_service_persists_a_batch_only_inside_the_owner_plan() -> None:
    service, repository, plan, tasks = _fixture()

    applied = asyncio.run(
        service.reschedule_milk_tasks(
            owner_user_id=plan.owner_user_id,
            plan_id=plan.id,
            updates=[_update(tasks[0], new_time="07:30"), _update(tasks[1], new_time="10:30")],
            request_id="request-1",
        )
    )

    assert [task.task_time for task in applied] == ["07:30", "10:30"]
    assert repository.update_calls == [(tasks[0].id, "07:30"), (tasks[1].id, "10:30")]


def test_reschedule_service_rejects_stale_preview_before_any_write() -> None:
    service, repository, plan, tasks = _fixture()
    update = _update(tasks[0], new_time="07:30")
    tasks[0].task_time = "08:30"

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.reschedule_milk_tasks(
                owner_user_id=plan.owner_user_id,
                plan_id=plan.id,
                updates=[update],
            )
        )

    assert exc_info.value.code == "milk_schedule_conflict"
    assert repository.update_calls == []
    assert repository.locked_dates == [date(2026, 7, 14)]


def test_reschedule_service_rechecks_a_concurrently_occupied_target_slot_before_any_write() -> None:
    service, repository, plan, tasks = _fixture()
    conflicting_task = PlanTask(
        id=uuid4(),
        owner_user_id=plan.owner_user_id,
        plan_id=None,
        task_date=date(2026, 7, 14),
        task_time="07:30",
        title="刚新增的日程",
        status="pending",
        payload={"duration_minutes": 30},
    )
    repository.tasks[conflicting_task.id] = conflicting_task

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.reschedule_milk_tasks(
                owner_user_id=plan.owner_user_id,
                plan_id=plan.id,
                updates=[_update(tasks[0], new_time="07:30")],
            )
        )

    assert exc_info.value.code == "milk_schedule_conflict"
    assert repository.update_calls == []


def test_reschedule_service_ignores_another_owners_task_in_the_target_slot() -> None:
    service, repository, plan, tasks = _fixture()
    foreign_task = PlanTask(
        id=uuid4(),
        owner_user_id=uuid4(),
        plan_id=None,
        task_date=date(2026, 7, 14),
        task_time="07:30",
        title="其他用户的日程",
        status="pending",
        payload={"duration_minutes": 30},
    )
    repository.tasks[foreign_task.id] = foreign_task

    applied = asyncio.run(
        service.reschedule_milk_tasks(
            owner_user_id=plan.owner_user_id,
            plan_id=plan.id,
            updates=[_update(tasks[0], new_time="07:30")],
        )
    )

    assert applied[0].task_time == "07:30"
    assert repository.update_calls == [(tasks[0].id, "07:30")]


def test_create_task_locks_the_owner_and_target_date_before_insert() -> None:
    service, repository, plan, _ = _fixture()

    created = asyncio.run(
        service.create_task(
            owner_user_id=plan.owner_user_id,
            plan_id=plan.id,
            task_date=date(2026, 7, 15),
            task_time="09:00",
            title="吸奶",
        )
    )

    assert created.task_date == date(2026, 7, 15)
    assert repository.lock_calls[-1] == (plan.owner_user_id, (date(2026, 7, 15),))


def test_update_task_locks_both_old_and_new_dates_before_moving() -> None:
    service, repository, plan, tasks = _fixture()

    updated = asyncio.run(
        service.update_task(
            owner_user_id=plan.owner_user_id,
            task_id=tasks[0].id,
            updates={"task_date": date(2026, 7, 15), "task_time": "09:00"},
        )
    )

    assert updated.task_date == date(2026, 7, 15)
    assert repository.lock_calls[-1] == (
        plan.owner_user_id,
        (date(2026, 7, 14), date(2026, 7, 15)),
    )


def test_update_task_fails_closed_if_the_date_changed_before_row_lock() -> None:
    service, repository, plan, tasks = _fixture()
    repository.locked_task_date_override = date(2026, 7, 16)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_task(
                owner_user_id=plan.owner_user_id,
                task_id=tasks[0].id,
                updates={"task_date": date(2026, 7, 15), "task_time": "09:00"},
            )
        )

    assert exc_info.value.code == "milk_schedule_conflict"
    assert repository.update_calls == []


def test_reschedule_service_hides_cross_owner_task_and_does_not_write() -> None:
    service, repository, plan, tasks = _fixture()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.reschedule_milk_tasks(
                owner_user_id=uuid4(),
                plan_id=plan.id,
                updates=[_update(tasks[0], new_time="07:30")],
            )
        )

    assert exc_info.value.code == "not_found"
    assert repository.update_calls == []


class MemoryPlansRepository:
    def __init__(self, *, plan: Plan, tasks: list[PlanTask]) -> None:
        self.plan = plan
        self.tasks = {task.id: task for task in tasks}
        self.update_calls: list[tuple[object, str]] = []
        self.locked_dates: list[date] = []
        self.lock_calls: list[tuple[object, tuple[date, ...]]] = []
        self.locked_task_date_override: date | None = None

    async def get_plan_for_owner(self, *, plan_id, owner_user_id):
        if plan_id == self.plan.id and owner_user_id == self.plan.owner_user_id:
            return self.plan
        return None

    async def get_task_for_owner(self, *, task_id, owner_user_id):
        task = self.tasks.get(task_id)
        return task if task is not None and task.owner_user_id == owner_user_id else None

    async def get_task_for_owner_for_update(self, *, task_id, owner_user_id):
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is not None and self.locked_task_date_override is not None:
            task.task_date = self.locked_task_date_override
        return task

    async def list_tasks_for_milk_reschedule_for_update(self, *, owner_user_id, task_ids, task_dates):
        dates = set(task_dates)
        ids = set(task_ids)
        return sorted(
            (
                task
                for task in self.tasks.values()
                if task.owner_user_id == owner_user_id
                and task.deleted_at is None
                and (task.id in ids or task.task_date in dates)
            ),
            key=lambda task: str(task.id),
        )

    async def lock_milk_schedule_dates(self, *, owner_user_id, task_dates):
        assert owner_user_id == self.plan.owner_user_id
        self.locked_dates = sorted(set(task_dates))
        self.lock_calls.append((owner_user_id, tuple(self.locked_dates)))

    async def create_task(self, **kwargs):
        task = PlanTask(id=uuid4(), status="pending", **kwargs)
        self.tasks[task.id] = task
        return task

    async def update_task(self, *, task_id, owner_user_id, updates):
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        task.task_date = updates["task_date"]
        task.task_time = updates["task_time"]
        self.update_calls.append((task.id, task.task_time))
        return task
