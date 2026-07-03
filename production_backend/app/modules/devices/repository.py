from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PumpDevice, PumpTelemetryEvent


class DevicesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_device(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        model: str,
        firmware_version: str,
        last_seen_at: datetime | None,
    ) -> PumpDevice:
        statement = select(PumpDevice).where(
            PumpDevice.owner_user_id == owner_user_id,
            PumpDevice.device_id == device_id,
            PumpDevice.deleted_at.is_(None),
        )
        device = await self.session.scalar(statement)
        if device is None:
            device = PumpDevice(owner_user_id=owner_user_id, device_id=device_id)
            self.session.add(device)
        device.model = model
        device.firmware_version = firmware_version
        device.last_seen_at = last_seen_at
        device.status = "active"
        await self.session.flush()
        return device

    async def list_devices(self, *, owner_user_id: UUID) -> list[PumpDevice]:
        statement = (
            select(PumpDevice)
            .where(PumpDevice.owner_user_id == owner_user_id, PumpDevice.status == "active", PumpDevice.deleted_at.is_(None))
            .order_by(PumpDevice.updated_at.desc(), PumpDevice.id.desc())
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def get_device_for_owner(self, *, owner_user_id: UUID, device_id: str) -> PumpDevice | None:
        statement = select(PumpDevice).where(
            PumpDevice.owner_user_id == owner_user_id,
            PumpDevice.device_id == device_id,
            PumpDevice.deleted_at.is_(None),
        )
        return cast(PumpDevice | None, await self.session.scalar(statement))

    async def create_telemetry_event(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> PumpTelemetryEvent:
        event = PumpTelemetryEvent(
            owner_user_id=owner_user_id,
            device_id=device_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_telemetry_event_for_owner(self, *, owner_user_id: UUID, event_id: UUID) -> PumpTelemetryEvent | None:
        statement = select(PumpTelemetryEvent).where(
            PumpTelemetryEvent.owner_user_id == owner_user_id,
            PumpTelemetryEvent.id == event_id,
        )
        return cast(PumpTelemetryEvent | None, await self.session.scalar(statement))

    async def list_telemetry_events(
        self,
        *,
        owner_user_id: UUID,
        device_id: str | None,
        event_type: str | None,
        limit: int,
    ) -> list[PumpTelemetryEvent]:
        conditions = [PumpTelemetryEvent.owner_user_id == owner_user_id]
        if device_id:
            conditions.append(PumpTelemetryEvent.device_id == device_id)
        if event_type:
            conditions.append(PumpTelemetryEvent.event_type == event_type)
        statement = select(PumpTelemetryEvent).where(*conditions).order_by(PumpTelemetryEvent.occurred_at.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def get_latest_telemetry_event(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        event_type: str,
    ) -> PumpTelemetryEvent | None:
        statement = (
            select(PumpTelemetryEvent)
            .where(
                PumpTelemetryEvent.owner_user_id == owner_user_id,
                PumpTelemetryEvent.device_id == device_id,
                PumpTelemetryEvent.event_type == event_type,
            )
            .order_by(PumpTelemetryEvent.occurred_at.desc(), PumpTelemetryEvent.id.desc())
            .limit(1)
        )
        return cast(PumpTelemetryEvent | None, await self.session.scalar(statement))
