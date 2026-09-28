from __future__ import annotations

from ..baby.profile_models import BabyProfile

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, Float, and_, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..users.models import User
from .models import BabyRecord
from .schemas import RecordKind


class BabyRecordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def profile_count(self, *, owner_user_id: UUID) -> int:
        return int(await self.session.scalar(select(func.count()).select_from(BabyProfile).join(User, User.id == BabyProfile.owner_user_id).where(
            BabyProfile.owner_user_id == owner_user_id, BabyProfile.deleted_at.is_(None), User.deleted_at.is_(None), User.status == 'active')) or 0)

    async def baby(self, owner: UUID, baby_id: UUID, *, write: bool = False) -> BabyProfile:
        if write:
            await self.session.scalar(select(User.id).where(User.id == owner).with_for_update())
        value = await self.session.scalar(select(BabyProfile).join(User, User.id == BabyProfile.owner_user_id).where(
            BabyProfile.id == baby_id, BabyProfile.owner_user_id == owner, BabyProfile.deleted_at.is_(None),
            User.status == 'active', User.deleted_at.is_(None)).with_for_update(of=BabyProfile, read=not write).execution_options(populate_existing=True))
        if value is None:
            raise ApiError(code='not_found', message='Baby not found.', status=404)
        return value

    async def get(self, owner: UUID, baby_id: UUID, record_id: UUID) -> BabyRecord:
        value = await self.session.scalar(select(BabyRecord).where(BabyRecord.id == record_id, BabyRecord.owner_user_id == owner,
            BabyRecord.baby_id == baby_id).with_for_update().execution_options(populate_existing=True))
        if value is None:
            raise ApiError(code='not_found', message='Baby record not found.', status=404)
        return value

    async def daily_status(self, owner: UUID, baby_id: UUID, day: date) -> BabyRecord | None:
        value: BabyRecord | None = await self.session.scalar(select(BabyRecord).where(
            BabyRecord.owner_user_id == owner, BabyRecord.baby_id == baby_id,
            BabyRecord.kind == 'daily_status', BabyRecord.recorded_on == day,
            BabyRecord.deleted_at.is_(None),
        ).with_for_update().execution_options(populate_existing=True))
        return value

    async def active_sleep(self, baby_id: UUID, exclude: UUID | None = None) -> bool:
        query = select(BabyRecord.id).where(BabyRecord.baby_id == baby_id, BabyRecord.kind == 'sleep', BabyRecord.deleted_at.is_(None), BabyRecord.ended_at.is_(None))
        if exclude is not None:
            query = query.where(BabyRecord.id != exclude)
        return await self.session.scalar(query.limit(1)) is not None

    async def list_latest_growth_by_infant_ids(
        self, *, owner_user_id: UUID, infant_ids: list[UUID], as_of_date: date | None = None,
    ) -> dict[UUID, BabyRecord]:
        if not infant_ids:
            return {}
        metric = BabyRecord.data["metric"].astext
        numeric_value = case(
            (func.jsonb_typeof(BabyRecord.data["value"]) == "number", cast(BabyRecord.data["value"].astext, Float)),
            else_=None,
        )
        conditions = [
            BabyRecord.owner_user_id == owner_user_id,
            BabyRecord.baby_id.in_(infant_ids),
            BabyRecord.kind == "growth",
            BabyRecord.deleted_at.is_(None),
            metric.in_(("weight", "length", "head_circumference")),
            numeric_value > 0,
            numeric_value <= 150,
            or_(metric != "weight", numeric_value <= 50),
            BabyProfile.owner_user_id == owner_user_id,
            BabyProfile.deleted_at.is_(None),
        ]
        if as_of_date is not None:
            conditions.append(BabyRecord.recorded_on <= as_of_date)
        ranked = select(
            BabyRecord.id,
            func.row_number().over(
                partition_by=BabyRecord.baby_id,
                order_by=(BabyRecord.recorded_on.desc(), BabyRecord.updated_at.desc(), BabyRecord.id.desc()),
            ).label("record_rank"),
        ).join(BabyProfile, BabyProfile.id == BabyRecord.baby_id).where(*conditions).subquery()
        rows = await self.session.scalars(
            select(BabyRecord).join(ranked, ranked.c.id == BabyRecord.id).where(ranked.c.record_rank == 1)
        )
        return {record.baby_id: record for record in rows}

    async def latest_growth(self, owner: UUID, baby_id: UUID) -> list[BabyRecord]:
        ranked = select(BabyRecord.id, func.row_number().over(
            partition_by=BabyRecord.data['metric'].astext,
            order_by=(BabyRecord.recorded_on.desc(), BabyRecord.updated_at.desc(), BabyRecord.id),
        ).label('rank')).where(BabyRecord.owner_user_id == owner, BabyRecord.baby_id == baby_id,
            BabyRecord.kind == 'growth', BabyRecord.deleted_at.is_(None)).subquery()
        return list(await self.session.scalars(select(BabyRecord).join(ranked, ranked.c.id == BabyRecord.id).where(ranked.c.rank == 1).order_by(BabyRecord.data['metric'].astext)))

    async def list(self, owner: UUID, baby_id: UUID, start: datetime, end: datetime, start_date: date, end_date: date, timezone: str,
        *, as_of: datetime, kind: RecordKind | None, offset: int, limit: int) -> tuple[list[BabyRecord], int]:
        query = select(BabyRecord).where(BabyRecord.owner_user_id == owner, BabyRecord.baby_id == baby_id, BabyRecord.deleted_at.is_(None),
            or_(and_(BabyRecord.recorded_on >= start_date, BabyRecord.recorded_on < end_date),
                and_(BabyRecord.occurred_at < end, or_(BabyRecord.occurred_at >= start,
                    and_(BabyRecord.kind == 'sleep', or_(and_(BabyRecord.ended_at.is_(None), literal(start < as_of)), BabyRecord.ended_at > start))))))
        if kind is not None:
            query = query.where(BabyRecord.kind == kind)
        total = int(await self.session.scalar(select(func.count()).select_from(query.subquery())) or 0)
        day = func.coalesce(BabyRecord.recorded_on, cast(func.timezone(timezone, BabyRecord.occurred_at), Date))
        values = list(await self.session.scalars(query.order_by(day.desc(), BabyRecord.occurred_at.desc().nulls_last(), BabyRecord.created_at.desc(), BabyRecord.id).offset(offset).limit(limit)))
        return values, total
