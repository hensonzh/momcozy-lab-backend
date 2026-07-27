from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DiaryEntry


_ENTRY_DEFAULTS: dict[str, Any] = {
    "content": "",
    "attributes": {},
    "attachments": [],
}


@dataclass(frozen=True)
class DiaryEntryMutation:
    entry: DiaryEntry
    changed: bool


class DiaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_entry_by_date(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        include_deleted: bool = False,
        for_update: bool = False,
    ) -> DiaryEntry | None:
        conditions = [
            DiaryEntry.owner_user_id == owner_user_id,
            DiaryEntry.entry_date == entry_date,
        ]
        if not include_deleted:
            conditions.append(DiaryEntry.deleted_at.is_(None))
        statement = select(DiaryEntry).where(*conditions)
        if for_update:
            statement = statement.with_for_update()
        return cast(DiaryEntry | None, await self.session.scalar(statement))

    async def list_entries(
        self,
        *,
        owner_user_id: UUID,
        start_date: date | None,
        end_date: date | None,
        limit: int,
    ) -> list[DiaryEntry]:
        conditions = [
            DiaryEntry.owner_user_id == owner_user_id,
            DiaryEntry.status == "active",
            DiaryEntry.deleted_at.is_(None),
        ]
        if start_date is not None:
            conditions.append(DiaryEntry.entry_date >= start_date)
        if end_date is not None:
            conditions.append(DiaryEntry.entry_date <= end_date)
        statement = select(DiaryEntry).where(*conditions).order_by(DiaryEntry.entry_date.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def create_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
    ) -> DiaryEntry | None:
        try:
            async with self.session.begin_nested():
                entry = await self.get_entry_by_date(
                    owner_user_id=owner_user_id,
                    entry_date=entry_date,
                    include_deleted=True,
                    for_update=True,
                )
                if entry is None:
                    entry = DiaryEntry(
                        owner_user_id=owner_user_id,
                        entry_date=entry_date,
                    )
                    self.session.add(entry)
                elif entry.deleted_at is None:
                    return None
                else:
                    for field, value in _ENTRY_DEFAULTS.items():
                        setattr(entry, field, _copy_default(value))
                entry.status = "active"
                entry.deleted_at = None
                for field, value in values.items():
                    setattr(entry, field, value)
                await self.session.flush()
                await self.session.refresh(entry, attribute_names=["updated_at"])
                return entry
        except IntegrityError:
            return None

    async def update_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
    ) -> DiaryEntry | None:
        mutation = await self.update_entry_with_status(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            values=values,
        )
        return mutation.entry if mutation is not None else None

    async def update_entry_with_status(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
    ) -> DiaryEntryMutation | None:
        entry = await self.get_entry_by_date(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            for_update=True,
        )
        if entry is None:
            return None
        resolved_values = _merge_attribute_updates(entry=entry, values=values)
        changed = any(getattr(entry, field) != value for field, value in resolved_values.items())
        if not changed:
            return DiaryEntryMutation(entry=entry, changed=False)
        for field, value in resolved_values.items():
            setattr(entry, field, value)
        await self.session.flush()
        await self.session.refresh(entry, attribute_names=["updated_at"])
        return DiaryEntryMutation(entry=entry, changed=True)

    async def soft_delete_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        deleted_at: datetime,
    ) -> DiaryEntry | None:
        entry = await self.get_entry_by_date(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            for_update=True,
        )
        if entry is None:
            return None
        entry.status = "deleted"
        entry.deleted_at = deleted_at
        await self.session.flush()
        await self.session.refresh(entry, attribute_names=["updated_at"])
        return entry


def _copy_default(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _merge_attribute_updates(*, entry: DiaryEntry, values: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(values)
    if "attributes" in resolved:
        attributes = dict(entry.attributes or {})
        attributes.update(cast(dict[str, Any], resolved["attributes"]))
        resolved["attributes"] = attributes
    return resolved
