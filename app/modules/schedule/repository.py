from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from ..appointments.models import CareAppointment
from ..care.models import CareEpisode
from ..documentation.models import CarePlanDraft, CarePlanPublication
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

    async def appointments(self, owner: UUID, start_at: datetime, end_at: datetime, limit: int) -> list[CareAppointment]:
        return list(await self.session.scalars(select(CareAppointment).where(
            CareAppointment.owner_user_id == owner,
            CareAppointment.starts_at < end_at,
            CareAppointment.ends_at > start_at,
            CareAppointment.status.not_in(["expired"]),
        ).order_by(CareAppointment.starts_at, CareAppointment.id).limit(limit)))

    async def episodes(self, owner: UUID, limit: int) -> list[CareEpisode]:
        return list(await self.session.scalars(select(CareEpisode).where(
            CareEpisode.owner_user_id == owner,
            CareEpisode.status.not_in(["cancelled"]),
        ).order_by(CareEpisode.created_at.desc(), CareEpisode.id).limit(limit)))

    async def published_rows(self, owner: UUID, limit: int) -> list[tuple[CarePlanPublication, UUID, UUID, dict[str, Any]]]:
        from ..consultations.room_models import CareConsultation
        previous = aliased(CarePlanPublication)
        statement = select(CarePlanPublication, CareAppointment.id, CareEpisode.id, CarePlanPublication.content).join(
            CarePlanDraft, CarePlanDraft.id == CarePlanPublication.plan_id,
        ).join(CareConsultation, CareConsultation.id == CarePlanDraft.consultation_id).join(
            CareAppointment, CareAppointment.id == CareConsultation.appointment_id,
        ).join(CareEpisode, CareEpisode.id == CareAppointment.episode_id).where(
            CareEpisode.owner_user_id == owner,
            CareAppointment.status == "completed",
            ~select(previous.id).where(
                previous.plan_id == CarePlanPublication.plan_id,
                previous.revision > CarePlanPublication.revision,
            ).exists(),
        ).order_by(CareAppointment.starts_at.desc(), CarePlanPublication.revision.desc()).limit(limit)
        return cast(list[tuple[CarePlanPublication, UUID, UUID, dict[str, Any]]], (await self.session.execute(statement)).all())

    async def flush(self) -> None:
        await self.session.flush()
