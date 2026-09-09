from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LactationRecord


class LactationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, owner: UUID, record_id: UUID) -> LactationRecord | None:
        return cast(LactationRecord | None, await self.session.scalar(select(LactationRecord).where(
            LactationRecord.id == record_id, LactationRecord.owner_user_id == owner).execution_options(populate_existing=True)))

    async def list(self, owner: UUID, start: datetime, end: datetime) -> list[LactationRecord]:
        result = await self.session.scalars(select(LactationRecord).where(LactationRecord.owner_user_id == owner,
            LactationRecord.occurred_at >= start, LactationRecord.occurred_at < end,
            LactationRecord.deleted_at.is_(None)).order_by(LactationRecord.occurred_at.desc(), LactationRecord.id))
        return list(result)

    async def create(self, owner: UUID, values: dict[str, Any]) -> LactationRecord:
        record = LactationRecord(owner_user_id=owner, version=1, **values)
        self.session.add(record)
        await self.session.flush()
        return record

    async def update(self, owner: UUID, record_id: UUID, version: int, values: dict[str, Any], *, deleted: bool = False) -> LactationRecord | None:
        return cast(LactationRecord | None, await self.session.scalar(update(LactationRecord).where(
            LactationRecord.owner_user_id == owner, LactationRecord.id == record_id,
            LactationRecord.version == version,
            LactationRecord.deleted_at.is_not(None) if deleted else LactationRecord.deleted_at.is_(None),
        ).values(**values, version=version + 1, updated_at=datetime.now(timezone.utc)).returning(LactationRecord)
          .execution_options(populate_existing=True)))
