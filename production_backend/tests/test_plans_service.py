import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.audit.service import request_hash
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
            updates={"title": "Pack hospital bag", "description": None, "payload": {"category": "birth_prep"}},
            request_id="req_task_update",
        )
    )

    assert updated.title == "Pack hospital bag"
    assert updated.description == ""
    assert updated.payload == {"category": "birth_prep"}
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
            payload={"category": "birth_prep"},
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
            "payload": {"category": "birth_prep"},
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


def test_plans_service_can_filter_plans_by_type() -> None:
    owner_user_id = uuid4()
    repository = FakePlansRepository(plans=[])
    service = PlansService(repository=repository)

    asyncio.run(service.list_plans(owner_user_id=owner_user_id, plan_type="pregnancy", status="active", limit=5))

    assert repository.list_plans_kwargs == {
        "owner_user_id": owner_user_id,
        "plan_type": "pregnancy",
        "status": "active",
        "limit": 5,
    }


def test_pregnancy_plan_create_normalizes_stable_todo_item_ids() -> None:
    owner_user_id = uuid4()
    repository = FakePlansRepository()
    service = PlansService(repository=repository)
    source_payload = {
        "card": {
            "card_json": {
                "todo_plan": {
                    "periods": [
                        {
                            "id": "current",
                            "items": [
                                {"id": "existing-id", "title": "产检问题"},
                                {"title": "准备待产包"},
                            ],
                        }
                    ]
                }
            }
        }
    }

    first = asyncio.run(
        service.create_plan(
            owner_user_id=owner_user_id,
            plan_type="pregnancy",
            title="孕期计划",
            payload=source_payload,
        )
    )
    first_items = first.payload["card"]["card_json"]["todo_plan"]["periods"][0]["items"]
    first_ids = [item["item_id"] for item in first_items]

    repository.plan = None
    second = asyncio.run(
        service.create_plan(
            owner_user_id=owner_user_id,
            plan_type="pregnancy",
            title="孕期计划",
            payload=source_payload,
        )
    )
    second_items = second.payload["card"]["card_json"]["todo_plan"]["periods"][0]["items"]

    assert first_ids[0] == "existing-id"
    assert first_ids[1].startswith("todo-")
    assert [item["item_id"] for item in second_items] == first_ids
    assert "item_id" not in source_payload["card"]["card_json"]["todo_plan"]["periods"][0]["items"][0]


def test_todo_completion_updates_authoritative_payload_version_and_audit() -> None:
    owner_user_id = uuid4()
    plan = _pregnancy_plan(owner_user_id=owner_user_id)
    repository = FakePlansRepository(plan=plan)
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = PlansService(
        repository=repository,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
    )

    updated = asyncio.run(
        service.update_plan_todo_completion(
            owner_user_id=owner_user_id,
            plan_id=plan.id,
            item_id="prepare-hospital-bag",
            completed=True,
            expected_version=1,
            request_id="req_todo",
            idempotency_key="idem-todo",
        )
    )

    item = updated.payload["card"]["card_json"]["todo_plan"]["periods"][0]["items"][0]
    assert item == {
        "item_id": "prepare-hospital-bag",
        "title": "准备待产包",
        "completed": True,
        "status": "completed",
    }
    assert updated.version == 2
    assert idempotency_service.reserve_kwargs["scope"] == "plans.todos.completion"
    assert audit_service.record_kwargs["action"] == "plans.todos.completion"


def test_todo_completion_rejects_stale_version_and_legacy_item_without_item_id() -> None:
    owner_user_id = uuid4()
    plan = _pregnancy_plan(owner_user_id=owner_user_id)
    plan.version = 2
    repository = FakePlansRepository(plan=plan)
    service = PlansService(repository=repository)

    with pytest.raises(ApiError) as stale:
        asyncio.run(
            service.update_plan_todo_completion(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                item_id="prepare-hospital-bag",
                completed=True,
                expected_version=1,
            )
        )
    assert stale.value.code == "version_conflict"

    plan.version = 2
    plan.payload["card"]["card_json"]["todo_plan"]["periods"][0]["items"] = [
        {"title": "准备待产包"}
    ]
    with pytest.raises(ApiError) as missing:
        asyncio.run(
            service.update_plan_todo_completion(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                item_id="prepare-hospital-bag",
                completed=True,
                expected_version=2,
            )
        )
    assert missing.value.code == "todo_item_not_found"


