from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..care.models import CareEpisode, CareOrder, CareProvider
from ..users.models import User
from .models import BookingEligibility, CareAppointment, ProviderAvailability, ProviderCalendarBlock


def active_at(now: datetime) -> Any:
    return or_(CareAppointment.status.in_(["confirmed", "in_progress"]),
        and_(CareAppointment.status == "held", CareAppointment.hold_expires_at > now))


class AppointmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_owner(self, owner: UUID) -> None:
        await self.session.scalar(select(User.id).where(User.id == owner).with_for_update())

    async def episode(self, owner: UUID, episode_id: UUID, *, lock: bool = False) -> CareEpisode | None:
        query = select(CareEpisode).where(CareEpisode.owner_user_id == owner, CareEpisode.id == episode_id)
        if lock:
            query = query.with_for_update()
        return cast(CareEpisode | None, await self.session.scalar(query.execution_options(populate_existing=True)))

    async def sandbox_episode(self, episode: CareEpisode) -> bool:
        return await self.session.scalar(select(CareOrder.payment_mode).where(CareOrder.id == episode.order_id)) == "sandbox"

    async def provider(self, provider_id: UUID, *, lock: bool = False) -> CareProvider | None:
        query = select(CareProvider).where(CareProvider.user_id == provider_id)
        if lock:
            query = query.with_for_update()
        return cast(CareProvider | None, await self.session.scalar(query.execution_options(populate_existing=True)))

    async def providers(self, *, sandbox: bool) -> list[CareProvider]:
        return list(await self.session.scalars(select(CareProvider).where(CareProvider.active.is_(True), CareProvider.sandbox.is_(sandbox)).order_by(CareProvider.display_name)))

    async def eligibility(self, owner: UUID, episode_id: UUID, check_id: UUID | None = None) -> BookingEligibility | None:
        query = select(BookingEligibility).where(BookingEligibility.owner_user_id == owner, BookingEligibility.episode_id == episode_id)
        if check_id is not None:
            query = query.where(BookingEligibility.id == check_id)
        return cast(BookingEligibility | None, await self.session.scalar(query.order_by(BookingEligibility.created_at.desc(), BookingEligibility.id).limit(1)))

    async def rules(self, provider_id: UUID) -> list[ProviderAvailability]:
        return list(await self.session.scalars(select(ProviderAvailability).where(ProviderAvailability.provider_id == provider_id)))

    async def blocks(self, provider_id: UUID, start: datetime, end: datetime) -> list[ProviderCalendarBlock]:
        return list(await self.session.scalars(select(ProviderCalendarBlock).where(ProviderCalendarBlock.provider_id == provider_id,
            ProviderCalendarBlock.starts_at < end, ProviderCalendarBlock.ends_at > start)))

    async def busy(self, owner: UUID, provider_id: UUID, start: datetime, end: datetime, now: datetime) -> list[CareAppointment]:
        return list(await self.session.scalars(select(CareAppointment).where(
            or_(CareAppointment.owner_user_id == owner, CareAppointment.provider_id == provider_id), active_at(now),
            CareAppointment.starts_at < end, CareAppointment.ends_at > start)))

    async def appointments(self, owner: UUID, episode_id: UUID) -> list[CareAppointment]:
        return list(await self.session.scalars(select(CareAppointment).where(CareAppointment.owner_user_id == owner,
            CareAppointment.episode_id == episode_id).order_by(CareAppointment.starts_at.desc())))

    async def appointment(self, owner: UUID, appointment_id: UUID, *, lock: bool = False) -> CareAppointment | None:
        query = select(CareAppointment).where(CareAppointment.owner_user_id == owner, CareAppointment.id == appointment_id)
        if lock:
            query = query.with_for_update()
        return cast(CareAppointment | None, await self.session.scalar(query.execution_options(populate_existing=True)))

    async def expire_holds(self, owner: UUID, now: datetime) -> None:
        await self.session.execute(update(CareAppointment).where(
            CareAppointment.owner_user_id == owner,
            CareAppointment.status == "held", CareAppointment.hold_expires_at <= now)
            .values(status="expired", version=CareAppointment.version + 1))

    async def add(self, value: Any) -> None:
        self.session.add(value)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()
