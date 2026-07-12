from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..profiles.models import InfantProfile
from ..plans.models import PlanTask
from .models import FeedingRecord, GrowthRecord, PumpingRecord


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

    async def plan_task_belongs_to_owner(self, *, plan_task_id: UUID, owner_user_id: UUID) -> bool:
        statement = select(PlanTask.id).where(
            PlanTask.id == plan_task_id,
            PlanTask.owner_user_id == owner_user_id,
            PlanTask.deleted_at.is_(None),
        )
        return await self.session.scalar(statement) is not None

    async def complete_plan_task(self, *, plan_task_id: UUID, owner_user_id: UUID) -> bool:
        statement = select(PlanTask).where(
            PlanTask.id == plan_task_id,
            PlanTask.owner_user_id == owner_user_id,
            PlanTask.deleted_at.is_(None),
        ).with_for_update()
        task = await self.session.scalar(statement)
        if task is None:
            return False
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return True

    async def create_feeding(
        self,
        *,
        owner_user_id: UUID,
        plan_task_id: UUID | None,
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
            plan_task_id=plan_task_id,
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
        return cast(FeedingRecord | None, await self.session.scalar(statement))

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
            conditions.append(FeedingRecord.feed_time < end_at)
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

    async def create_pumping(
        self,
        *,
        owner_user_id: UUID,
        plan_task_id: UUID | None,
        pump_start_time: datetime,
        pump_end_time: datetime | None,
        milk_volume_ml: float | None,
        pump_type: str,
        duration_seconds: int | None,
        source: str,
        title: str,
    ) -> PumpingRecord:
        record = PumpingRecord(
            owner_user_id=owner_user_id,
            plan_task_id=plan_task_id,
            pump_start_time=pump_start_time,
            pump_end_time=pump_end_time,
            milk_volume_ml=milk_volume_ml,
            pump_type=pump_type,
            duration_seconds=duration_seconds,
            source=source,
            title=title,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_pumping_for_owner(self, *, record_id: UUID, owner_user_id: UUID) -> PumpingRecord | None:
        statement = select(PumpingRecord).where(
            PumpingRecord.id == record_id,
            PumpingRecord.owner_user_id == owner_user_id,
            PumpingRecord.deleted_at.is_(None),
        )
        return cast(PumpingRecord | None, await self.session.scalar(statement))

    async def list_pumpings(
        self,
        *,
        owner_user_id: UUID,
        start_at: datetime | None,
        end_at: datetime | None,
        limit: int,
    ) -> list[PumpingRecord]:
        conditions = [
            PumpingRecord.owner_user_id == owner_user_id,
            PumpingRecord.status == "active",
            PumpingRecord.deleted_at.is_(None),
        ]
        if start_at is not None:
            conditions.append(PumpingRecord.pump_start_time >= start_at)
        if end_at is not None:
            conditions.append(PumpingRecord.pump_start_time < end_at)
        statement = select(PumpingRecord).where(*conditions).order_by(PumpingRecord.pump_start_time.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def soft_delete_pumping(
        self,
        *,
        record_id: UUID,
        owner_user_id: UUID,
        deleted_at: datetime,
    ) -> PumpingRecord | None:
        record = await self.get_pumping_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            return None
        record.status = "deleted"
        record.deleted_at = deleted_at
        await self.session.flush()
        return record

    async def create_growth(
        self,
        *,
        owner_user_id: UUID,
        infant_id: UUID | None,
        measured_at: datetime,
        height_cm: float | None,
        weight_kg: float | None,
        head_cm: float | None,
    ) -> GrowthRecord:
        record = GrowthRecord(
            owner_user_id=owner_user_id,
            infant_id=infant_id,
            measured_at=measured_at,
            height_cm=height_cm,
            weight_kg=weight_kg,
            head_cm=head_cm,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_growth_for_owner(self, *, record_id: UUID, owner_user_id: UUID) -> GrowthRecord | None:
        statement = select(GrowthRecord).where(
            GrowthRecord.id == record_id,
            GrowthRecord.owner_user_id == owner_user_id,
            GrowthRecord.deleted_at.is_(None),
        )
        return cast(GrowthRecord | None, await self.session.scalar(statement))

    async def update_growth(
        self,
        *,
        record_id: UUID,
        owner_user_id: UUID,
        updates: dict[str, Any],
    ) -> GrowthRecord | None:
        record = await self.get_growth_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            return None
        for field, value in updates.items():
            setattr(record, field, value)
        await self.session.flush()
        return record

    async def list_growth(
        self,
        *,
        owner_user_id: UUID,
        infant_id: UUID | None,
        limit: int,
    ) -> list[GrowthRecord]:
        conditions = [
            GrowthRecord.owner_user_id == owner_user_id,
            GrowthRecord.status == "active",
            GrowthRecord.deleted_at.is_(None),
        ]
        if infant_id is not None:
            conditions.append(GrowthRecord.infant_id == infant_id)
        statement = select(GrowthRecord).where(*conditions).order_by(GrowthRecord.measured_at.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def soft_delete_growth(
        self,
        *,
        record_id: UUID,
        owner_user_id: UUID,
        deleted_at: datetime,
    ) -> GrowthRecord | None:
        record = await self.get_growth_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            return None
        record.status = "deleted"
        record.deleted_at = deleted_at
        await self.session.flush()
        return record
