from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.plans.agent_contracts import AgentPlansActionRequest
from app.modules.plans.agent_router import (
    get_agent_plans_action_service,
    get_agent_plans_read_service,
)
from app.modules.plans.agent_service import AgentPlansActionService


SERVICE_KEY = "agent-runtime-service-key-with-32-bytes"


def test_agent_plans_current_read_uses_service_identity_and_explicit_actor_scope() -> None:
    actor_user_id = uuid4()
    service = RecordingPlansReadService()
    app = _app()
    app.dependency_overrides[get_agent_plans_read_service] = lambda: service

    response = TestClient(app).get(
        "/v1/internal/agent/plans/current",
        headers={"X-Service-Key": SERVICE_KEY},
        params={"actor_user_id": str(actor_user_id), "limit": 4},
    )

    assert response.status_code == 200
    assert response.json()["counts"] == {"plans": 1, "tasks": 1}
    assert service.list_plans_kwargs == {
        "owner_user_id": actor_user_id,
        "status": "active",
        "limit": 4,
    }
    assert service.list_tasks_kwargs == {
        "owner_user_id": actor_user_id,
        "limit": 4,
    }


def test_agent_plans_calendar_read_forwards_owner_scoped_filters() -> None:
    actor_user_id = uuid4()
    service = RecordingPlansReadService()
    app = _app()
    app.dependency_overrides[get_agent_plans_read_service] = lambda: service

    response = TestClient(app).get(
        "/v1/internal/agent/plans/calendar",
        headers={"X-Service-Key": SERVICE_KEY},
        params={
            "actor_user_id": str(actor_user_id),
            "task_date": "2026-07-28",
            "status": "pending",
            "limit": 12,
        },
    )

    assert response.status_code == 200
    assert response.json()["filters"] == {
        "task_date": "2026-07-28",
        "status": "pending",
        "limit": 12,
    }
    assert service.list_tasks_kwargs == {
        "owner_user_id": actor_user_id,
        "task_date": date(2026, 7, 28),
        "status": "pending",
        "limit": 12,
    }


def test_agent_plans_routes_reject_missing_service_identity() -> None:
    response = TestClient(_app()).get(
        "/v1/internal/agent/plans/current",
        params={"actor_user_id": str(uuid4())},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_plans_apply_requires_action_bound_idempotency_key() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    app = _app()
    app.dependency_overrides[get_agent_plans_action_service] = (
        lambda: RecordingAgentPlansActionService()
    )
    command = _task_create_command(
        actor_user_id=actor_user_id,
        action_id=action_id,
    )

    missing = TestClient(app).post(
        "/v1/internal/agent/actions/plans/apply",
        headers={"X-Service-Key": SERVICE_KEY},
        json=command,
    )
    wrong = TestClient(app).post(
        "/v1/internal/agent/actions/plans/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{uuid4()}",
        },
        json=command,
    )

    assert missing.status_code == 422
    assert wrong.status_code == 422


def test_agent_plans_apply_forwards_runtime_action_identity() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    service = RecordingAgentPlansActionService()
    app = _app()
    app.dependency_overrides[get_agent_plans_action_service] = lambda: service

    response = TestClient(app).post(
        "/v1/internal/agent/actions/plans/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{action_id}",
            "X-Request-ID": "req-plans",
        },
        json=_task_create_command(
            actor_user_id=actor_user_id,
            action_id=action_id,
            run_id=run_id,
        ),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert response.json()["action_id"] == str(action_id)
    assert service.kwargs["idempotency_key"] == f"agent-action:{action_id}"
    assert service.kwargs["actor_service"] == "agent-runtime"
    assert service.kwargs["request_id"] == "req-plans"
    command = service.kwargs["command"]
    assert isinstance(command, AgentPlansActionRequest)
    assert command.actor_user_id == actor_user_id
    assert command.run_id == run_id
    assert command.action_type == "plans.task.create"


