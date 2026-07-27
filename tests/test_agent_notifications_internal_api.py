from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.notifications.agent_router import (
    get_agent_milk_reminder_action_service,
)
from app.modules.notifications.agent_service import AgentMilkReminderActionService
from app.modules.notifications.models import Notification


SERVICE_KEY = "agent-runtime-service-key-with-32-bytes"


def test_agent_milk_reminder_apply_requires_runtime_identity() -> None:
    response = TestClient(_app()).post(
        "/v1/internal/agent/actions/notifications.milk_reminder/apply",
        json=_command(operation="create"),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_milk_reminder_apply_requires_action_bound_idempotency_key() -> None:
    action_id = uuid4()
    app = _app()
    app.dependency_overrides[get_agent_milk_reminder_action_service] = (
        lambda: FakeMilkReminderActionService()
    )
    client = TestClient(app)

    missing = client.post(
        "/v1/internal/agent/actions/notifications.milk_reminder/apply",
        headers={"X-Service-Key": SERVICE_KEY},
        json=_command(operation="create", action_id=action_id),
    )
    wrong = client.post(
        "/v1/internal/agent/actions/notifications.milk_reminder/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{uuid4()}",
        },
        json=_command(operation="create", action_id=action_id),
    )

    assert missing.status_code == 422
    assert wrong.status_code == 422


@pytest.mark.parametrize("operation", ("create", "update", "delete", "disable"))
def test_agent_milk_reminder_route_forwards_supported_operation(
    operation: str,
) -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    service = FakeMilkReminderActionService()
    app = _app()
    app.dependency_overrides[get_agent_milk_reminder_action_service] = lambda: service

    response = TestClient(app).post(
        "/v1/internal/agent/actions/notifications.milk_reminder/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{action_id}",
            "X-Request-ID": "req-reminder",
        },
        json=_command(
            operation=operation,
            actor_user_id=actor_user_id,
            action_id=action_id,
            run_id=run_id,
        ),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert service.kwargs["owner_user_id"] == actor_user_id
    assert service.kwargs["action_id"] == action_id
    assert service.kwargs["run_id"] == run_id
    assert service.kwargs["actor_service"] == "agent-runtime"
    assert service.kwargs["payload"].operation == operation


def test_agent_milk_reminder_write_replays_without_second_mutation_or_audit() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    repository = InMemoryNotificationsRepository()
    idempotency = InMemoryIdempotencyService()
    audit = RecordingAuditService()
    service = AgentMilkReminderActionService(
        repository=repository,
        idempotency_service=idempotency,
        audit_service=audit,
    )
    kwargs: dict[str, Any] = {
        "owner_user_id": actor_user_id,
        "payload": {
            "operation": "create",
            "title": "吸奶提醒",
            "body": "准备好吸奶器",
            "remind_at": "2026-07-27T09:00:00+08:00",
            "payload": {"plan_task_id": "task-1"},
        },
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "run_id": run_id,
        "actor_service": "agent-runtime",
        "request_id": "req-reminder",
    }

    first = asyncio.run(service.apply_idempotent(**kwargs))
    replay = asyncio.run(service.apply_idempotent(**kwargs))

    assert first == replay
    assert repository.create_calls == 1
    assert len(audit.calls) == 1
    notification = repository.notification
    assert notification is not None
    assert notification.owner_user_id == actor_user_id
    assert notification.notification_type == "milk_reminder"
    assert notification.payload["agent_action_id"] == str(action_id)


def test_agent_milk_reminder_mutations_are_owner_scoped() -> None:
    reminder = _notification(owner_user_id=uuid4())
    service = AgentMilkReminderActionService(
        repository=InMemoryNotificationsRepository(notification=reminder),
        idempotency_service=InMemoryIdempotencyService(),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.apply_idempotent(
                owner_user_id=uuid4(),
                payload={
                    "operation": "disable",
                    "reminder_id": str(reminder.id),
                },
                idempotency_key=f"agent-action:{(action_id := uuid4())}",
                action_id=action_id,
                run_id=uuid4(),
                actor_service="agent-runtime",
                request_id="req-reminder",
            )
        )

    assert exc_info.value.code == "not_found"


