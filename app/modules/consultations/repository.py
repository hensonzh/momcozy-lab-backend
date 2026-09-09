from __future__ import annotations

from ..baby.profile_models import BabyProfile

from datetime import date
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..appointments.models import CareAppointment
from ..care.models import CareEpisode, CareProvider
from ..profiles.models import MaternalProfile
from .models import CareConsentRevision, CareIntakeRevision


class ConsultationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def intake(self, appointment_id: UUID) -> CareIntakeRevision | None:
        return cast(CareIntakeRevision | None, await self.session.scalar(select(CareIntakeRevision).where(
            CareIntakeRevision.appointment_id == appointment_id).order_by(CareIntakeRevision.version.desc()).limit(1)))

    async def previous_intake(self, episode_id: UUID, appointment_id: UUID) -> CareIntakeRevision | None:
        return cast(CareIntakeRevision | None, await self.session.scalar(select(CareIntakeRevision).where(
            CareIntakeRevision.episode_id == episode_id, CareIntakeRevision.appointment_id != appointment_id)
            .order_by(CareIntakeRevision.submitted_at.desc(), CareIntakeRevision.version.desc()).limit(1)))

    async def consent(self, episode_id: UUID, scope: str) -> CareConsentRevision | None:
        return cast(CareConsentRevision | None, await self.session.scalar(select(CareConsentRevision).where(
            CareConsentRevision.episode_id == episode_id, CareConsentRevision.scope == scope).order_by(CareConsentRevision.version.desc()).limit(1)))

    async def consents(self, episode_id: UUID) -> list[CareConsentRevision]:
        rows = await self.session.scalars(select(CareConsentRevision).where(CareConsentRevision.episode_id == episode_id)
            .order_by(CareConsentRevision.scope, CareConsentRevision.version.desc()))
        result: dict[str, CareConsentRevision] = {}
        for row in rows:
            result.setdefault(row.scope, row)
        return list(result.values())

    async def babies(self, owner: UUID) -> list[BabyProfile]:
        return list(await self.session.scalars(select(BabyProfile).where(BabyProfile.owner_user_id == owner,
            BabyProfile.deleted_at.is_(None)).order_by(BabyProfile.created_at, BabyProfile.id)))

    async def delivery_date(self, owner: UUID) -> date | None:
        return cast(date | None, await self.session.scalar(select(MaternalProfile.latest_delivery_date).where(MaternalProfile.owner_user_id == owner)))

    async def assigned_appointment(self, provider: UUID, appointment_id: UUID) -> CareAppointment | None:
        return cast(CareAppointment | None, await self.session.scalar(select(CareAppointment).join(CareEpisode, CareEpisode.id == CareAppointment.episode_id)
            .join(CareProvider, CareProvider.user_id == CareAppointment.provider_id).where(CareAppointment.id == appointment_id,
                CareAppointment.provider_id == provider, CareEpisode.assigned_ibclc_id == provider, CareProvider.active.is_(True))
            .with_for_update(read=True, of=CareEpisode)))

    async def add(self, value: Any) -> None:
        self.session.add(value)
        await self.session.flush()
