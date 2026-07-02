from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..profiles.models import InfantProfile
from .models import FeedingRecord


class RecordsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def infant_belongs_to_owner(self, *, infant_id: UUID, owner_user_id: UUID) -> bool:
        statement = select(InfantProfile.id).where(
            InfantProfile.id == infant_id,
            InfantProfile.owner_user_id == owner_user_id,
            InfantProfile.deleted_at.is_(None),
        )
        return await self.session.scalar(statement) is not None

    async def create_feeding(
        self,
        *,
        owner_user_id: UUID,
        infant_id: UUID | None,
        feed_time: datetime,
        feed_type: str,
        feed_action: str,
        volume_ml: float | None,
        duration_seconds: int | None,
        title: str,
    ) -> FeedingRecord:
        record = FeedingRecord(
            owner_user_id=owner_user_id,
            infant_id=infant_id,
            feed_time=feed_time,
            feed_type=feed_type,
            feed_action=feed_action,
            volume_ml=volume_ml,
            duration_seconds=duration_seconds,
            title=title,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_feeding_for_owner(self, *, record_id: UUID, owner_user_id: UUID) -> FeedingRecord | None:
        statement = select(FeedingRecord).where(
            FeedingRecord.id == record_id,
            FeedingRecord.owner_user_id == owner_user_id,
            FeedingRecord.deleted_at.is_(None),
        )
        return await self.session.scalar(statement)

    async def list_feedings(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime | None,
        end_at: datetime | None,
        limit: int,
    ) -> list[FeedingRecord]:
        conditions = [
            FeedingRecord.owner_user_id == owner_user_id,
            FeedingRecord.status == "active",
            FeedingRecord.deleted_at.is_(None),
        ]
        if start_at is not None:
            conditions.append(FeedingRecord.feed_time >= start_at)
        if end_at is not None:
            conditions.append(FeedingRecord.feed_time <= end_at)
        statement = select(FeedingRecord).where(*conditions).order_by(FeedingRecord.feed_time.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def soft_delete_feeding(
        self,
        *,
        record_id: UUID,
        owner_user_id: UUID,
        deleted_at: datetime,
    ) -> FeedingRecord | None:
        record = await self.get_feeding_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            return None
        record.status = "deleted"
        record.deleted_at = deleted_at
        await self.session.flush()
        return record
