from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..plans.models import PlanTask


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def personal(self, owner: UUID, start: date, end: date, limit: int) -> list[PlanTask]:
        return list(await self.session.scalars(select(PlanTask).where(
            PlanTask.owner_user_id == owner,
            PlanTask.deleted_at.is_(None),
            PlanTask.plan_id.is_(None),
            PlanTask.payload["schedule_kind"].astext == "personal",
            PlanTask.task_date >= start,
            PlanTask.task_date < end,
        ).order_by(PlanTask.task_date, PlanTask.task_time, PlanTask.id).limit(limit)))

    async def personal_for_update(self, owner: UUID, task_id: UUID) -> PlanTask | None:
        return cast(PlanTask | None, await self.session.scalar(select(PlanTask).where(
            PlanTask.id == task_id, PlanTask.owner_user_id == owner,
            PlanTask.plan_id.is_(None),
            PlanTask.payload["schedule_kind"].astext == "personal",
        ).with_for_update().execution_options(populate_existing=True)))

    async def flush(self) -> None:
        await self.session.flush()
