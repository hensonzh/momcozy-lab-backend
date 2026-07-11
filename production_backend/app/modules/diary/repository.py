from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PregnancyDiaryEntry


_ENTRY_DEFAULTS: dict[str, Any] = {
    "gestational_week": "",
    "mood": "",
    "energy_level": "",
    "sleep_summary": "",
    "fetal_movement": "",
    "symptom_tags": [],
    "appointment_note": "",
    "nutrition_note": "",
    "content": "",
    "attachments": [],
}


@dataclass(frozen=True)
class DiaryEntryMutation:
    entry: PregnancyDiaryEntry
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
    ) -> PregnancyDiaryEntry | None:
        conditions = [
            PregnancyDiaryEntry.owner_user_id == owner_user_id,
            PregnancyDiaryEntry.entry_date == entry_date,
        ]
        if not include_deleted:
            conditions.append(PregnancyDiaryEntry.deleted_at.is_(None))
        statement = select(PregnancyDiaryEntry).where(*conditions)
        if for_update:
            statement = statement.with_for_update()
        return cast(PregnancyDiaryEntry | None, await self.session.scalar(statement))

    async def list_entries(
        self,
        *,
        owner_user_id: UUID,
        start_date: date | None,
        end_date: date | None,
        limit: int,
    ) -> list[PregnancyDiaryEntry]:
        conditions = [
            PregnancyDiaryEntry.owner_user_id == owner_user_id,
            PregnancyDiaryEntry.status == "active",
            PregnancyDiaryEntry.deleted_at.is_(None),
        ]
        if start_date is not None:
            conditions.append(PregnancyDiaryEntry.entry_date >= start_date)
        if end_date is not None:
            conditions.append(PregnancyDiaryEntry.entry_date <= end_date)
        statement = select(PregnancyDiaryEntry).where(*conditions).order_by(PregnancyDiaryEntry.entry_date.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def create_entry(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
    ) -> PregnancyDiaryEntry | None:
        try:
            async with self.session.begin_nested():
                entry = await self.get_entry_by_date(
                    owner_user_id=owner_user_id,
                    entry_date=entry_date,
                    include_deleted=True,
                    for_update=True,
                )
                if entry is None:
                    entry = PregnancyDiaryEntry(owner_user_id=owner_user_id, entry_date=entry_date)
                    self.session.add(entry)
                elif entry.deleted_at is None:
                    return None
                else:
                    for field, value in _ENTRY_DEFAULTS.items():
                        setattr(entry, field, list(value) if isinstance(value, list) else value)
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
        content_mode: str = "replace",
    ) -> PregnancyDiaryEntry | None:
        mutation = await self.update_entry_with_status(
            owner_user_id=owner_user_id,
            entry_date=entry_date,
            values=values,
            content_mode=content_mode,
        )
        return mutation.entry if mutation is not None else None

    async def update_entry_with_status(
        self,
        *,
        owner_user_id: UUID,
        entry_date: date,
        values: dict[str, Any],
        content_mode: str = "replace",
    ) -> DiaryEntryMutation | None:
        entry = await self.get_entry_by_date(owner_user_id=owner_user_id, entry_date=entry_date, for_update=True)
        if entry is None:
            return None
        resolved_values = dict(values)
        if content_mode == "append" and "content" in resolved_values:
            resolved_values["content"] = _append_content(entry.content, str(resolved_values["content"]))
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
    ) -> PregnancyDiaryEntry | None:
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


def _append_content(existing: str, addition: str) -> str:
    current = str(existing or "").rstrip()
    added = str(addition or "").strip()
    if not current:
        return added
    if not added or current == added or current.endswith(f"\n{added}"):
        return current
    return f"{current}\n{added}"
