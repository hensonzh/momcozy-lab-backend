from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService
from .models import PregnancyDiaryEntry
from .repository import DiaryRepository


class DiaryService:
    def __init__(self, *, repository: DiaryRepository, audit_service: AuditService | None = None) -> None:
        self.repository = repository
        self.audit_service = audit_service

    async def get_entry(self, *, owner_user_id: UUID, entry_date: date) -> PregnancyDiaryEntry:
        entry = await self.repository.get_entry_by_date(owner_user_id=owner_user_id, entry_date=entry_date)
        if entry is None:
            raise ApiError(code="not_found", message="Diary entry not found.", status=404)
        return entry

    async def list_entries(
        self,
        *,
        owner_user_id: UUID,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 30,
    ) -> list[PregnancyDiaryEntry]:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_entries(
            owner_user_id=owner_user_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

    async def upsert_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
        request_id: str = "",
    ) -> PregnancyDiaryEntry:
        entry = await self.repository.upsert_entry(owner_user_id=owner_user_id, entry_date=entry_date, values=values)
        await self._audit(owner_user_id=owner_user_id, action="diary.entry.upsert", resource_id=str(entry.id), request_id=request_id)
        return entry

    async def delete_entry(self, *, owner_user_id: UUID, entry_date: date, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_entry(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            deleted_at=datetime.now(timezone.utc),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Diary entry not found.", status=404)
        await self._audit(owner_user_id=owner_user_id, action="diary.entry.delete", resource_id=str(deleted.id), request_id=request_id)

    async def _audit(self, *, owner_user_id: UUID, action: str, resource_id: str, request_id: str) -> None:
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action=action,
                resource_type="pregnancy_diary_entry",
                resource_id=resource_id,
                request_id=request_id,
            )
