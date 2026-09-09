from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from ...core.errors import ApiError
from ..audit.service import AuditService
from .models import MotherDiaryEntry
from .repository import MotherDiaryRepository
from .schemas import MotherDiaryWrite


class MotherDiaryService:
    def __init__(self, repository: MotherDiaryRepository, audit: AuditService) -> None:
        self.repository = repository
        self.audit = audit

    async def list(self, owner_user_id: UUID, start: date, end: date) -> list[MotherDiaryEntry]:
        if end < start or (end - start).days > 366:
            raise ApiError(code="validation_failed", message="Choose a date range of at most 367 days.", status=422)
        return await self.repository.list(owner_user_id, start, end)

    async def save(self, owner_user_id: UUID, entry_date: date, payload: MotherDiaryWrite, request_id: str) -> MotherDiaryEntry:
        # A calendar diary follows the user's date; UTC+14 can already be tomorrow.
        if entry_date > (datetime.now(timezone.utc) + timedelta(hours=14)).date():
            raise ApiError(code="validation_failed", message="A diary date cannot be in the future.", status=422)
        values = payload.diary.model_dump(mode="json")
        existing = await self.repository.get(owner_user_id, entry_date)
        if existing is not None and existing.diary == values:
            # Retrying an acknowledged or timed-out identical save is idempotent.
            return existing
        saved = await self.repository.save(owner_user_id, entry_date, values, payload.expected_version)
        if saved is None:
            # A concurrently completed identical write is safe to replay as well.
            current = await self.repository.get(owner_user_id, entry_date)
            if current is not None and current.diary == values:
                return current
            raise ApiError(code="version_conflict", message="This diary was updated. Reload before saving.", status=409)
        await self.audit.record(actor_user_id=owner_user_id, action="mother.diary.save",
            resource_type="mother_diary", resource_id=str(saved.id), request_id=request_id,
            details={"version": saved.version})
        return saved
