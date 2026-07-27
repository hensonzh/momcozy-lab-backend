from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from ...core.errors import ApiError
from ..audit import AuditService
from .models import DiaryEntry
from .repository import DiaryEntryMutation, DiaryRepository


_ENTRY_VALUE_FIELDS = frozenset({"content", "attributes", "attachments"})


class DiaryService:
    def __init__(self, *, repository: DiaryRepository, audit_service: AuditService | None = None) -> None:
        self.repository = repository
        self.audit_service = audit_service

    async def get_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
    ) -> DiaryEntry:
        entry = await self.repository.get_entry_by_date(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
        )
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
    ) -> list[DiaryEntry]:
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
    ) -> DiaryEntry:
        _require_values(values)
        try:
            entry = await self.repository.create_entry(
                owner_user_id=owner_user_id,
                entry_date=entry_date,
                values=values,
            )
        except IntegrityError as exc:
            raise ApiError(
                code="conflict",
                message="Diary entry already exists for this date.",
                status=409,
            ) from exc
        if entry is None:
            raise ApiError(
                code="conflict",
                message="Diary entry already exists for this date.",
                status=409,
            )
        await self._audit(
            owner_user_id=owner_user_id,
            action="diary.entry.create",
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
    ) -> DiaryEntry:
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
        mutation: DiaryEntryMutation | None
        if callable(update_with_status):
            mutation = cast(
                DiaryEntryMutation | None,
                await update_with_status(
                    owner_user_id=owner_user_id,
                    entry_date=entry_date,
                    values=values,
                ),
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
                action="diary.entry.update",
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
    ) -> DiaryEntry:
        deleted = await self.repository.soft_delete_entry(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            deleted_at=datetime.now(timezone.utc),
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Diary entry not found.", status=404)
        await self._audit(
            owner_user_id=owner_user_id,
            action="diary.entry.delete",
            resource_id=str(deleted.id),
            request_id=request_id,
        )
        return deleted

    async def _audit(self, *, owner_user_id: UUID, action: str, resource_id: str, request_id: str) -> None:
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action=action,
                resource_type="diary_entry",
                resource_id=resource_id,
                request_id=request_id,
            )


def _require_values(values: dict[str, Any]) -> None:
    if not values:
        raise ApiError(code="validation_failed", message="At least one diary field is required.", status=422)
    unsupported = set(values) - _ENTRY_VALUE_FIELDS
    if unsupported:
        raise ApiError(
            code="validation_failed",
            message=f"Unsupported diary fields: {', '.join(sorted(unsupported))}.",
            status=422,
        )
    if "attributes" in values and not isinstance(values["attributes"], dict):
        raise ApiError(code="validation_failed", message="Diary attributes must be an object.", status=422)
    if "attachments" in values and not isinstance(values["attachments"], list):
        raise ApiError(code="validation_failed", message="Diary attachments must be a list.", status=422)
