import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.modules.audit.models import IdempotencyKey
from app.modules.audit.service import request_hash
from app.modules.plans.models import Plan, PlanTask
from app.modules.plans.repository import ScheduleTimelineTaskRow
from app.modules.plans.service import PlansService


def test_plans_service_creates_plan_with_idempotency_and_audit() -> None:
    owner_user_id = uuid4()
    repository = FakePlansRepository()
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = PlansService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    plan = asyncio.run(
        service.create_plan(
            owner_user_id=owner_user_id,
            title="Birth plan",
            request_id="req_plan",
            idempotency_key="idem-plan",
        )
    )

    assert plan.owner_user_id == owner_user_id
    assert idempotency_service.reserve_kwargs["scope"] == "plans.create"
    assert idempotency_service.completed_response_ref == str(plan.id)
    assert audit_service.record_kwargs["action"] == "plans.create"


def test_milk_plan_creation_is_retired_at_the_service_boundary() -> None:
    owner_user_id = uuid4()
    repository = FakePlansRepository()
    service = PlansService(repository=repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.create_plan(
                owner_user_id=owner_user_id,
                plan_type="milk_management",
                title="稳奶计划",
                payload={"start_date": "2026-07-27", "days": 7},
            )
        )

    assert exc_info.value.code == "unsupported_plan_type"
    assert repository.create_plan_kwargs == {}




def test_plans_service_creates_and_completes_task() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id)
    repository = FakePlansRepository(plan=plan)
    audit_service = FakeAuditService()
    service = PlansService(repository=repository, audit_service=audit_service)

    task = asyncio.run(service.create_task(owner_user_id=owner_user_id, plan_id=plan.id, title="Pack bag"))
    completed = asyncio.run(service.set_task_completed(owner_user_id=owner_user_id, task_id=task.id, completed=True))

    assert task.plan_id == plan.id
    assert completed.status == "completed"
    assert audit_service.record_kwargs["action"] == "plans.tasks.complete"






def test_plans_service_supports_typed_skipped_state() -> None:
    owner_user_id = uuid4()
    task = _task(owner_user_id=owner_user_id)
    repository = FakePlansRepository(task=task)
    service = PlansService(repository=repository, audit_service=FakeAuditService())

    skipped = asyncio.run(
        service.set_task_state(
            owner_user_id=owner_user_id,
            task_id=task.id,
            state="skipped",
            request_id="req_skip",
        )
    )

    assert skipped.status == "skipped"
    assert repository.set_task_state_kwargs["state"] == "skipped"


def test_plans_service_rejects_unknown_task_state() -> None:
    service = PlansService(repository=FakePlansRepository(task=_task(owner_user_id=uuid4())))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.set_task_state(owner_user_id=uuid4(), task_id=uuid4(), state="snoozed"))

    assert exc_info.value.code == "validation_failed"


def test_plans_service_updates_task_with_audit() -> None:
    owner_user_id = uuid4()
    task = _task(owner_user_id=owner_user_id)
    repository = FakePlansRepository(task=task)
    audit_service = FakeAuditService()
    service = PlansService(repository=repository, audit_service=audit_service)

    updated = asyncio.run(
        service.update_task(
            owner_user_id=owner_user_id,
            task_id=task.id,
            updates={"title": "Prepare appointment", "description": None, "payload": {"category": "general"}},
            request_id="req_task_update",
        )
    )

    assert updated.title == "Prepare appointment"
    assert updated.description == ""
    assert updated.payload == {"category": "general"}
    assert repository.update_task_kwargs["updates"]["description"] == ""
    assert audit_service.record_kwargs["action"] == "plans.tasks.update"