def test_todo_completion_hides_cross_owner_plan_as_not_found() -> None:
    service = PlansService(repository=FakePlansRepository(plan=None))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_plan_todo_completion(
                owner_user_id=uuid4(),
                plan_id=uuid4(),
                item_id="prepare-hospital-bag",
                completed=True,
                expected_version=1,
            )
        )

    assert exc_info.value.code == "not_found"


def test_todo_completion_idempotency_replay_returns_authoritative_plan_without_second_update() -> None:
    owner_user_id = uuid4()
    plan = _pregnancy_plan(owner_user_id=owner_user_id)
    plan.version = 2
    repository = FakePlansRepository(plan=plan)
    service = PlansService(
        repository=repository,
        idempotency_service=FakeIdempotencyService(status="replay", response_ref=str(plan.id)),
    )

    replayed = asyncio.run(
        service.update_plan_todo_completion(
            owner_user_id=owner_user_id,
            plan_id=plan.id,
            item_id="prepare-hospital-bag",
            completed=True,
            expected_version=1,
            idempotency_key="idem-todo",
        )
    )

    assert replayed is plan
    assert repository.update_plan_payload_kwargs == {}


def _now() -> datetime:
    return datetime(2026, 7, 2, tzinfo=timezone.utc)


def _plan(*, owner_user_id: UUID) -> Plan:
    return Plan(id=uuid4(), owner_user_id=owner_user_id, title="Birth plan", plan_type="", summary="", source="manual", payload={}, status="active", version=1)


def _pregnancy_plan(*, owner_user_id: UUID) -> Plan:
    return Plan(
        id=uuid4(),
        owner_user_id=owner_user_id,
        title="孕期计划",
        plan_type="pregnancy",
        summary="",
        source="agent_action",
        status="active",
        version=1,
        payload={
            "card": {
                "card_json": {
                    "todo_plan": {
                        "periods": [
                            {
                                "items": [
                                    {"item_id": "prepare-hospital-bag", "title": "准备待产包", "completed": False}
                                ]
                            }
                        ]
                    }
                }
            }
        },
    )


def _task(*, owner_user_id: UUID, plan_id: UUID | None = None) -> PlanTask:
    return PlanTask(id=uuid4(), owner_user_id=owner_user_id, plan_id=plan_id, title="Pack bag", task_date=date(2026, 7, 2), status="pending", payload={})


class FakePlansRepository:
    def __init__(self, *, plan=None, task=None, plans=None, tasks=None) -> None:
        self.plan = plan
        self.task = task
        self.plans = plans or []
        self.tasks = tasks or []
        self.update_task_kwargs = {}
        self.set_task_state_kwargs = {}
        self.list_plans_kwargs = {}
        self.update_plan_payload_kwargs = {}

    async def create_plan(self, **kwargs):
        self.plan = _plan(owner_user_id=kwargs["owner_user_id"])
        self.plan.plan_type = kwargs["plan_type"]
        self.plan.payload = kwargs["payload"]
        return self.plan

    async def get_plan_for_owner(self, *, plan_id: UUID, owner_user_id: UUID):
        return self.plan

    async def list_plans(self, **kwargs):
        self.list_plans_kwargs = kwargs
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

    async def set_task_state(self, **kwargs):
        self.set_task_state_kwargs = kwargs
        if self.task is None:
            return None
        self.task.status = kwargs["state"]
        self.task.completed_at = kwargs["completed_at"]
        return self.task

    async def get_plan_for_owner_for_update(self, *, plan_id: UUID, owner_user_id: UUID):
        return self.plan

    async def update_plan_payload_and_version(self, **kwargs):
        self.update_plan_payload_kwargs = kwargs
        if self.plan is None or self.plan.version != kwargs["expected_version"]:
            return None
        self.plan.payload = kwargs["payload"]
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