@pytest.mark.parametrize(
    ("command_factory", "expected_call", "expected_resource_type"),
    [
        ("task_create", "create_task", "plan_task"),
        ("task_complete", "set_task_completed", "plan_task"),
        ("task_update", "update_task", "plan_task"),
        ("task_delete", "delete_task", "plan_task"),
        ("plan_delete", "delete_plan", "plan"),
        ("pregnancy_create", "create_plan", "plan"),
        ("milk_create", "create_plan", "plan"),
        ("milk_reschedule", "reschedule_milk_tasks", "plan"),
    ],
)
def test_agent_plans_action_service_supports_existing_action_operations(
    command_factory: str,
    expected_call: str,
    expected_resource_type: str,
) -> None:
    plans_service = RecordingPlansDomainService()
    idempotency_service = InMemoryIdempotencyService()
    audit_service = RecordingAuditService()
    service = AgentPlansActionService(
        plans_service=plans_service,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
    )
    command = _action_command(command_factory)

    result = asyncio.run(
        service.apply_idempotent(
            command=AgentPlansActionRequest.model_validate(command),
            idempotency_key=f"agent-action:{command['action_id']}",
            actor_service="agent-runtime",
            request_id="req-plans",
        )
    )

    assert result.resource_type == expected_resource_type
    assert expected_call in plans_service.calls
    assert len(audit_service.calls) == 1
    assert audit_service.calls[0]["actor_type"] == "service"
    assert audit_service.calls[0]["actor_service"] == "agent-runtime"
    assert audit_service.calls[0]["details"]["action_id"] == command["action_id"]
    assert audit_service.calls[0]["details"]["run_id"] == command["run_id"]


def test_agent_plans_action_replay_does_not_repeat_business_write_or_audit() -> None:
    plans_service = RecordingPlansDomainService()
    idempotency_service = InMemoryIdempotencyService()
    audit_service = RecordingAuditService()
    service = AgentPlansActionService(
        plans_service=plans_service,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
    )
    raw_command = _action_command("milk_create")
    command = AgentPlansActionRequest.model_validate(raw_command)
    kwargs = {
        "command": command,
        "idempotency_key": f"agent-action:{command.action_id}",
        "actor_service": "agent-runtime",
        "request_id": "req-plans",
    }

    first = asyncio.run(service.apply_idempotent(**kwargs))
    replay = asyncio.run(service.apply_idempotent(**kwargs))

    assert first == replay
    assert plans_service.calls.count("create_plan") == 1
    assert plans_service.calls.count("create_task") == 2
    assert len(audit_service.calls) == 1


def test_agent_plans_action_factory_audits_only_at_service_boundary() -> None:
    service = get_agent_plans_action_service(session=SimpleNamespace())

    assert service.plans_service.audit_service is None
    assert service.plans_service.idempotency_service is None
    assert service.audit_service is not None
    assert service.idempotency_service is not None


def test_agent_milk_plan_result_emits_bounded_event_without_private_content() -> None:
    plans_service = RecordingPlansDomainService()
    service = AgentPlansActionService(
        plans_service=plans_service,
        idempotency_service=InMemoryIdempotencyService(),
    )
    raw_command = _action_command("milk_create")
    raw_command["payload"]["title"] = "private plan title"
    raw_command["payload"]["summary"] = "private supply and health context"
    command = AgentPlansActionRequest.model_validate(raw_command)

    result = asyncio.run(
        service.apply_idempotent(
            command=command,
            idempotency_key=f"agent-action:{command.action_id}",
            actor_service="agent-runtime",
            request_id="",
        )
    )

    event = result.application_events[0]
    assert event["type"] == "milk_plan.changed"
    assert event["payload"]["affected_dates"] == [
        "2026-07-28",
        "2026-07-29",
    ]
    assert "private" not in str(event)


