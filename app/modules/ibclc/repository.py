from __future__ import annotations

from datetime import date, datetime
from typing import Literal, NamedTuple
from uuid import UUID

from sqlalchemy import String, case, cast as sql_cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..appointments.models import CareAppointment
from ..care.models import CareEpisode, CareProvider
from ..consultations.models import CareConsentRevision
from ..consultations.room_models import CareConsultation
from ..documentation.models import CarePlanDraft, ClinicalNote
from ..profiles.models import MaternalProfile, UserProfile
from ..users.models import AuthIdentity


class CaseProjection(NamedTuple):
    episode: CareEpisode
    granted: bool
    name: str | None
    delivery_date: date | None


class AppointmentProjection(NamedTuple):
    appointment: CareAppointment
    case: CaseProjection
    consultation: CareConsultation | None
    note_status: Literal["draft", "signed"] | None
    published_revision: int


def case_granted() -> ColumnElement[bool]:
    return func.coalesce(select(CareConsentRevision.active).where(CareConsentRevision.episode_id == CareEpisode.id,
        CareConsentRevision.scope == "ibclc_case").order_by(CareConsentRevision.version.desc()).limit(1).correlate(CareEpisode).scalar_subquery(), False)


class WorkbenchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def provider(self, provider_id: UUID) -> CareProvider | None:
        return await self.session.get(CareProvider, provider_id)

    async def email(self, provider_id: UUID) -> str:
        return str(await self.session.scalar(select(AuthIdentity.email).where(AuthIdentity.user_id == provider_id, AuthIdentity.provider == "email")) or "")

    async def _lock_cases(self, provider_id: UUID, episode_ids: list[UUID]) -> None:
        if episode_ids:
            await self.session.execute(select(CareEpisode.id).where(CareEpisode.assigned_ibclc_id == provider_id, CareEpisode.id.in_(episode_ids))
                .order_by(CareEpisode.id).with_for_update(read=True))

    async def appointments(self, provider_id: UUID, *, start: datetime | None = None, end: datetime | None = None,
        owner: UUID | None = None, offset: int = 0, limit: int = 50, include_cancelled: bool = True, overlap: bool = False) -> tuple[list[AppointmentProjection], int]:
        statuses = ["confirmed", "in_progress", "completed"] + (["cancelled"] if include_cancelled else [])
        scope = select(CareAppointment.id, CareEpisode.id.label("episode_id")).join(CareEpisode, CareEpisode.id == CareAppointment.episode_id).where(
            CareAppointment.provider_id == provider_id, CareEpisode.assigned_ibclc_id == provider_id,
            CareAppointment.status.in_(statuses))
        if start is not None:
            scope = scope.where(CareAppointment.ends_at > start if overlap else CareAppointment.starts_at >= start)
        if end is not None:
            scope = scope.where(CareAppointment.starts_at < end)
        if owner is not None:
            scope = scope.where(CareAppointment.owner_user_id == owner)
        total = int(await self.session.scalar(select(func.count()).select_from(scope.subquery())) or 0)
        selected = list(await self.session.execute(scope.order_by(CareAppointment.starts_at, CareAppointment.id).offset(offset).limit(limit)))
        await self._lock_cases(provider_id, [row.episode_id for row in selected])
        if not selected:
            return [], total
        granted = case_granted()
        note = select(ClinicalNote.status).where(ClinicalNote.consultation_id == CareConsultation.id).order_by(ClinicalNote.revision.desc()).limit(1).correlate(CareConsultation).scalar_subquery()
        statement = select(CareAppointment, CareEpisode, granted, case((granted, UserProfile.preferred_name), else_=None),
            case((granted, MaternalProfile.latest_delivery_date), else_=None), CareConsultation,
            case((granted, note), else_=None), case((granted, func.coalesce(CarePlanDraft.published_revision, 0)), else_=0))
        statement = statement.join(CareEpisode, CareEpisode.id == CareAppointment.episode_id).outerjoin(UserProfile, UserProfile.user_id == CareEpisode.owner_user_id)
        statement = statement.outerjoin(MaternalProfile, MaternalProfile.owner_user_id == CareEpisode.owner_user_id)
        statement = statement.outerjoin(CareConsultation, CareConsultation.appointment_id == CareAppointment.id).outerjoin(CarePlanDraft, CarePlanDraft.consultation_id == CareConsultation.id)
        rows = await self.session.execute(statement.where(CareAppointment.id.in_([row.id for row in selected]), CareAppointment.provider_id == provider_id,
            CareEpisode.assigned_ibclc_id == provider_id, CareAppointment.status.in_(statuses)).order_by(CareAppointment.starts_at, CareAppointment.id).execution_options(populate_existing=True))
        return [AppointmentProjection(row[0], CaseProjection(row[1], row[2], row[3], row[4]), row[5], row[6], row[7]) for row in rows], total

    async def clients(self, provider_id: UUID, *, query: str, status: str, offset: int, limit: int) -> tuple[list[UUID], int]:
        granted = case_granted()
        scope = select(CareEpisode.owner_user_id).outerjoin(UserProfile, UserProfile.user_id == CareEpisode.owner_user_id).where(CareEpisode.assigned_ibclc_id == provider_id)
        if status == "active":
            scope = scope.where(CareEpisode.status.in_(["active", "paused", "provisioning_pending"]))
        elif status == "completed":
            scope = scope.where(CareEpisode.status.in_(["completed", "cancelled"]))
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            scope = scope.where(or_((granted.is_(True) & UserProfile.preferred_name.ilike(f"%{escaped}%", escape="\\")),
                sql_cast(CareEpisode.owner_user_id, String).ilike(f"{escaped}%", escape="\\")))
        scope = scope.distinct()
        total = int(await self.session.scalar(select(func.count()).select_from(scope.subquery())) or 0)
        owners = list(await self.session.scalars(scope.order_by(CareEpisode.owner_user_id).offset(offset).limit(limit)))
        return owners, total

    async def cases(self, provider_id: UUID, owners: list[UUID]) -> list[CaseProjection]:
        if not owners:
            return []
        ids = list(await self.session.scalars(select(CareEpisode.id).where(CareEpisode.assigned_ibclc_id == provider_id, CareEpisode.owner_user_id.in_(owners))))
        await self._lock_cases(provider_id, ids)
        granted = case_granted()
        statement = select(CareEpisode, granted, case((granted, UserProfile.preferred_name), else_=None),
            case((granted, MaternalProfile.latest_delivery_date), else_=None)).outerjoin(UserProfile, UserProfile.user_id == CareEpisode.owner_user_id)
        rows = await self.session.execute(statement.outerjoin(MaternalProfile, MaternalProfile.owner_user_id == CareEpisode.owner_user_id).where(
            CareEpisode.id.in_(ids), CareEpisode.assigned_ibclc_id == provider_id).order_by(CareEpisode.created_at.desc(), CareEpisode.id).execution_options(populate_existing=True))
        return [CaseProjection(*row) for row in rows]