def test_plans_service_rejects_blank_task_update_title() -> None:
    owner_user_id = uuid4()
    task = _task(owner_user_id=owner_user_id)
    repository = FakePlansRepository(task=task)
    service = PlansService(repository=repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_task(
                owner_user_id=owner_user_id,
                task_id=task.id,
                updates={"title": "   "},
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert repository.update_task_kwargs == {}


def test_plans_service_task_idempotency_hash_includes_persisted_body() -> None:
    owner_user_id = uuid4()
    idempotency_service = FakeIdempotencyService(status="reserved")
    service = PlansService(repository=FakePlansRepository(), idempotency_service=idempotency_service)

    asyncio.run(
        service.create_task(
            owner_user_id=owner_user_id,
            task_date=date(2026, 7, 4),
            task_time="09:00",
            title="Pack bag",
            description="Bring charger and snacks",
            payload={"category": "general"},
            idempotency_key="idem-task",
        )
    )

    expected_hash = request_hash(
        {
            "plan_id": "",
            "task_date": "2026-07-04",
            "task_time": "09:00",
            "title": "Pack bag",
            "description": "Bring charger and snacks",
            "payload": {"category": "general"},
        }
    )
    legacy_hash = request_hash({"plan_id": "", "task_date": "2026-07-04", "task_time": "09:00", "title": "Pack bag"})
    assert idempotency_service.reserve_kwargs["request_hash"] == expected_hash
    assert idempotency_service.reserve_kwargs["request_hash"] != legacy_hash


def test_plans_service_lists_and_deletes_owner_scoped_resources() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id)
    task = _task(owner_user_id=owner_user_id)
    repository = FakePlansRepository(plan=plan, task=task, plans=[plan], tasks=[task])
    service = PlansService(repository=repository, audit_service=FakeAuditService())

    plans = asyncio.run(service.list_plans(owner_user_id=owner_user_id))
    tasks = asyncio.run(service.list_tasks(owner_user_id=owner_user_id, task_date=date(2026, 7, 2)))
    asyncio.run(service.delete_plan(owner_user_id=owner_user_id, plan_id=plan.id))
    asyncio.run(service.delete_task(owner_user_id=owner_user_id, task_id=task.id))

    assert plans == [plan]
    assert tasks == [task]
    assert plan.status == "deleted"
    assert task.status == "deleted"


def test_plan_delete_soft_deletes_all_linked_schedule_tasks() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id)
    first = _task(owner_user_id=owner_user_id, plan_id=plan.id)
    second = _task(owner_user_id=owner_user_id, plan_id=plan.id)
    second.status = "completed"
    standalone = _task(owner_user_id=owner_user_id)
    repository = FakePlansRepository(
        plan=plan,
        plans=[plan],
        tasks=[first, second, standalone],
    )
    audit_service = FakeAuditService()
    service = PlansService(repository=repository, audit_service=audit_service)

    asyncio.run(
        service.delete_plan(
            owner_user_id=owner_user_id,
            plan_id=plan.id,
            request_id="req_delete_plan",
            reason="用户不再需要这个计划",
        )
    )

    assert plan.status == "deleted"
    assert first.status == "deleted"
    assert second.status == "deleted"
    assert standalone.status == "pending"
    assert audit_service.record_kwargs["details"] == {
        "deleted_task_count": 2,
        "reason": "用户不再需要这个计划",
    }


def test_plans_service_can_filter_plans_by_type() -> None:
    owner_user_id = uuid4()
    repository = FakePlansRepository(plans=[])
    service = PlansService(repository=repository)

    asyncio.run(
        service.list_plans(
            owner_user_id=owner_user_id,
            plan_type="postpartum_recovery",
            status="active",
            as_of_date=date(2026, 7, 27),
            limit=5,
        )
    )

    assert repository.list_plans_kwargs == {
        "owner_user_id": owner_user_id,
        "plan_type": "postpartum_recovery",
        "status": "active",
        "as_of_date": date(2026, 7, 27),
        "limit": 5,
    }


def test_plans_service_updates_plan_metadata_with_optimistic_version_and_audit() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id)
    plan.version = 2
    repository = FakePlansRepository(plan=plan)
    audit_service = FakeAuditService()
    service = PlansService(repository=repository, audit_service=audit_service)

    updated = asyncio.run(
        service.update_plan_metadata(
            owner_user_id=owner_user_id,
            plan_id=plan.id,
            expected_version=2,
            updates={"title": "更新后的计划", "summary": "更新后的摘要"},
            request_id="req_plan_update",
        )
    )

    assert updated.title == "更新后的计划"
    assert updated.summary == "更新后的摘要"
    assert updated.version == 3
    assert repository.update_plan_metadata_kwargs["expected_version"] == 2
    assert audit_service.record_kwargs["action"] == "plans.update"
    assert audit_service.record_kwargs["details"] == {
        "fields": ["summary", "title"],
        "version": 3,
    }


def test_plans_service_rejects_a_stale_plan_metadata_update() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id)
    plan.version = 4
    repository = FakePlansRepository(plan=plan)
    service = PlansService(repository=repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_plan_metadata(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                expected_version=3,
                updates={"title": "过期更新"},
            )
        )

    assert exc_info.value.code == "version_conflict"
    assert plan.title == "Birth plan"


