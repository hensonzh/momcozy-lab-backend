import asyncio
from datetime import date
from uuid import UUID, uuid4

from app.modules.plans.models import Plan, PlanTask
from app.modules.plans.service import PlansService


def test_birth_plan_task_main_flow_tracks_completion_and_delete_state() -> None:
    owner_user_id = uuid4()
    repository = InMemoryPlansRepository()
    audit_service = FlowAuditService()
    service = PlansService(repository=repository, audit_service=audit_service)

    plan = asyncio.run(
        service.create_plan(
            owner_user_id=owner_user_id,
            plan_type="birth_prep",
            title="Hospital bag plan",
            summary="Prepare essentials before delivery.",
            source="manual",
            request_id="req_plan_create",
        )
    )
    task = asyncio.run(
        service.create_task(
            owner_user_id=owner_user_id,
            plan_id=plan.id,
            task_date=date(2026, 7, 5),
            task_time="09:00",
            title="Pack nursing bra",
            description="Add nursing bra and charger to hospital bag.",
            payload={"category": "hospital_bag"},
            request_id="req_task_create",
        )
    )

    active_plans = asyncio.run(service.list_plans(owner_user_id=owner_user_id, status="active"))
    pending_tasks = asyncio.run(service.list_tasks(owner_user_id=owner_user_id, task_date=date(2026, 7, 5), status="pending"))

    assert [item.id for item in active_plans] == [plan.id]
    assert [item.id for item in pending_tasks] == [task.id]

    completed = asyncio.run(
        service.set_task_completed(
            owner_user_id=owner_user_id,
            task_id=task.id,
            completed=True,
            request_id="req_task_complete",
        )
    )
    pending_after_complete = asyncio.run(service.list_tasks(owner_user_id=owner_user_id, task_date=date(2026, 7, 5), status="pending"))
    completed_tasks = asyncio.run(service.list_tasks(owner_user_id=owner_user_id, task_date=date(2026, 7, 5), status="completed"))

    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert pending_after_complete == []
    assert [item.id for item in completed_tasks] == [task.id]

    asyncio.run(service.delete_task(owner_user_id=owner_user_id, task_id=task.id, request_id="req_task_delete"))
    asyncio.run(service.delete_plan(owner_user_id=owner_user_id, plan_id=plan.id, request_id="req_plan_delete"))

    assert asyncio.run(service.list_tasks(owner_user_id=owner_user_id, task_date=date(2026, 7, 5), status="completed")) == []
    assert asyncio.run(service.list_plans(owner_user_id=owner_user_id, status="active")) == []
    assert [entry["action"] for entry in audit_service.entries] == [
        "plans.create",
        "plans.tasks.create",
        "plans.tasks.complete",
        "plans.tasks.delete",
        "plans.delete",
    ]


class InMemoryPlansRepository:
    def __init__(self) -> None:
        self.plans: list[Plan] = []
        self.tasks: list[PlanTask] = []

    async def create_plan(self, **kwargs):
        plan = Plan(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_type=kwargs["plan_type"],
            title=kwargs["title"],
            summary=kwargs["summary"],
            source=kwargs["source"],
            payload=kwargs["payload"],
            status="active",
        )
        self.plans.append(plan)
        return plan

    async def get_plan_for_owner(self, *, plan_id: UUID, owner_user_id: UUID):
        return next(
            (
                plan
                for plan in self.plans
                if plan.id == plan_id and plan.owner_user_id == owner_user_id and plan.deleted_at is None
            ),
            None,
        )

    async def list_plans(
        self,
        *,
        owner_user_id: UUID,
        plan_type: str,
        status: str,
        as_of_date: date,
        limit: int,
    ):
        plans = [
            plan
            for plan in self.plans
            if plan.owner_user_id == owner_user_id
            and plan.status == status
            and (not plan_type or plan.plan_type == plan_type)
            and plan.deleted_at is None
        ]
        return plans[:limit]

    async def soft_delete_tasks_for_plan(self, *, plan_id: UUID, owner_user_id: UUID, deleted_at):
        deleted = []
        for task in self.tasks:
            if (
                task.plan_id == plan_id
                and task.owner_user_id == owner_user_id
                and task.deleted_at is None
            ):
                task.status = "deleted"
                task.deleted_at = deleted_at
                deleted.append(task)
        return deleted

    async def get_plan_for_owner_for_update(self, *, plan_id: UUID, owner_user_id: UUID):
        return await self.get_plan_for_owner(
            plan_id=plan_id,
            owner_user_id=owner_user_id,
        )

    async def soft_delete_plan(self, *, plan_id: UUID, owner_user_id: UUID, deleted_at):
        plan = await self.get_plan_for_owner(plan_id=plan_id, owner_user_id=owner_user_id)
        if plan is None:
            return None
        plan.status = "deleted"
        plan.deleted_at = deleted_at
        return plan

    async def create_task(self, **kwargs):
        task = PlanTask(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            plan_id=kwargs["plan_id"],
            task_date=kwargs["task_date"],
            task_time=kwargs["task_time"],
            title=kwargs["title"],
            description=kwargs["description"],
            payload=kwargs["payload"],
            status="pending",
        )
        self.tasks.append(task)
        return task

    async def get_task_for_owner(self, *, task_id: UUID, owner_user_id: UUID):
        return next(
            (
                task
                for task in self.tasks
                if task.id == task_id and task.owner_user_id == owner_user_id and task.deleted_at is None
            ),
            None,
        )

    async def list_tasks(self, *, owner_user_id: UUID, task_date: date | None, status: str | None, limit: int):
        tasks = [
            task
            for task in self.tasks
            if task.owner_user_id == owner_user_id
            and task.deleted_at is None
            and (task_date is None or task.task_date == task_date)
            and (status is None or task.status == status)
        ]
        return tasks[:limit]

    async def set_task_completed(self, *, task_id: UUID, owner_user_id: UUID, completed: bool, completed_at):
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        task.status = "completed" if completed else "pending"
        task.completed_at = completed_at
        return task

    async def soft_delete_task(self, *, task_id: UUID, owner_user_id: UUID, deleted_at):
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        task.status = "deleted"
        task.deleted_at = deleted_at
        return task


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
