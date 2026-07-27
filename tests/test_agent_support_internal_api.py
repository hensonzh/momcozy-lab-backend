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
from app.modules.support.agent_router import get_agent_support_action_service
from app.modules.support.agent_service import AgentSupportTicketActionService
from app.modules.support.models import SupportTicket


SERVICE_KEY = "agent-runtime-service-key-with-32-bytes"


def test_agent_support_apply_requires_runtime_identity() -> None:
    response = TestClient(_app()).post(
        "/v1/internal/agent/actions/support.ticket/apply",
        json=_command(),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_support_apply_requires_action_bound_idempotency_key() -> None:
    action_id = uuid4()
    app = _app()
    app.dependency_overrides[get_agent_support_action_service] = (
        lambda: FakeSupportActionService()
    )
    client = TestClient(app)

    missing = client.post(
        "/v1/internal/agent/actions/support.ticket/apply",
        headers={"X-Service-Key": SERVICE_KEY},
        json=_command(action_id=action_id),
    )
    wrong = client.post(
        "/v1/internal/agent/actions/support.ticket/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{uuid4()}",
        },
        json=_command(action_id=action_id),
    )

    assert missing.status_code == 422
    assert wrong.status_code == 422


def test_agent_support_apply_forwards_explicit_actor_and_action_identity() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    service = FakeSupportActionService()
    app = _app()
    app.dependency_overrides[get_agent_support_action_service] = lambda: service

    response = TestClient(app).post(
        "/v1/internal/agent/actions/support.ticket/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{action_id}",
            "X-Request-ID": "req-support",
        },
        json=_command(
            actor_user_id=actor_user_id,
            action_id=action_id,
            run_id=run_id,
        ),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert response.json()["resource_type"] == "support_ticket"
    assert service.kwargs["owner_user_id"] == actor_user_id
    assert service.kwargs["action_id"] == action_id
    assert service.kwargs["run_id"] == run_id
    assert service.kwargs["actor_service"] == "agent-runtime"


def test_agent_support_submit_replays_without_duplicate_ticket_or_audit() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    repository = InMemorySupportRepository()
    idempotency = InMemoryIdempotencyService()
    audit = RecordingAuditService()
    service = AgentSupportTicketActionService(
        repository=repository,
        idempotency_service=idempotency,
        audit_service=audit,
    )
    kwargs: dict[str, Any] = {
        "owner_user_id": actor_user_id,
        "payload": {
            "operation": "create",
            "issue_type": "malfunction",
            "issue_summary": "Air1 充电后无法开机",
            "product_model": "Air1",
            "urgency": "high",
            "troubleshooting_done": ["重新充电", "长按电源键"],
        },
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "run_id": run_id,
        "actor_service": "agent-runtime",
        "request_id": "req-support",
    }

    first = asyncio.run(service.apply_idempotent(**kwargs))
    replay = asyncio.run(service.apply_idempotent(**kwargs))

    assert first == replay
    assert repository.create_calls == 1
    assert len(audit.calls) == 1
    assert repository.ticket is not None
    assert repository.ticket.owner_user_id == actor_user_id
    assert repository.ticket.source == "agent_action"
    assert repository.ticket.payload["agent_action_id"] == str(action_id)
    assert audit.calls[0]["actor_type"] == "service"
    assert audit.calls[0]["actor_service"] == "agent-runtime"


def test_agent_support_submit_rejects_reusing_action_for_other_run() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    service = AgentSupportTicketActionService(
        repository=InMemorySupportRepository(),
        idempotency_service=InMemoryIdempotencyService(),
    )
    kwargs: dict[str, Any] = {
        "owner_user_id": actor_user_id,
        "payload": {
            "operation": "create",
            "issue_summary": "Air1 无法开机",
        },
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "actor_service": "agent-runtime",
        "request_id": "req-support",
    }

    asyncio.run(service.apply_idempotent(run_id=uuid4(), **kwargs))
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.apply_idempotent(run_id=uuid4(), **kwargs))

    assert exc_info.value.code == "idempotency_conflict"


class FakeSupportActionService:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def apply_idempotent(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        return SimpleNamespace(
            resource_type="support_ticket",
            resource_id=str(uuid4()),
            details={"ticket_number": "ticket_agent"},
            application_events=(),
        )


class InMemorySupportRepository:
    def __init__(self) -> None:
        self.ticket: SupportTicket | None = None
        self.create_calls = 0

    async def create_ticket(self, **kwargs: Any) -> SupportTicket:
        self.create_calls += 1
        self.ticket = SupportTicket(id=uuid4(), status="submitted", **kwargs)
        return self.ticket


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
    actor_user_id: UUID | None = None,
    action_id: UUID | None = None,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "actor_user_id": str(actor_user_id or uuid4()),
        "action_id": str(action_id or uuid4()),
        "run_id": str(run_id or uuid4()),
        "payload": {
            "operation": "create",
            "issue_type": "malfunction",
            "issue_summary": "Air1 充电后无法开机",
            "product_model": "Air1",
            "urgency": "high",
        },
    }


def _app():
    return create_app(
        Settings(
            app_env="test",
            agent_runtime_service_api_key=SERVICE_KEY,
        )
    )