def test_agent_milk_task_update_event_keeps_authoritative_plan_identity() -> None:
    plans_service = RecordingPlansDomainService()
    service = AgentPlansActionService(
        plans_service=plans_service,
        idempotency_service=InMemoryIdempotencyService(),
    )
    raw_command = _action_command("task_update")
    command = AgentPlansActionRequest.model_validate(raw_command)

    result = asyncio.run(
        service.apply_idempotent(
            command=command,
            idempotency_key=f"agent-action:{command.action_id}",
            actor_service="agent-runtime",
            request_id="",
        )
    )

    event = result.application_events[0]
    assert event["type"] == "milk_plan.changed"
    assert event["payload"]["plan_id"] == str(plans_service.task.plan_id)
    assert event["payload"]["task_ids"] == [str(plans_service.task.id)]


def test_agent_plans_action_rejects_same_action_bound_to_another_run() -> None:
    service = AgentPlansActionService(
        plans_service=RecordingPlansDomainService(),
        idempotency_service=InMemoryIdempotencyService(),
    )
    raw_command = _action_command("plan_delete")
    first = AgentPlansActionRequest.model_validate(raw_command)
    changed = AgentPlansActionRequest.model_validate(
        {**raw_command, "run_id": str(uuid4())}
    )
    key = f"agent-action:{first.action_id}"

    asyncio.run(
        service.apply_idempotent(
            command=first,
            idempotency_key=key,
            actor_service="agent-runtime",
            request_id="",
        )
    )
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.apply_idempotent(
                command=changed,
                idempotency_key=key,
                actor_service="agent-runtime",
                request_id="",
            )
        )

    assert exc_info.value.code == "idempotency_conflict"


def test_agent_milk_plan_apply_rejects_expired_analysis_before_business_write() -> None:
    plans_service = RecordingPlansDomainService()
    service = AgentPlansActionService(
        plans_service=plans_service,
        idempotency_service=InMemoryIdempotencyService(),
    )
    raw_command = _action_command("milk_create")
    raw_command["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    command = AgentPlansActionRequest.model_validate(raw_command)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.apply_idempotent(
                command=command,
                idempotency_key=f"agent-action:{command.action_id}",
                actor_service="agent-runtime",
                request_id="",
            )
        )

    assert exc_info.value.code == "milk_analysis_expired_before_plan"
    assert plans_service.calls == []


def test_agent_milk_plan_apply_maps_schedule_validation_to_contract_error() -> None:
    plans_service = RecordingPlansDomainService()
    service = AgentPlansActionService(
        plans_service=plans_service,
        idempotency_service=InMemoryIdempotencyService(),
    )
    raw_command = _action_command("milk_create")
    raw_command["payload"]["payload"]["tasks"][0].update(
        {
            "date": "2026-07-30",
            "day": 1,
        }
    )
    command = AgentPlansActionRequest.model_validate(raw_command)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.apply_idempotent(
                command=command,
                idempotency_key=f"agent-action:{command.action_id}",
                actor_service="agent-runtime",
                request_id="",
            )
        )

    assert exc_info.value.code == "invalid_milk_plan_schedule"
    assert plans_service.calls == []


class RecordingPlansReadService:
    def __init__(self) -> None:
        self.list_plans_kwargs: dict[str, object] = {}
        self.list_tasks_kwargs: dict[str, object] = {}
        self.plan = _plan()
        self.task = _task(plan_id=self.plan.id)

    async def list_plans(self, **kwargs: object) -> list[object]:
        self.list_plans_kwargs = kwargs
        return [self.plan]

    async def list_tasks(self, **kwargs: object) -> list[object]:
        self.list_tasks_kwargs = kwargs
        return [self.task]


