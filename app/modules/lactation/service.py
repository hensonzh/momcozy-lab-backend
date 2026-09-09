from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from ...core.errors import ApiError
from ..audit.service import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import LactationRecord
from .repository import LactationRepository
from .schemas import LactationObservation, LactationUpdate


class LactationService:
    def __init__(self, repository: LactationRepository, audit: AuditService, idempotency: IdempotencyService) -> None:
        self.repository = repository
        self.audit = audit
        self.idempotency = idempotency

    async def list(self, owner: UUID, start: datetime, end: datetime) -> list[LactationRecord]:
        if start.tzinfo is None or end.tzinfo is None or end <= start or end - start > timedelta(days=367):
            raise ApiError(code="validation_failed", message="Choose an aware time range of at most 367 days.", status=422)
        return await self.repository.list(owner, start, end)

    async def create(self, owner: UUID, observation: LactationObservation, key: str, request_id: str) -> LactationRecord:
        decision = await self.idempotency.reserve(actor_user_id=owner, scope="lactation.create", key=key,
            request_hash=request_hash(observation.model_dump(mode="json")), expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        if decision.status == "replay":
            record = await self._get(owner, parse_idempotency_response_ref(decision.record.response_ref))
            if record.deleted_at is not None:
                raise ApiError(code="resource_deleted", message="This record was deleted.", status=409)
            return record
        record = await self.repository.create(owner, observation.model_dump())
        await self.idempotency.mark_completed(record=decision.record, response_ref=str(record.id))
        await self._audit(owner, record, "create", request_id)
        return record

    async def update(self, owner: UUID, record_id: UUID, body: LactationUpdate, request_id: str) -> LactationRecord:
        current = await self._get(owner, record_id)
        actual = current.observation
        # PostgreSQL returns aware UTC timestamps; normalize naive test-dialect values as UTC too.
        if actual["occurred_at"].tzinfo is None:
            actual["occurred_at"] = actual["occurred_at"].replace(tzinfo=timezone.utc)
        if current.deleted_at is None and current.version == body.expected_version + 1 and actual == body.observation.model_dump():
            return current
        # Switching method clears the other measurement instead of leaving a hidden old value.
        values = {"volume_ml": None, "duration_minutes": None, **body.observation.model_dump()}
        record = await self.repository.update(owner, record_id, body.expected_version, values)
        if record is None:
            raise _conflict()
        await self._audit(owner, record, "update", request_id)
        return record

    async def set_deleted(self, owner: UUID, record_id: UUID, version: int, deleted: bool, request_id: str) -> LactationRecord:
        current = await self._get(owner, record_id)
        if (current.deleted_at is not None) == deleted and current.version == version + 1:
            return current
        record = await self.repository.update(owner, record_id, version,
            {"deleted_at": datetime.now(timezone.utc) if deleted else None}, deleted=not deleted)
        if record is None:
            raise _conflict()
        await self._audit(owner, record, "delete" if deleted else "restore", request_id)
        return record

    async def _get(self, owner: UUID, record_id: UUID) -> LactationRecord:
        record = await self.repository.get(owner, record_id)
        if record is None:
            raise ApiError(code="not_found", message="Record not found.", status=404)
        return record

    async def _audit(self, owner: UUID, record: LactationRecord, action: str, request_id: str) -> None:
        await self.audit.record(actor_user_id=owner, action=f"lactation.{action}", resource_type="lactation_record",
            resource_id=str(record.id), request_id=request_id, details={"version": record.version})


def _conflict() -> ApiError:
    return ApiError(code="version_conflict", message="This record was changed. Reload before saving.", status=409)