def test_plans_service_lists_cross_domain_timeline_plans_and_tasks() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id)
    task = _task(owner_user_id=owner_user_id)
    repository = FakePlansRepository(plans=[plan], tasks=[task])
    service = PlansService(repository=repository)

    plans = asyncio.run(
        service.list_schedule_timeline_plans(
            owner_user_id=owner_user_id,
            domains=("general", "postpartum_recovery"),
            status="active",
            as_of_date=date(2026, 7, 27),
            limit=5,
        )
    )
    rows = asyncio.run(
        service.list_schedule_timeline_tasks(
            owner_user_id=owner_user_id,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            domains=("general", "postpartum_recovery"),
            limit=7,
        )
    )

    assert plans == [plan]
    assert rows[0].task is task
    assert repository.list_schedule_plans_kwargs == {
        "owner_user_id": owner_user_id,
        "domains": ("postpartum_recovery", "general"),
        "status": "active",
        "as_of_date": date(2026, 7, 27),
        "limit": 5,
    }
    assert repository.list_schedule_tasks_kwargs == {
        "owner_user_id": owner_user_id,
        "start_date": date(2026, 7, 1),
        "end_date": date(2026, 7, 31),
        "domains": ("postpartum_recovery", "general"),
        "limit": 7,
    }














def _now() -> datetime:
    return datetime(2026, 7, 2, tzinfo=timezone.utc)


def _plan(*, owner_user_id: UUID) -> Plan:
    return Plan(id=uuid4(), owner_user_id=owner_user_id, title="Birth plan", plan_type="", summary="", source="manual", payload={}, status="active", version=1)




def _task(*, owner_user_id: UUID, plan_id: UUID | None = None) -> PlanTask:
    return PlanTask(id=uuid4(), owner_user_id=owner_user_id, plan_id=plan_id, title="Pack bag", task_date=date(2026, 7, 2), status="pending", payload={})




