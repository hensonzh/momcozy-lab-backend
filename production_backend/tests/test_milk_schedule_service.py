import asyncio
from datetime import date
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.modules.plans.service import PlansService


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

    async def get_plan_for_owner(self, *, plan_id, owner_user_id):
        if plan_id == self.plan.id and owner_user_id == self.plan.owner_user_id:
            return self.plan
        return None

    async def get_task_for_owner(self, *, task_id, owner_user_id):
        task = self.tasks.get(task_id)
        return task if task is not None and task.owner_user_id == owner_user_id else None

    async def update_task(self, *, task_id, owner_user_id, updates):
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        task.task_date = updates["task_date"]
        task.task_time = updates["task_time"]
        self.update_calls.append((task.id, task.task_time))
        return task
