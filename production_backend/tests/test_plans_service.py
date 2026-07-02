import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.modules.plans.service import PlansService


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


def _now() -> datetime:
    return datetime(2026, 7, 2, tzinfo=timezone.utc)


def _plan(*, owner_user_id: UUID) -> Plan:
    return Plan(id=uuid4(), owner_user_id=owner_user_id, title="Birth plan", plan_type="", summary="", source="manual", payload={}, status="active")


def _task(*, owner_user_id: UUID, plan_id: UUID | None = None) -> PlanTask:
    return PlanTask(id=uuid4(), owner_user_id=owner_user_id, plan_id=plan_id, title="Pack bag", task_date=date(2026, 7, 2), status="pending", payload={})


class FakePlansRepository:
    def __init__(self, *, plan=None, task=None, plans=None, tasks=None) -> None:
        self.plan = plan
        self.task = task
        self.plans = plans or []
        self.tasks = tasks or []

    async def create_plan(self, **kwargs):
        self.plan = _plan(owner_user_id=kwargs["owner_user_id"])
        return self.plan

    async def get_plan_for_owner(self, *, plan_id: UUID, owner_user_id: UUID):
        return self.plan

    async def list_plans(self, **kwargs):
        return self.plans

    async def soft_delete_plan(self, **kwargs):
        self.plan.status = "deleted"
        self.plan.deleted_at = kwargs["deleted_at"]
        return self.plan

    async def create_task(self, **kwargs):
        self.task = _task(owner_user_id=kwargs["owner_user_id"], plan_id=kwargs["plan_id"])
        return self.task

    async def get_task_for_owner(self, *, task_id: UUID, owner_user_id: UUID):
        return self.task

    async def list_tasks(self, **kwargs):
        return self.tasks

    async def set_task_completed(self, **kwargs):
        self.task.status = "completed" if kwargs["completed"] else "pending"
        self.task.completed_at = kwargs["completed_at"]
        return self.task

    async def soft_delete_task(self, **kwargs):
        self.task.status = "deleted"
        self.task.deleted_at = kwargs["deleted_at"]
        return self.task


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
