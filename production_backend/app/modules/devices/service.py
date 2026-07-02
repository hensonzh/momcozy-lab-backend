from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import PumpDevice, PumpTelemetryEvent
from .repository import DevicesRepository


TELEMETRY_CREATE_IDEMPOTENCY_SCOPE = "devices.telemetry.create"


class DevicesService:
    def __init__(
        self,
        *,
        repository: DevicesRepository,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service

    async def upsert_device(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        model: str = "",
        firmware_version: str = "",
        last_seen_at: datetime | None = None,
        request_id: str = "",
    ) -> PumpDevice:
        normalized_device_id = _normalize_identifier(device_id, field_name="device_id", max_length=120)
        device = await self.repository.upsert_device(
            owner_user_id=owner_user_id,
            device_id=normalized_device_id,
            model=model,
            firmware_version=firmware_version,
            last_seen_at=last_seen_at,
        )
        await self._audit(
            owner_user_id=owner_user_id,
            action="devices.pump.upsert",
            resource_type="pump_device",
            resource_id=str(device.id),
            request_id=request_id,
        )
        return device

    async def list_devices(self, *, owner_user_id: UUID) -> list[PumpDevice]:
        return await self.repository.list_devices(owner_user_id=owner_user_id)

    async def create_telemetry_event(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, Any],
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> PumpTelemetryEvent:
        normalized_device_id = _normalize_identifier(device_id, field_name="device_id", max_length=120)
        normalized_event_type = _normalize_identifier(event_type, field_name="event_type", max_length=64)
        if await self.repository.get_device_for_owner(owner_user_id=owner_user_id, device_id=normalized_device_id) is None:
            await self.repository.upsert_device(
                owner_user_id=owner_user_id,
                device_id=normalized_device_id,
                model="",
                firmware_version="",
                last_seen_at=occurred_at,
            )
        idempotency_record = None
        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=TELEMETRY_CREATE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "device_id": normalized_device_id,
                        "event_type": normalized_event_type,
                        "occurred_at": occurred_at.isoformat(),
                        "payload": payload,
                    }
                ),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_event(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        event = await self.repository.create_telemetry_event(
            owner_user_id=owner_user_id,
            device_id=normalized_device_id,
            event_type=normalized_event_type,
            occurred_at=occurred_at,
            payload=payload,
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(event.id))
        await self._audit(
            owner_user_id=owner_user_id,
            action="devices.telemetry.create",
            resource_type="pump_telemetry_event",
            resource_id=str(event.id),
            request_id=request_id,
        )
        return event

    async def list_telemetry_events(
        self,
        *,
        owner_user_id: UUID,
        device_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[PumpTelemetryEvent]:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_telemetry_events(
            owner_user_id=owner_user_id,
            device_id=device_id,
            event_type=event_type,
            limit=limit,
        )

    async def _replay_event(self, *, owner_user_id: UUID, response_ref: str) -> PumpTelemetryEvent:
        event_id = parse_idempotency_response_ref(response_ref)
        event = await self.repository.get_telemetry_event_for_owner(owner_user_id=owner_user_id, event_id=event_id)
        if event is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return event

    async def _audit(
        self,
        *,
        owner_user_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
    ) -> None:
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
            )


def _normalize_identifier(value: str, *, field_name: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ApiError(code="validation_failed", message=f"{field_name} is required.", status=422)
    if len(normalized) > max_length:
        raise ApiError(code="validation_failed", message=f"{field_name} is too long.", status=422)
    return normalized
