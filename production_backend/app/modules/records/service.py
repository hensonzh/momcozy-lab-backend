from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, request_hash
from .models import FeedingRecord
from .repository import RecordsRepository


FEEDING_CREATE_IDEMPOTENCY_SCOPE = "records.feeding.create"


class RecordsService:
    def __init__(
        self,
        *,
        repository: RecordsRepository,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service

    async def create_feeding(
        self,
        *,
        owner_user_id: UUID,
        infant_id: UUID | None,
        feed_time: datetime,
        feed_type: str,
        feed_action: str = "",
        volume_ml: float | None = None,
        duration_seconds: int | None = None,
        title: str = "",
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> FeedingRecord:
        if infant_id is not None and not await self.repository.infant_belongs_to_owner(
            infant_id=infant_id,
            owner_user_id=owner_user_id,
        ):
            raise ApiError(code="owner_scope_violation", message="Infant profile is outside the current user scope.", status=403)
        if volume_ml is None and duration_seconds is None:
            raise ApiError(code="validation_failed", message="volume_ml or duration_seconds is required.", status=422)

        idempotency_record = None
        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=FEEDING_CREATE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "infant_id": str(infant_id or ""),
                        "feed_time": feed_time.isoformat(),
                        "feed_type": feed_type,
                        "feed_action": feed_action,
                        "volume_ml": volume_ml,
                        "duration_seconds": duration_seconds,
                        "title": title,
                    }
                ),
                expires_at=_utcnow() + timedelta(hours=24),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_feeding(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        record = await self.repository.create_feeding(
            owner_user_id=owner_user_id,
            infant_id=infant_id,
            feed_time=feed_time,
            feed_type=feed_type,
            feed_action=feed_action,
            volume_ml=volume_ml,
            duration_seconds=duration_seconds,
            title=title,
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(record.id))
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.feeding.create",
                resource_type="feeding_record",
                resource_id=str(record.id),
                request_id=request_id,
                details={"feed_type": feed_type},
            )
        return record

    async def list_feedings(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[FeedingRecord]:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_feedings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )

    async def delete_feeding(self, *, owner_user_id: UUID, record_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_feeding(
            owner_user_id=owner_user_id,
            record_id=record_id,
            deleted_at=_utcnow(),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Feeding record not found.", status=404)
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="records.feeding.delete",
                resource_type="feeding_record",
                resource_id=str(record_id),
                request_id=request_id,
            )

    async def _replay_feeding(self, *, owner_user_id: UUID, response_ref: str) -> FeedingRecord:
        if not response_ref:
            raise ApiError(code="idempotency_in_progress", message="Request is still in progress.", status=409)
        try:
            record_id = UUID(response_ref)
        except ValueError as exc:
            raise ApiError(code="conflict", message="Idempotency response reference is invalid.", status=409) from exc
        record = await self.repository.get_feeding_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return record


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
