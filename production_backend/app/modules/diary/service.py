from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from ...core.errors import ApiError
from ..audit import AuditService
from .models import PregnancyDiaryEntry, PregnancyDiarySettings
from .repository import DiaryEntryMutation, DiaryRepository


class DiaryService:
    def __init__(self, *, repository: DiaryRepository, audit_service: AuditService | None = None) -> None:
        self.repository = repository
        self.audit_service = audit_service

    async def get_settings(self, *, owner_user_id: UUID) -> PregnancyDiarySettings:
        settings = await self.repository.get_settings(owner_user_id=owner_user_id)
        if settings is not None:
            return settings
        return PregnancyDiarySettings(owner_user_id=owner_user_id, auto_capture_enabled=False)

    async def update_settings(
        self,
        *,
        owner_user_id: UUID,
        auto_capture_enabled: bool,
        request_id: str = "",
    ) -> PregnancyDiarySettings:
        if not isinstance(auto_capture_enabled, bool):
            raise ApiError(code="validation_failed", message="auto_capture_enabled must be a boolean.", status=422)
        settings = await self.repository.upsert_settings(
            owner_user_id=owner_user_id,
            auto_capture_enabled=auto_capture_enabled,
        )
        await self._audit(
            owner_user_id=owner_user_id,
            action="pregnancy_diary.settings.update",
            resource_id=str(owner_user_id),
            request_id=request_id,
            resource_type="pregnancy_diary_settings",
        )
        return settings

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

    async def create_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
        request_id: str = "",
    ) -> PregnancyDiaryEntry:
        _require_values(values)
        try:
            entry = await self.repository.create_entry(owner_user_id=owner_user_id, entry_date=entry_date, values=values)
        except IntegrityError as exc:
            raise ApiError(code="conflict", message="Diary entry already exists for this date.", status=409) from exc
        if entry is None:
            raise ApiError(code="conflict", message="Diary entry already exists for this date.", status=409)
        await self._audit(
            owner_user_id=owner_user_id,
            action="pregnancy_diary.entry.create",
            resource_id=str(entry.id),
            request_id=request_id,
        )
        return entry

    async def update_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
        request_id: str = "",
    ) -> PregnancyDiaryEntry:
        mutation = await self.update_entry_with_status(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            values=values,
            request_id=request_id,
        )
        return mutation.entry

    async def update_entry_with_status(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
        request_id: str = "",
    ) -> DiaryEntryMutation:
        _require_values(values)
        update_with_status = getattr(self.repository, "update_entry_with_status", None)
        if callable(update_with_status):
            mutation = await update_with_status(
                owner_user_id=owner_user_id,
                entry_date=entry_date,
                values=values,
            )
        else:
            entry = await self.repository.update_entry(
                owner_user_id=owner_user_id,
                entry_date=entry_date,
                values=values,
            )
            mutation = DiaryEntryMutation(entry=entry, changed=True) if entry is not None else None
        if mutation is None:
            raise ApiError(code="not_found", message="Diary entry not found.", status=404)
        if mutation.changed:
            await self._audit(
                owner_user_id=owner_user_id,
                action="pregnancy_diary.entry.update",
                resource_id=str(mutation.entry.id),
                request_id=request_id,
            )
        return mutation

    async def delete_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        request_id: str = "",
    ) -> PregnancyDiaryEntry:
        deleted = await self.repository.soft_delete_entry(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            deleted_at=datetime.now(timezone.utc),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Diary entry not found.", status=404)
        await self._audit(
            owner_user_id=owner_user_id,
            action="pregnancy_diary.entry.delete",
            resource_id=str(deleted.id),
            request_id=request_id,
        )
        return deleted

    async def _audit(
        self,
        *,
        owner_user_id: UUID,
        action: str,
        resource_id: str,
        request_id: str,
        resource_type: str = "pregnancy_diary_entry",
    ) -> None:
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
            )


def _require_values(values: dict[str, Any]) -> None:
    if not values:
        raise ApiError(code="validation_failed", message="At least one diary field is required.", status=422)
