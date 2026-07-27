import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.audit.models import IdempotencyKey
from app.modules.audit.service import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash


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


def test_idempotency_service_normalizes_scope_and_key() -> None:
    repository = FakeAuditRepository()
    service = IdempotencyService(repository=repository)

    decision = asyncio.run(
        service.reserve(
            actor_user_id=uuid4(),
            scope=" files.upload ",
            key=" idem-1 ",
            request_hash="hash-1",
            expires_at=_expires_at(),
        )
    )

    assert decision.status == "reserved"
    assert repository.lookup_kwargs["scope"] == "files.upload"
    assert repository.lookup_kwargs["key"] == "idem-1"
    assert repository.created_idempotency.scope == "files.upload"
    assert repository.created_idempotency.key == "idem-1"


def test_idempotency_service_releases_reserved_key() -> None:
    repository = FakeAuditRepository()
    service = IdempotencyService(repository=repository)
    record = _idempotency_key(request_hash_value="hash-1")

    asyncio.run(service.release(record=record))

    assert repository.deleted_idempotency is record


@pytest.mark.parametrize(
    ("scope", "key", "message"),
    [
        ("", "idem-1", "scope"),
        ("files.upload", "", "key"),
        ("x" * 121, "idem-1", "scope"),
        ("files.upload", "x" * 256, "key"),
    ],
)
def test_idempotency_service_rejects_invalid_scope_or_key(scope: str, key: str, message: str) -> None:
    service = IdempotencyService(repository=FakeAuditRepository())

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.reserve(
                actor_user_id=uuid4(),
                scope=scope,
                key=key,
                request_hash="hash-1",
                expires_at=_expires_at(),
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert message in exc_info.value.message.lower()


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


def test_idempotency_service_replays_compatible_historical_request_hash() -> None:
    existing = _idempotency_key(request_hash_value="legacy-hash")
    service = IdempotencyService(repository=FakeAuditRepository(existing_idempotency=existing))

    decision = asyncio.run(
        service.reserve(
            actor_user_id=existing.actor_user_id,
            scope=existing.scope,
            key=existing.key,
            request_hash="canonical-hash",
            compatible_request_hashes=("legacy-hash",),
            expires_at=_expires_at(),
        )
    )

    assert decision.status == "replay"
    assert decision.record is existing


def test_idempotency_service_stores_canonical_hash_for_new_compatible_request() -> None:
    repository = FakeAuditRepository()
    service = IdempotencyService(repository=repository)

    asyncio.run(
        service.reserve(
            actor_user_id=uuid4(),
            scope="agent.runs.create",
            key="idem-runtime-contract",
            request_hash="canonical-hash",
            compatible_request_hashes=("legacy-hash",),
            expires_at=_expires_at(),
        )
    )

    assert repository.created_idempotency.request_hash == "canonical-hash"


def test_idempotency_service_rejects_expired_key_before_replay() -> None:
    existing = _idempotency_key(request_hash_value="hash-1", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    service = IdempotencyService(repository=FakeAuditRepository(existing_idempotency=existing))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.reserve(
                actor_user_id=existing.actor_user_id,
                scope=existing.scope,
                key=existing.key,
                request_hash="hash-1",
                expires_at=_expires_at(),
            )
        )

    assert exc_info.value.code == "idempotency_key_expired"


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


def test_idempotency_service_rereads_winner_after_concurrent_first_insert() -> None:
    winner = _idempotency_key(request_hash_value="hash-1")
    repository = ConcurrentInsertAuditRepository(winner=winner)
    service = IdempotencyService(repository=repository)

    decision = asyncio.run(
        service.reserve(
            actor_user_id=winner.actor_user_id,
            scope=winner.scope,
            key=winner.key,
            request_hash="hash-1",
            expires_at=_expires_at(),
        )
    )

    assert decision.status == "replay"
    assert decision.record is winner
    assert repository.lookup_count == 2


def test_idempotency_service_rejects_conflicting_concurrent_first_insert_winner() -> None:
    winner = _idempotency_key(request_hash_value="winner-hash")
    service = IdempotencyService(repository=ConcurrentInsertAuditRepository(winner=winner))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.reserve(
                actor_user_id=winner.actor_user_id,
                scope=winner.scope,
                key=winner.key,
                request_hash="loser-hash",
                expires_at=_expires_at(),
            )
        )

    assert exc_info.value.code == "idempotency_conflict"


def test_request_hash_is_stable_for_equivalent_dicts() -> None:
    assert request_hash({"b": 2, "a": 1}) == request_hash({"a": 1, "b": 2})


def test_parse_idempotency_response_ref_returns_uuid() -> None:
    value = uuid4()

    assert parse_idempotency_response_ref(str(value)) == value


@pytest.mark.parametrize(
    ("response_ref", "code"),
    [
        ("", "idempotency_in_progress"),
        ("not-a-uuid", "conflict"),
    ],
)
def test_parse_idempotency_response_ref_rejects_invalid_values(response_ref: str, code: str) -> None:
    with pytest.raises(ApiError) as exc_info:
        parse_idempotency_response_ref(response_ref)

    assert exc_info.value.code == code


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _idempotency_key(*, request_hash_value: str, expires_at: datetime | None = None) -> IdempotencyKey:
    return IdempotencyKey(
        actor_user_id=uuid4(),
        scope="files.upload",
        key="idem-1",
        request_hash=request_hash_value,
        expires_at=expires_at or _expires_at(),
    )


class FakeAuditRepository:
    def __init__(self, *, existing_idempotency=None) -> None:
        self.existing_idempotency = existing_idempotency
        self.created_idempotency = None
        self.deleted_idempotency = None
        self.audit_kwargs = {}
        self.lookup_kwargs = {}

    async def record_audit(self, **kwargs):
        self.audit_kwargs = kwargs
        return None

    async def get_idempotency_key(self, **kwargs):
        self.lookup_kwargs = kwargs
        return self.existing_idempotency

    async def create_idempotency_key(self, **kwargs):
        self.created_idempotency = IdempotencyKey(**kwargs)
        return self.created_idempotency

    async def mark_idempotency_completed(self, *, idempotency_key, response_ref: str):
        idempotency_key.status = "completed"
        idempotency_key.response_ref = response_ref
        return idempotency_key

    async def delete_idempotency_key(self, *, idempotency_key):
        self.deleted_idempotency = idempotency_key


class ConcurrentInsertAuditRepository(FakeAuditRepository):
    def __init__(self, *, winner: IdempotencyKey) -> None:
        super().__init__()
        self.winner = winner
        self.lookup_count = 0

    async def get_idempotency_key(self, **kwargs):
        self.lookup_kwargs = kwargs
        self.lookup_count += 1
        return None if self.lookup_count == 1 else self.winner

    async def create_idempotency_key(self, **kwargs):
        self.created_idempotency = IdempotencyKey(**kwargs)
        return None
