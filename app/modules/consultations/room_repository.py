from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..appointments.models import CareAppointment
from ..auth import CurrentUser
from ..care.models import CareEpisode, CareProvider
from .room_models import CareConsultation, CareLocationCheck, CareRoomParticipant, CareVideoCommand


class RoomRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def appointment_for_actor(self, actor: CurrentUser, appointment_id: UUID) -> CareAppointment | None:
        scope = CareAppointment.owner_user_id == actor.user_id
        if "ibclc" in actor.roles:
            scope = or_(scope, and_(CareAppointment.provider_id == actor.user_id, CareEpisode.assigned_ibclc_id == actor.user_id, CareProvider.active.is_(True)))
        return cast(CareAppointment | None, await self.session.scalar(select(CareAppointment).join(CareProvider, CareProvider.user_id == CareAppointment.provider_id)
            .join(CareEpisode, CareEpisode.id == CareAppointment.episode_id)
            .where(CareAppointment.id == appointment_id, scope).execution_options(populate_existing=True)))

    async def consultation(self, appointment_id: UUID, *, lock: bool = False) -> CareConsultation | None:
        statement = select(CareConsultation).where(CareConsultation.appointment_id == appointment_id)
        if lock:
            statement = statement.with_for_update()
        return cast(CareConsultation | None, await self.session.scalar(statement.execution_options(populate_existing=True)))

    async def participant(self, consultation_id: UUID, role: str) -> CareRoomParticipant | None:
        return cast(CareRoomParticipant | None, await self.session.scalar(select(CareRoomParticipant).where(
            CareRoomParticipant.consultation_id == consultation_id, CareRoomParticipant.role == role)))

    async def participants(self, consultation_id: UUID) -> list[CareRoomParticipant]:
        return list(await self.session.scalars(select(CareRoomParticipant).where(CareRoomParticipant.consultation_id == consultation_id).order_by(CareRoomParticipant.role)))

    async def location(self, appointment_id: UUID) -> CareLocationCheck | None:
        return cast(CareLocationCheck | None, await self.session.scalar(select(CareLocationCheck).where(CareLocationCheck.appointment_id == appointment_id)
            .order_by(CareLocationCheck.created_at.desc(), CareLocationCheck.id).limit(1)))

    async def enqueue(self, consultation: CareConsultation, operation: str, now: datetime) -> None:
        existing = await self.session.scalar(select(CareVideoCommand.id).where(CareVideoCommand.room_name == consultation.room_name, CareVideoCommand.operation == operation))
        if existing is None:
            self.session.add(CareVideoCommand(consultation_id=consultation.id, room_name=consultation.room_name, operation=operation, available_at=now))
            await self.session.flush()

    async def close_for_consent(self, episode_id: UUID, now: datetime) -> None:
        rooms = await self.session.scalars(select(CareConsultation).where(CareConsultation.episode_id == episode_id,
            CareConsultation.room_status.in_(["creating", "ready", "failed"])).with_for_update())
        for room in rooms:
            room.room_status = "closing"
            room.version += 1
            await self.enqueue(room, "close", now)

    async def cancel(self, appointment_id: UUID, now: datetime) -> None:
        room = await self.consultation(appointment_id, lock=True)
        if room and room.status == "waiting_room":
            room.status, room.room_status, room.ended_at = "cancelled", "closing", now
            room.version += 1
            await self.enqueue(room, "close", now)

    async def add(self, value: Any) -> None:
        self.session.add(value)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()
