import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.audit.service import AuditService, IdempotencyService, request_hash


def test_audit_service_records_actor_resource_and_request() -> None:
    repository = FakeAuditRepository()
    service = AuditService(repository=repository)
    actor_user_id = uuid4()

    asyncio.run(
        service.record(
            actor_user_id=actor_user_id,
            action="files.upload",
            resource_type="file",
            resource_id="file-1",
            request_id="req_123",
            details={"content_type": "image/png"},
        )
    )

    assert repository.audit_kwargs["actor_user_id"] == actor_user_id
    assert repository.audit_kwargs["actor_type"] == "user"
    assert repository.audit_kwargs["action"] == "files.upload"
    assert repository.audit_kwargs["request_id"] == "req_123"


def test_audit_service_can_attribute_service_actor() -> None:
    repository = FakeAuditRepository()
    service = AuditService(repository=repository)

    asyncio.run(
        service.record(
            actor_user_id=None,
            actor_type="service",
            actor_service="notification-service",
            action="notifications.create",
            resource_type="notification",
            resource_id="notify-1",
        )
    )

    assert repository.audit_kwargs["actor_type"] == "service"
    assert repository.audit_kwargs["actor_service"] == "notification-service"


def test_idempotency_service_reserves_new_key() -> None:
    repository = FakeAuditRepository()
    service = IdempotencyService(repository=repository)
    actor_user_id = uuid4()

    decision = asyncio.run(
        service.reserve(
            actor_user_id=actor_user_id,
            scope="files.upload",
            key="idem-1",
            request_hash="hash-1",
            expires_at=_expires_at(),
        )
    )

    assert decision.status == "reserved"
    assert repository.created_idempotency.actor_user_id == actor_user_id
    assert repository.created_idempotency.scope == "files.upload"


def test_idempotency_service_replays_same_request_hash() -> None:
    existing = _idempotency_key(request_hash_value="hash-1")
    service = IdempotencyService(repository=FakeAuditRepository(existing_idempotency=existing))

    decision = asyncio.run(
        service.reserve(
            actor_user_id=existing.actor_user_id,
            scope=existing.scope,
            key=existing.key,
            request_hash="hash-1",
            expires_at=_expires_at(),
        )
    )

    assert decision.status == "replay"
    assert decision.record is existing


def test_idempotency_service_rejects_conflicting_request_hash() -> None:
    existing = _idempotency_key(request_hash_value="hash-1")
    service = IdempotencyService(repository=FakeAuditRepository(existing_idempotency=existing))

    with pytest.raises(ApiError, match="different request"):
        asyncio.run(
            service.reserve(
                actor_user_id=existing.actor_user_id,
                scope=existing.scope,
                key=existing.key,
                request_hash="hash-2",
                expires_at=_expires_at(),
            )
        )


def test_request_hash_is_stable_for_equivalent_dicts() -> None:
    assert request_hash({"b": 2, "a": 1}) == request_hash({"a": 1, "b": 2})


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _idempotency_key(*, request_hash_value: str) -> IdempotencyKey:
    return IdempotencyKey(
        actor_user_id=uuid4(),
        scope="files.upload",
        key="idem-1",
        request_hash=request_hash_value,
        expires_at=_expires_at(),
    )


class FakeAuditRepository:
    def __init__(self, *, existing_idempotency=None) -> None:
        self.existing_idempotency = existing_idempotency
        self.created_idempotency = None
        self.audit_kwargs = {}

    async def record_audit(self, **kwargs):
        self.audit_kwargs = kwargs
        return None

    async def get_idempotency_key(self, **kwargs):
        return self.existing_idempotency

    async def create_idempotency_key(self, **kwargs):
        self.created_idempotency = IdempotencyKey(**kwargs)
        return self.created_idempotency

    async def mark_idempotency_completed(self, *, idempotency_key, response_ref: str):
        idempotency_key.status = "completed"
        idempotency_key.response_ref = response_ref
        return idempotency_key