def test_agent_milk_reminder_supports_update_disable_and_delete() -> None:
    owner_user_id = uuid4()
    reminder = _notification(owner_user_id=owner_user_id)
    repository = InMemoryNotificationsRepository(notification=reminder)
    service = AgentMilkReminderActionService(
        repository=repository,
        idempotency_service=InMemoryIdempotencyService(),
    )

    updated = _apply(
        service,
        owner_user_id=owner_user_id,
        payload={
            "operation": "update",
            "reminder_id": str(reminder.id),
            "title": "新的提醒",
            "remind_at": "2026-07-28T10:00:00+08:00",
        },
    )
    disabled = _apply(
        service,
        owner_user_id=owner_user_id,
        payload={"operation": "disable", "reminder_id": str(reminder.id)},
    )
    deleted = _apply(
        service,
        owner_user_id=owner_user_id,
        payload={"operation": "delete", "reminder_id": str(reminder.id)},
    )

    assert updated.details["operation"] == "updated"
    assert reminder.title == "新的提醒"
    assert disabled.details["status"] == "disabled"
    assert reminder.status == "disabled"
    assert deleted.details["operation"] == "deleted"
    assert repository.notification is None


class FakeMilkReminderActionService:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def apply_idempotent(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        operation = kwargs["payload"].operation
        return SimpleNamespace(
            resource_type="milk_reminder",
            resource_id=str(uuid4()),
            details={"operation": f"{operation}d", "status": "scheduled"},
            application_events=(),
        )


class InMemoryNotificationsRepository:
    def __init__(self, *, notification: Notification | None = None) -> None:
        self.notification = notification
        self.create_calls = 0

    async def create_notification(self, **kwargs: Any) -> Notification:
        self.create_calls += 1
        self.notification = Notification(
            id=uuid4(),
            status="scheduled",
            **kwargs,
        )
        return self.notification

    async def get_milk_reminder_for_owner(
        self,
        *,
        reminder_id: UUID,
        owner_user_id: UUID,
    ) -> Notification | None:
        if (
            self.notification is not None
            and self.notification.id == reminder_id
            and self.notification.owner_user_id == owner_user_id
            and self.notification.notification_type == "milk_reminder"
        ):
            return self.notification
        return None

    async def delete_milk_reminder(
        self,
        *,
        reminder: Notification,
    ) -> None:
        assert self.notification is reminder
        self.notification = None

    async def flush(self) -> None:
        return None


class InMemoryIdempotencyService:
    def __init__(self) -> None:
        self.records: dict[str, SimpleNamespace] = {}

    async def reserve(self, **kwargs: Any) -> object:
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

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _command(
    *,
    operation: str,
    actor_user_id: UUID | None = None,
    action_id: UUID | None = None,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"operation": operation}
    if operation == "create":
        payload.update(
            {
                "title": "吸奶提醒",
                "remind_at": "2026-07-27T09:00:00+08:00",
            }
        )
    else:
        payload["reminder_id"] = str(uuid4())
        if operation == "update":
            payload["title"] = "新的吸奶提醒"
    return {
        "actor_user_id": str(actor_user_id or uuid4()),
        "action_id": str(action_id or uuid4()),
        "run_id": str(run_id or uuid4()),
        "payload": payload,
    }


def _apply(
    service: AgentMilkReminderActionService,
    *,
    owner_user_id: UUID,
    payload: dict[str, Any],
) -> Any:
    action_id = uuid4()
    return asyncio.run(
        service.apply_idempotent(
            owner_user_id=owner_user_id,
            payload=payload,
            idempotency_key=f"agent-action:{action_id}",
            action_id=action_id,
            run_id=uuid4(),
            actor_service="agent-runtime",
            request_id="req-reminder",
        )
    )


def _notification(*, owner_user_id: UUID) -> Notification:
    return Notification(
        id=uuid4(),
        owner_user_id=owner_user_id,
        notification_type="milk_reminder",
        title="吸奶提醒",
        body="",
        status="scheduled",
        source="agent_action",
        payload={"remind_at": "2026-07-27T09:00:00+08:00"},
        delivered_at=None,
    )


def _app():
    return create_app(
        Settings(
            app_env="test",
            agent_runtime_service_api_key=SERVICE_KEY,
        )
    )
