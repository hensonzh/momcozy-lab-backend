from __future__ import annotations

from datetime import date, datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Select, case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..appointments.models import CareAppointment
from ..care.event_models import CareServiceEvent, CareServiceEventRead, WORK_REMINDER_KINDS
from ..care.models import CareEpisode
from ..profiles.models import UserProfile
from ..reports.models import CareReport
from .repository import case_granted


class ReminderProjection(NamedTuple):
    event: CareServiceEvent
    read_at: datetime | None
    patient_ref: UUID
    patient_name: str | None
    package_id: str
    starts_at: datetime | None
    timezone: str | None
    report_date: date | None


class WorkbenchRemindersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _scope(provider_id: UUID) -> Select[tuple[UUID, UUID, datetime]]:
        return select(CareServiceEvent.id, CareServiceEvent.episode_id, CareServiceEventRead.read_at).join(
            CareEpisode, CareEpisode.id == CareServiceEvent.episode_id).outerjoin(CareServiceEventRead,
            (CareServiceEventRead.event_id == CareServiceEvent.id) & (CareServiceEventRead.user_id == provider_id)).where(
            CareEpisode.assigned_ibclc_id == provider_id, CareServiceEvent.workbench_recipient_id == provider_id,
            CareServiceEvent.kind.in_(WORK_REMINDER_KINDS))

    async def _lock(self, provider_id: UUID, episodes: list[UUID]) -> None:
        if episodes:
            await self.session.execute(select(CareEpisode.id).where(CareEpisode.id.in_(episodes),
                CareEpisode.assigned_ibclc_id == provider_id).order_by(CareEpisode.id).with_for_update(read=True))

    async def list(self, provider_id: UUID, *, offset: int, limit: int) -> tuple[list[ReminderProjection], int, int]:
        scope = self._scope(provider_id)
        total = int(await self.session.scalar(select(func.count()).select_from(scope.subquery())) or 0)
        unread = int(await self.session.scalar(select(func.count()).select_from(scope.where(CareServiceEventRead.read_at.is_(None)).subquery())) or 0)
        rows = list(await self.session.execute(scope.order_by(CareServiceEventRead.read_at.is_not(None),
            CareServiceEvent.occurred_at.desc(), CareServiceEvent.id.desc()).offset(offset).limit(limit)))
        if not rows:
            return [], total, unread
        await self._lock(provider_id, [row.episode_id for row in rows])
        # Recheck both assignment and current consent after taking the case lock.
        permitted = self._scope(provider_id).with_only_columns(CareServiceEvent.id)
        statement = select(CareServiceEvent, CareServiceEventRead.read_at, CareEpisode.owner_user_id,
            case((case_granted(), UserProfile.preferred_name), else_=None), CareEpisode.package_id,
            CareAppointment.starts_at, func.coalesce(CareAppointment.timezone, CareReport.timezone), CareReport.report_date).join(CareEpisode, CareEpisode.id == CareServiceEvent.episode_id)
        statement = statement.outerjoin(CareServiceEventRead, (CareServiceEventRead.event_id == CareServiceEvent.id) & (CareServiceEventRead.user_id == provider_id))
        statement = statement.outerjoin(UserProfile, UserProfile.user_id == CareEpisode.owner_user_id).outerjoin(CareAppointment, CareAppointment.id == CareServiceEvent.appointment_id)
        statement = statement.outerjoin(CareReport, (CareReport.id == CareServiceEvent.aggregate_id) & (CareServiceEvent.kind == 'report_generated'))
        values = await self.session.execute(statement.where(CareServiceEvent.id.in_([row.id for row in rows]), CareServiceEvent.id.in_(permitted))
            .order_by(CareServiceEventRead.read_at.is_not(None), CareServiceEvent.occurred_at.desc(), CareServiceEvent.id.desc()).execution_options(populate_existing=True))
        return [ReminderProjection(*value) for value in values], total, unread

    async def mark_read(self, provider_id: UUID, event_id: UUID, read_at: datetime) -> bool:
        selected = (await self.session.execute(self._scope(provider_id).where(CareServiceEvent.id == event_id))).first()
        if selected is None:
            return False
        await self._lock(provider_id, [selected.episode_id])
        if await self.session.scalar(self._scope(provider_id).where(CareServiceEvent.id == event_id)) is None:
            return False
        await self.session.execute(insert(CareServiceEventRead).values(event_id=event_id, user_id=provider_id,
            read_at=read_at).on_conflict_do_nothing(index_elements=['event_id', 'user_id']))
        return True