class RecordingAgentPlansActionService:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def apply_idempotent(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        return SimpleNamespace(
            resource_type="plan_task",
            resource_id=str(uuid4()),
            details={"status": "pending"},
            application_events=(),
        )


class RecordingPlansDomainService:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.plan = _plan()
        self.task = _task(plan_id=self.plan.id)

    async def list_plans(self, **_kwargs: object) -> list[object]:
        return [self.plan]

    async def list_tasks(self, **_kwargs: object) -> list[object]:
        return [self.task]

    async def get_plan(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID,
    ) -> object:
        del owner_user_id
        self.calls.append("get_plan")
        self.plan.id = plan_id
        return self.plan

    async def get_task(
        self,
        *,
        owner_user_id: UUID,
        task_id: UUID,
    ) -> object:
        del owner_user_id
        self.calls.append("get_task")
        self.task.id = task_id
        return self.task

    async def create_plan(self, **kwargs: Any) -> object:
        self.calls.append("create_plan")
        self.plan.id = uuid4()
        self.plan.owner_user_id = kwargs["owner_user_id"]
        self.plan.plan_type = kwargs["plan_type"]
        self.plan.title = kwargs["title"]
        self.plan.summary = kwargs["summary"]
        self.plan.source = kwargs["source"]
        self.plan.payload = kwargs["payload"]
        return self.plan

    async def create_task(self, **kwargs: Any) -> object:
        self.calls.append("create_task")
        task = _task(
            owner_user_id=kwargs["owner_user_id"],
            plan_id=kwargs["plan_id"],
            task_date=kwargs["task_date"],
            task_time=kwargs["task_time"],
            title=kwargs["title"],
        )
        self.task = task
        return task

    async def set_task_completed(self, **kwargs: Any) -> object:
        self.calls.append("set_task_completed")
        self.task.id = kwargs["task_id"]
        self.task.owner_user_id = kwargs["owner_user_id"]
        self.task.status = "completed" if kwargs["completed"] else "pending"
        return self.task

    async def update_task(self, **kwargs: Any) -> object:
        self.calls.append("update_task")
        self.task.id = kwargs["task_id"]
        self.task.owner_user_id = kwargs["owner_user_id"]
        for field, value in kwargs["updates"].items():
            setattr(self.task, field, value)
        return self.task

    async def delete_task(self, **_kwargs: Any) -> None:
        self.calls.append("delete_task")

    async def delete_plan(self, **_kwargs: Any) -> None:
        self.calls.append("delete_plan")

    async def replace_future_milk_plan_tasks(self, **kwargs: Any) -> list[object]:
        self.calls.append("replace_future_milk_plan_tasks")
        return [
            _task(owner_user_id=kwargs["owner_user_id"], task_id=task_id)
            for task_id in kwargs["expected_task_ids"]
        ]

    async def reschedule_milk_tasks(self, **kwargs: Any) -> list[object]:
        self.calls.append("reschedule_milk_tasks")
        return [
            _task(
                owner_user_id=kwargs["owner_user_id"],
                task_id=UUID(str(update["task_id"])),
                plan_id=kwargs["plan_id"],
                task_date=date.fromisoformat(str(update["new_task_date"])),
                task_time=str(update["new_task_time"]),
            )
            for update in kwargs["updates"]
        ]


class InMemoryIdempotencyService:
    def __init__(self) -> None:
        self.records: dict[str, SimpleNamespace] = {}

    async def reserve(self, **kwargs: object) -> object:
        key = str(kwargs["key"])
        existing = self.records.get(key)
        if existing is not None:
            if existing.request_hash != kwargs["request_hash"]:
                raise ApiError(
                    code="idempotency_conflict",
                    message="conflict",
                    status=409,
                )
            return SimpleNamespace(status="replay", record=existing)
        record = SimpleNamespace(
            request_hash=kwargs["request_hash"],
            response_ref="",
            status="in_progress",
        )
        self.records[key] = record
        return SimpleNamespace(status="reserved", record=record)

    async def mark_completed(
        self,
        *,
        record: SimpleNamespace,
        response_ref: str,
    ) -> object:
        record.status = "completed"
        record.response_ref = response_ref
        return record


class RecordingAuditService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(id=uuid4())


def _plan(
    *,
    owner_user_id: UUID | None = None,
    plan_id: UUID | None = None,
    plan_type: str = "milk_management",
) -> SimpleNamespace:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=plan_id or uuid4(),
        owner_user_id=owner_user_id or uuid4(),
        plan_type=plan_type,
        title="计划",
        summary="计划摘要",
        status="active",
        source="agent_action",
        payload={"start_date": "2026-07-28", "days": 2},
        version=1,
        created_at=now,
        updated_at=now,
    )