class FakePlansRepository:
    def __init__(self, *, plan=None, task=None, plans=None, tasks=None) -> None:
        self.plan = plan
        self.task = task
        self.plans = plans or []
        self.tasks = tasks or []
        self.update_task_kwargs = {}
        self.create_plan_kwargs = {}
        self.set_task_state_kwargs = {}
        self.list_plans_kwargs = {}
        self.list_schedule_plans_kwargs = {}
        self.list_schedule_tasks_kwargs = {}
        self.update_plan_payload_kwargs = {}
        self.update_plan_metadata_kwargs = {}
        self.locked_task_dates = []
        self.soft_deleted_task_ids = []
        self.locked_plan_types = []

    async def create_plan(self, **kwargs):
        self.create_plan_kwargs = kwargs
        self.plan = _plan(owner_user_id=kwargs["owner_user_id"])
        self.plan.plan_type = kwargs["plan_type"]
        self.plan.payload = kwargs["payload"]
        self.plan.starts_on = kwargs.get("starts_on")
        self.plan.ends_on = kwargs.get("ends_on")
        return self.plan

    async def get_plan_for_owner(self, *, plan_id: UUID, owner_user_id: UUID):
        return self.plan

    async def list_plans(self, **kwargs):
        self.list_plans_kwargs = kwargs
        return self.plans

    async def list_schedule_timeline_plans(self, **kwargs):
        self.list_schedule_plans_kwargs = kwargs
        return self.plans

    async def list_schedule_timeline_tasks(self, **kwargs):
        self.list_schedule_tasks_kwargs = kwargs
        return [
            ScheduleTimelineTaskRow(task=task, plan=None)
            for task in self.tasks
        ]

    async def soft_delete_plan(self, **kwargs):
        self.plan.status = "deleted"
        self.plan.deleted_at = kwargs["deleted_at"]
        return self.plan

    async def create_task(self, **kwargs):
        self.task = _task(owner_user_id=kwargs["owner_user_id"], plan_id=kwargs["plan_id"])
        return self.task

    async def get_task_for_owner(self, *, task_id: UUID, owner_user_id: UUID):
        return self.task

    async def get_task_for_owner_for_update(self, *, task_id: UUID, owner_user_id: UUID):
        return self.task

    async def list_tasks(self, **kwargs):
        return self.tasks

    async def list_tasks_for_plan(self, **kwargs):
        return [
            task
            for task in self.tasks
            if task.owner_user_id == kwargs["owner_user_id"]
            and task.plan_id == kwargs["plan_id"]
            and (not kwargs.get("task_dates") or task.task_date in kwargs["task_dates"])
            and (kwargs.get("status") is None or task.status == kwargs["status"])
        ][: kwargs.get("limit", 200)]

    async def lock_milk_schedule_dates(self, *, owner_user_id, task_dates):
        self.locked_task_dates = task_dates

    async def set_task_completed(self, **kwargs):
        self.task.status = "completed" if kwargs["completed"] else "pending"
        self.task.completed_at = kwargs["completed_at"]
        return self.task

    async def set_task_state(self, **kwargs):
        self.set_task_state_kwargs = kwargs
        if self.task is None:
            return None
        self.task.status = kwargs["state"]
        self.task.completed_at = kwargs["completed_at"]
        return self.task

    async def get_plan_for_owner_for_update(self, *, plan_id: UUID, owner_user_id: UUID):
        return self.plan

    async def lock_plan_type(self, *, owner_user_id: UUID, plan_type: str):
        self.locked_plan_types.append((owner_user_id, plan_type))

    async def get_active_plan_by_type_for_update(self, *, owner_user_id: UUID, plan_type: str):
        if (
            self.plan is not None
            and self.plan.owner_user_id == owner_user_id
            and self.plan.plan_type == plan_type
            and self.plan.status == "active"
            and self.plan.deleted_at is None
        ):
            return self.plan
        return None

    async def update_plan_payload_and_version(self, **kwargs):
        self.update_plan_payload_kwargs = kwargs
        if self.plan is None or self.plan.version != kwargs["expected_version"]:
            return None
        self.plan.payload = kwargs["payload"]
        self.plan.version += 1
        return self.plan

    async def update_plan_metadata_and_version(self, **kwargs):
        self.update_plan_metadata_kwargs = kwargs
        if self.plan is None or self.plan.version != kwargs["expected_version"]:
            return None
        for field, value in kwargs["updates"].items():
            setattr(self.plan, field, value)
        self.plan.version += 1
        return self.plan

    async def update_task(self, **kwargs):
        self.update_task_kwargs = kwargs
        if self.task is None:
            return None
        for field, value in kwargs["updates"].items():
            setattr(self.task, field, value)
        return self.task

    async def soft_delete_task(self, **kwargs):
        task = next((item for item in self.tasks if item.id == kwargs["task_id"]), self.task)
        if task is None:
            return None
        task.status = "deleted"
        task.deleted_at = kwargs["deleted_at"]
        self.soft_deleted_task_ids.append(task.id)
        return task

    async def soft_delete_tasks_for_plan(self, **kwargs):
        deleted = []
        for task in self.tasks:
            if (
                task.owner_user_id == kwargs["owner_user_id"]
                and task.plan_id == kwargs["plan_id"]
                and task.deleted_at is None
            ):
                task.status = "deleted"
                task.deleted_at = kwargs["deleted_at"]
                deleted.append(task)
        return deleted


class FakeIdempotencyService:
    def __init__(self, *, status: str, response_ref: str = "") -> None:
        self.status = status
        self.reserve_kwargs = {}
        self.completed_response_ref = ""
        self.record = IdempotencyKey(
            actor_user_id=uuid4(),
            scope="plans.create",
            key="idem-plan",
            request_hash="hash",
            response_ref=response_ref,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def reserve(self, **kwargs):
        self.reserve_kwargs = kwargs
        return FakeIdempotencyDecision(status=self.status, record=self.record)

    async def mark_completed(self, *, record, response_ref: str):
        self.completed_response_ref = response_ref
        record.response_ref = response_ref
        return record


class FakeIdempotencyDecision:
    def __init__(self, *, status: str, record) -> None:
        self.status = status
        self.record = record


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None


@pytest.mark.parametrize("plan_type", ["pregnancy", "birth_prep", "birth_journey"])
def test_retired_plan_creation_is_rejected(plan_type: str) -> None:
    service = PlansService(repository=None)  # rejection must happen before any database access
    with pytest.raises(ApiError) as error:
        asyncio.run(service.create_plan(owner_user_id=uuid4(), plan_type=plan_type, title="retired"))
    assert error.value.code == "unsupported_plan_type"
