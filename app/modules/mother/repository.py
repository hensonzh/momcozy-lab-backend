from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import MotherDiaryEntry


class MotherDiaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, owner_user_id: UUID, entry_date: date) -> MotherDiaryEntry | None:
        return cast(MotherDiaryEntry | None, await self.session.scalar(select(MotherDiaryEntry).where(
            MotherDiaryEntry.owner_user_id == owner_user_id, MotherDiaryEntry.entry_date == entry_date)))

    async def list(self, owner_user_id: UUID, start: date, end: date) -> list[MotherDiaryEntry]:
        result = await self.session.scalars(select(MotherDiaryEntry).where(
            MotherDiaryEntry.owner_user_id == owner_user_id,
            MotherDiaryEntry.entry_date >= start, MotherDiaryEntry.entry_date <= end,
        ).order_by(MotherDiaryEntry.entry_date.desc()))
        return list(result)

    async def save(self, owner_user_id: UUID, entry_date: date, diary: dict[str, Any], expected_version: int) -> MotherDiaryEntry | None:
        if expected_version == 0:
            try:
                async with self.session.begin_nested():
                    entry = MotherDiaryEntry(owner_user_id=owner_user_id, entry_date=entry_date, diary=diary, version=1)
                    self.session.add(entry)
                    await self.session.flush()
                    await self.session.refresh(entry)
                return entry
            except IntegrityError:
                return None
        # Compare-and-swap is enforced in SQL, including concurrent writers.
        return cast(MotherDiaryEntry | None, await self.session.scalar(update(MotherDiaryEntry).where(
            MotherDiaryEntry.owner_user_id == owner_user_id,
            MotherDiaryEntry.entry_date == entry_date,
            MotherDiaryEntry.version == expected_version,
        ).values(diary=diary, version=expected_version + 1, updated_at=datetime.now(timezone.utc))
          .returning(MotherDiaryEntry).execution_options(populate_existing=True)))
