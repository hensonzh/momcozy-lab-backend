from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.diary.agent_router import (
    get_agent_diary_read_service,
    get_agent_diary_write_service,
)
from app.modules.diary.agent_service import AgentDiaryWriteService


SERVICE_KEY = "agent-runtime-service-key-with-32-bytes"


def test_agent_diary_read_uses_runtime_identity_and_explicit_actor_scope() -> None:
    actor_user_id = uuid4()
    service = FakeDiaryReadService()
    app = _app()
    app.dependency_overrides[get_agent_diary_read_service] = lambda: service

    response = TestClient(app).get(
        "/v1/internal/agent/pregnancy-diary",
        headers={"X-Service-Key": SERVICE_KEY},
        params={
            "actor_user_id": str(actor_user_id),
            "entry_date": "2026-07-26",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "entry_read"
    assert service.get_kwargs == {
        "owner_user_id": actor_user_id,
        "entry_date": date(2026, 7, 26),
    }


def test_agent_diary_routes_reject_missing_service_identity() -> None:
    response = TestClient(_app()).get(
        "/v1/internal/agent/pregnancy-diary",
        params={"actor_user_id": str(uuid4())},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_agent_diary_apply_requires_action_bound_idempotency_key() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    app = _app()
    app.dependency_overrides[get_agent_diary_write_service] = (
        lambda: FakeDiaryWriteService()
    )

    missing = TestClient(app).post(
        "/v1/internal/agent/actions/pregnancy-diary.entry/apply",
        headers={"X-Service-Key": SERVICE_KEY},
        json=_apply_command(actor_user_id=actor_user_id, action_id=action_id),
    )
    wrong = TestClient(app).post(
        "/v1/internal/agent/actions/pregnancy-diary.entry/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{uuid4()}",
        },
        json=_apply_command(actor_user_id=actor_user_id, action_id=action_id),
    )

    assert missing.status_code == 422
    assert wrong.status_code == 422


def test_agent_diary_apply_forwards_action_identity() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    service = FakeDiaryWriteService()
    app = _app()
    app.dependency_overrides[get_agent_diary_write_service] = lambda: service

    response = TestClient(app).post(
        "/v1/internal/agent/actions/pregnancy-diary.entry/apply",
        headers={
            "X-Service-Key": SERVICE_KEY,
            "Idempotency-Key": f"agent-action:{action_id}",
            "X-Request-ID": "req-diary",
        },
        json={
            "actor_user_id": str(actor_user_id),
            "action_id": str(action_id),
            "run_id": str(run_id),
            "payload": {
                "operation": "create",
                "entry_date": "2026-07-26",
                "content": "今天感觉很好。",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert response.json()["application_events"][0]["type"] == (
        "pregnancy_diary.changed"
    )
    assert service.kwargs["owner_user_id"] == actor_user_id
    assert service.kwargs["action_id"] == action_id
    assert service.kwargs["run_id"] == run_id
    assert service.kwargs["idempotency_key"] == f"agent-action:{action_id}"
    assert service.kwargs["actor_service"] == "agent-runtime"


def test_agent_diary_write_replay_does_not_repeat_business_write_or_audit() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    diary_service = RecordingDiaryService()
    idempotency_service = InMemoryIdempotencyService()
    audit_service = RecordingAuditService()
    service = AgentDiaryWriteService(
        diary_service=diary_service,
        idempotency_service=idempotency_service,
        audit_service=audit_service,
    )
    kwargs: dict[str, Any] = {
        "owner_user_id": actor_user_id,
        "payload": {
            "operation": "create",
            "entry_date": date(2026, 7, 26),
            "content": "今天感觉很好。",
        },
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "run_id": run_id,
        "actor_service": "agent-runtime",
        "request_id": "req-diary",
    }

    first = asyncio.run(service.apply_idempotent(**kwargs))
    replay = asyncio.run(service.apply_idempotent(**kwargs))

    assert first == replay
    assert diary_service.create_calls == 1
    assert len(audit_service.calls) == 1


def test_agent_diary_write_rejects_same_action_bound_to_another_run() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    service = AgentDiaryWriteService(
        diary_service=RecordingDiaryService(),
        idempotency_service=InMemoryIdempotencyService(),
    )
    kwargs: dict[str, Any] = {
        "owner_user_id": actor_user_id,
        "payload": {
            "operation": "delete",
            "entry_date": date(2026, 7, 26),
        },
        "idempotency_key": f"agent-action:{action_id}",
        "action_id": action_id,
        "actor_service": "agent-runtime",
        "request_id": "",
    }

    asyncio.run(service.apply_idempotent(run_id=uuid4(), **kwargs))
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.apply_idempotent(run_id=uuid4(), **kwargs))

    assert exc_info.value.code == "idempotency_conflict"


class FakeDiaryReadService:
    def __init__(self) -> None:
        self.get_kwargs: dict[str, object] = {}

    async def get_entry(self, **kwargs: object) -> object:
        self.get_kwargs = kwargs
        return _entry(owner_user_id=kwargs["owner_user_id"])  # type: ignore[arg-type]

    async def list_entries(self, **_kwargs: object) -> list[object]:
        return []


class FakeDiaryWriteService:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def apply_idempotent(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return SimpleNamespace(
            resource_type="pregnancy_diary_entry",
            resource_id=str(uuid4()),
            details={"operation": "created", "changed": True},
            application_events=(
                {
                    "type": "pregnancy_diary.changed",
                    "payload": {
                        "operation": "created",
                        "entry_date": "2026-07-26",
                    },
                },
            ),
        )


class RecordingDiaryService:
    def __init__(self) -> None:
        self.create_calls = 0

    async def create_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, object],
        request_id: str,
    ) -> object:
        del request_id
        self.create_calls += 1
        return _entry(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            content=str(values["content"]),
        )

    async def update_entry_with_status(self, **_kwargs: object) -> object:
        raise AssertionError("unexpected update")

    async def delete_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        request_id: str,
    ) -> object:
        del request_id
        return _entry(owner_user_id=owner_user_id, entry_date=entry_date)


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
        self.calls: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _entry(
    *,
    owner_user_id: UUID,
    entry_date: date = date(2026, 7, 26),
    content: str = "今天感觉很好。",
) -> object:
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        owner_user_id=owner_user_id,
        entry_date=entry_date,
        gestational_week="",
        mood="",
        energy_level="",
        sleep_summary="",
        fetal_movement="",
        symptom_tags=[],
        appointment_note="",
        nutrition_note="",
        content=content,
        attachments=[],
        status="active",
        created_at=now,
        updated_at=now,
    )


def _apply_command(*, actor_user_id: UUID, action_id: UUID) -> dict[str, object]:
    return {
        "actor_user_id": str(actor_user_id),
        "action_id": str(action_id),
        "run_id": str(uuid4()),
        "payload": {
            "operation": "delete",
            "entry_date": "2026-07-26",
        },
    }


def _app() -> FastAPI:
    return create_app(
        Settings(
            app_env="test",
            agent_runtime_service_api_key=SERVICE_KEY,
        )
    )