def _task(
    *,
    owner_user_id: UUID | None = None,
    task_id: UUID | None = None,
    plan_id: UUID | None = None,
    task_date: date = date(2026, 7, 28),
    task_time: str = "08:00",
    title: str = "吸奶",
) -> SimpleNamespace:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=task_id or uuid4(),
        owner_user_id=owner_user_id or uuid4(),
        plan_id=plan_id,
        task_date=task_date,
        task_time=task_time,
        title=title,
        description="",
        status="pending",
        completed_at=None,
        payload={"task_type": "pumping"},
        created_at=now,
        updated_at=now,
    )


def _task_create_command(
    *,
    actor_user_id: UUID,
    action_id: UUID,
    run_id: UUID | None = None,
) -> dict[str, object]:
    return {
        "actor_user_id": str(actor_user_id),
        "action_id": str(action_id),
        "run_id": str(run_id or uuid4()),
        "action_type": "plans.task.create",
        "payload": {
            "task_date": "2026-07-28",
            "task_time": "08:00",
            "title": "吸奶",
            "payload": {"task_type": "pumping"},
        },
    }


def _action_command(kind: str) -> dict[str, Any]:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    plan_id = uuid4()
    task_id = uuid4()
    common = {
        "actor_user_id": str(actor_user_id),
        "action_id": str(action_id),
        "run_id": str(run_id),
    }
    commands: dict[str, dict[str, Any]] = {
        "task_create": {
            **common,
            "action_type": "plans.task.create",
            "payload": {
                "plan_id": str(plan_id),
                "task_date": "2026-07-28",
                "task_time": "08:00",
                "title": "吸奶",
                "payload": {"task_type": "pumping"},
            },
        },
        "task_complete": {
            **common,
            "action_type": "plans.task.complete",
            "payload": {"task_id": str(task_id), "completed": True},
        },
        "task_update": {
            **common,
            "action_type": "plans.task.update",
            "payload": {
                "task_id": str(task_id),
                "task_time": "09:00",
                "title": "调整后的吸奶",
            },
        },
        "task_delete": {
            **common,
            "action_type": "plans.task.delete",
            "payload": {"task_id": str(task_id), "reason": "用户明确要求删除"},
        },
        "plan_delete": {
            **common,
            "action_type": "plans.plan.delete",
            "payload": {"plan_id": str(plan_id), "reason": "用户明确要求删除"},
        },
        "pregnancy_create": {
            **common,
            "action_type": "pregnancy.plan.create",
            "payload": {
                "title": "孕期计划",
                "summary": "从现在到生产前后的阶段计划与待办",
                "payload": {
                    "plan_context": {"current_week": "28周"},
                    "card": {"card_json": {"title": "孕期计划"}},
                },
            },
        },
        "milk_create": {
            **common,
            "action_type": "plans.milk_plan.create",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            "payload": {
                "title": "2 天稳奶计划",
                "summary": "沿用近期可执行节奏。",
                "calendar_write_strategy": "append",
                "expected_replaced_task_ids": [],
                "payload": {
                    "direction": "maintain",
                    "analysis_context_fingerprint": "fingerprint",
                    "analysis_workflow_state_id": str(uuid4()),
                    "start_date": "2026-07-28",
                    "days": 2,
                    "tasks": [
                        {
                            "title": "稳奶吸奶",
                            "time": "08:00",
                            "task_type": "pumping",
                        }
                    ],
                },
            },
        },
        "milk_reschedule": {
            **common,
            "action_type": "plans.milk_schedule.reschedule",
            "payload": {
                "plan_id": str(plan_id),
                "updates": [
                    {
                        "task_id": str(task_id),
                        "expected_plan_id": str(plan_id),
                        "expected_task_date": "2026-07-28",
                        "expected_task_time": "08:00",
                        "new_task_date": "2026-07-28",
                        "new_task_time": "09:00",
                    }
                ],
                "calendar_events": [],
            },
        },
    }
    return commands[kind]


def _app():
    return create_app(
        Settings(
            app_env="test",
            agent_runtime_service_api_key=SERVICE_KEY,
        )
    )
