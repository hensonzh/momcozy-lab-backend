from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.audit import request_hash
from app.modules.profiles.agent_router import (
    get_agent_profile_read_service,
    get_agent_profile_update_service,
)
from app.modules.profiles.agent_service import (
    AGENT_PROFILE_UPDATE_ACTION_TYPE,
    AgentProfileUpdateService,
)


SERVICE_KEY = "agent-runtime-service-key-with-32-bytes"


def test_agent_profile_read_uses_service_identity_and_explicit_actor_scope() -> None:
    actor_user_id = uuid4()
    service = FakeAgentProfileReadService()
    app = _app()
    app.dependency_overrides[get_agent_profile_read_service] = lambda: service

    response = TestClient(app).get(
        "/v1/internal/agent/profile",
        headers={"X-Service-Key": SERVICE_KEY},
        params={
            "actor_user_id": str(actor_user_id),
            "infant_scope": "all",
            "as_of_date": "2026-07-26",
        },
    )

    assert response.status_code == 200
    assert response.json()["infant_scope"] == "all"
    assert service.kwargs == {
        "owner_user_id": actor_user_id,
        "as_of_date": date(2026, 7, 26),
        "infant_scope": "all",
    }


def test_agent_profile_internal_routes_reject_missing_service_identity() -> None:
    response = TestClient(_app()).get(
        "/v1/internal/agent/profile",
        params={"actor_user_id": str(uuid4())},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_profile_update_requires_idempotency_key() -> None:
    actor_user_id = uuid4()
    app = _app()
    app.dependency_overrides[get_agent_profile_update_service] = lambda: FakeAgentProfileUpdateService()

    response = TestClient(app).post(
        "/v1/internal/agent/actions/profile.update/apply",
        headers={"X-Service-Key": SERVICE_KEY},
        json=_update_command(actor_user_id=actor_user_id),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_agent_profile_update_requires_idempotency_key_bound_to_action_id() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    app = _app()
    app.dependency_overrides[get_agent_profile_update_service] = lambda: FakeAgentProfileUpdateService()

    response = TestClient(app).post(
        "/v1/internal/agent/actions/profile.update/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{uuid4()}",
        },
        json=_update_command(actor_user_id=actor_user_id, action_id=action_id),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_agent_profile_update_forwards_action_identity_and_returns_stable_result() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    service = FakeAgentProfileUpdateService()
    app = _app()
    app.dependency_overrides[get_agent_profile_update_service] = lambda: service

    response = TestClient(app).post(
        "/v1/internal/agent/actions/profile.update/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{action_id}",
            "X-Request-ID": "req_profile_apply",
        },
        json={
            "actor_user_id": str(actor_user_id),
            "action_id": str(action_id),
            "run_id": str(run_id),
            "payload": {"mother": {"preferred_name": "Mai"}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "applied",
        "action_id": str(action_id),
        "resource_type": "profile",
        "resource_id": str(actor_user_id),
        "details": {
            "mother_fields": ["preferred_name"],
            "infants": [],
            "current_infants_updated": False,
        },
        "application_events": [],
    }
    assert service.kwargs == {
        "owner_user_id": actor_user_id,
        "payload": {"mother": {"preferred_name": "Mai"}},
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "run_id": run_id,
        "actor_service": "agent-runtime",
        "request_id": "req_profile_apply",
    }


def test_agent_profile_update_service_replays_without_reapplying_business_write() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    profile_service = RecordingProfileService()
    lactation_service = RecordingLactationContextService()
    idempotency_service = InMemoryIdempotencyService()
    audit_service = RecordingAuditService()
    service = AgentProfileUpdateService(
        profile_service=profile_service,
        lactation_context_service=lactation_service,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
    )
    payload = {"mother": {"preferred_name": "Mai"}}

    first = asyncio.run(
        service.apply_idempotent(
            owner_user_id=actor_user_id,
            payload=payload,
            idempotency_key=f"agent-action:{action_id}",
            action_id=action_id,
            run_id=run_id,
            actor_service="agent-runtime",
            request_id="req-1",
        )
    )
    replay = asyncio.run(
        service.apply_idempotent(
            owner_user_id=actor_user_id,
            payload=payload,
            idempotency_key=f"agent-action:{action_id}",
            action_id=action_id,
            run_id=run_id,
            actor_service="agent-runtime",
            request_id="req-2",
        )
    )

    assert first == replay
    assert profile_service.calls == 1
    assert lactation_service.calls == 0
    assert idempotency_service.records[f"agent-action:{action_id}"].request_hash == request_hash(
        {
            "actor": {
                "type": "service",
                "service": "agent-runtime",
                "user_id": str(actor_user_id),
            },
            "action_id": str(action_id),
            "run_id": str(run_id),
            "action_type": AGENT_PROFILE_UPDATE_ACTION_TYPE,
            "payload": payload,
        }
    )
    assert len(audit_service.calls) == 1
    assert audit_service.calls[0] == {
        "actor_user_id": None,
        "actor_type": "service",
        "actor_service": "agent-runtime",
        "action": "profiles.update",
        "resource_type": "profile",
        "resource_id": str(actor_user_id),
        "request_id": "req-1",
        "details": {
            "owner_user_id": str(actor_user_id),
            "action_id": str(action_id),
            "run_id": str(run_id),
            "action_type": AGENT_PROFILE_UPDATE_ACTION_TYPE,
            "mother_fields": ["preferred_name"],
        },
    }


def test_agent_profile_update_service_rejects_same_action_from_different_run() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    service = AgentProfileUpdateService(
        profile_service=RecordingProfileService(),
        lactation_context_service=RecordingLactationContextService(),
        idempotency_service=InMemoryIdempotencyService(),
    )
    kwargs = {
        "owner_user_id": actor_user_id,
        "payload": {"mother": {"preferred_name": "Mai"}},
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "actor_service": "agent-runtime",
        "request_id": "req-1",
    }

    asyncio.run(service.apply_idempotent(run_id=uuid4(), **kwargs))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.apply_idempotent(run_id=uuid4(), **kwargs))

    assert exc_info.value.code == "idempotency_conflict"


def test_agent_profile_update_service_rejects_key_for_different_action() -> None:
    service = AgentProfileUpdateService(
        profile_service=RecordingProfileService(),
        lactation_context_service=RecordingLactationContextService(),
        idempotency_service=InMemoryIdempotencyService(),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.apply_idempotent(
                owner_user_id=uuid4(),
                payload={"mother": {"preferred_name": "Mai"}},
                idempotency_key=f"agent-action:{uuid4()}",
                action_id=uuid4(),
                run_id=uuid4(),
                actor_service="agent-runtime",
                request_id="req-1",
            )
        )

    assert exc_info.value.code == "validation_failed"


def test_agent_profile_update_service_rejects_replay_bound_to_other_action() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    idempotency_service = InMemoryIdempotencyService()
    service = AgentProfileUpdateService(
        profile_service=RecordingProfileService(),
        lactation_context_service=RecordingLactationContextService(),
        idempotency_service=idempotency_service,
    )
    kwargs = {
        "owner_user_id": actor_user_id,
        "payload": {"mother": {"preferred_name": "Mai"}},
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "run_id": uuid4(),
        "actor_service": "agent-runtime",
        "request_id": "req-1",
    }

    asyncio.run(service.apply_idempotent(**kwargs))
    idempotency_service.records[kwargs["idempotency_key"]].response_ref = str(uuid4())

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.apply_idempotent(**kwargs))

    assert exc_info.value.code == "conflict"


def test_agent_profile_update_factory_audits_only_at_action_boundary() -> None:
    service = get_agent_profile_update_service(session=SimpleNamespace())

    assert service.profile_service.audit_service is None
    assert service.lactation_context_service.audit_service is None
    assert service.audit_service is not None


def _app():
    return create_app(
        Settings(
            app_env="test",
            agent_runtime_service_api_key=SERVICE_KEY,
        )
    )


def _update_command(*, actor_user_id: UUID, action_id: UUID | None = None) -> dict:
    return {
        "actor_user_id": str(actor_user_id),
        "action_id": str(action_id or uuid4()),
        "run_id": str(uuid4()),
        "payload": {"mother": {"preferred_name": "Mai"}},
    }


class FakeAgentProfileReadService:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def read(self, **kwargs):
        self.kwargs = kwargs
        return {
            "as_of_date": kwargs["as_of_date"] or date.today(),
            "infant_scope": kwargs["infant_scope"],
            "mother": {
                "preferred_name": None,
                "age": None,
                "estimated_due_date": None,
                "delivery_count": None,
                "current_delivery_method": None,
                "actual_delivery_date": None,
                "has_cesarean_history": None,
                "postpartum_days": None,
                "current_feeding_mode": None,
            },
            "infants": [],
            "missing_fields": [],
            "data_quality_issues": [],
        }


class FakeAgentProfileUpdateService:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def apply_idempotent(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            resource_type="profile",
            resource_id=str(kwargs["owner_user_id"]),
            details={"mother_fields": ["preferred_name"]},
        )


class RecordingProfileService:
    def __init__(self) -> None:
        self.calls = 0

    async def update_profile(self, **_kwargs):
        self.calls += 1
        return None, []


class RecordingLactationContextService:
    def __init__(self) -> None:
        self.calls = 0

    async def update_maternal_profile(self, **_kwargs):
        self.calls += 1
        return None, []


class InMemoryIdempotencyService:
    def __init__(self) -> None:
        self.records: dict[str, SimpleNamespace] = {}

    async def reserve(self, **kwargs):
        key = kwargs["key"]
        record = self.records.get(key)
        if record is None:
            record = SimpleNamespace(
                status="in_progress",
                response_ref="",
                request_hash=kwargs["request_hash"],
            )
            self.records[key] = record
            return SimpleNamespace(status="reserved", record=record)
        if record.request_hash != kwargs["request_hash"]:
            raise ApiError(
                code="idempotency_conflict",
                message="Idempotency key was reused with a different request.",
                status=409,
            )
        return SimpleNamespace(status="replay", record=record)

    async def mark_completed(self, *, record, response_ref: str):
        record.status = "completed"
        record.response_ref = response_ref
        return record


class RecordingAuditService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record(self, **kwargs):
        self.calls.append(kwargs)
        return None
