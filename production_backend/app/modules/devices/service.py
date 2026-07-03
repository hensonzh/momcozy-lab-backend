from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import PumpDevice, PumpTelemetryEvent
from .repository import DevicesRepository


TELEMETRY_CREATE_IDEMPOTENCY_SCOPE = "devices.telemetry.create"
PUMP_WORKSTATE_CREATE_IDEMPOTENCY_SCOPE = "devices.pump_workstate.create"
PUMP_THRESHOLD_CREATE_IDEMPOTENCY_SCOPE = "devices.pump_threshold.create"
PUMP_HEALTH_CREATE_IDEMPOTENCY_SCOPE = "devices.pump_health.create"
PUMP_WORKSTATE_EVENT_TYPE = "workstate"
PUMP_THRESHOLD_EVENT_TYPE = "threshold"
PUMP_HEALTH_EVENT_TYPE = "health"
PUMP_ENERGY_TARGET_LOWER_VALUE = 80
PUMP_ENERGY_TARGET_UPPER_VALUE = 100


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

        if await self.repository.get_device_for_owner(owner_user_id=owner_user_id, device_id=normalized_device_id) is None:
            await self.repository.upsert_device(
                owner_user_id=owner_user_id,
                device_id=normalized_device_id,
                model="",
                firmware_version="",
                last_seen_at=occurred_at,
            )

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

    async def create_pump_workstate(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        occurred_at: datetime,
        state: dict[str, Any],
        source: str = "device",
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> PumpTelemetryEvent:
        normalized_device_id = _normalize_identifier(device_id, field_name="device_id", max_length=120)
        normalized_source = _normalize_identifier(source or "device", field_name="source", max_length=64)
        safe_state = dict(state or {})
        idempotency_record = None
        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=PUMP_WORKSTATE_CREATE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "device_id": normalized_device_id,
                        "occurred_at": occurred_at.isoformat(),
                        "source": normalized_source,
                        "state": safe_state,
                    }
                ),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_event(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        if await self.repository.get_device_for_owner(owner_user_id=owner_user_id, device_id=normalized_device_id) is None:
            await self.repository.upsert_device(
                owner_user_id=owner_user_id,
                device_id=normalized_device_id,
                model="",
                firmware_version="",
                last_seen_at=occurred_at,
            )

        event = await self.repository.create_telemetry_event(
            owner_user_id=owner_user_id,
            device_id=normalized_device_id,
            event_type=PUMP_WORKSTATE_EVENT_TYPE,
            occurred_at=occurred_at,
            payload={"source": normalized_source, "state": safe_state},
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(event.id))
        await self._audit(
            owner_user_id=owner_user_id,
            action="devices.pump_workstate.create",
            resource_type="pump_telemetry_event",
            resource_id=str(event.id),
            request_id=request_id,
        )
        return event

    async def get_latest_pump_workstate(self, *, owner_user_id: UUID, device_id: str) -> PumpTelemetryEvent:
        normalized_device_id = _normalize_identifier(device_id, field_name="device_id", max_length=120)
        event = await self.repository.get_latest_telemetry_event(
            owner_user_id=owner_user_id,
            device_id=normalized_device_id,
            event_type=PUMP_WORKSTATE_EVENT_TYPE,
        )
        if event is None:
            raise ApiError(code="not_found", message="Pump workstate not found.", status=404)
        return event

    async def create_pump_threshold(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        occurred_at: datetime,
        stimulate_level_l: int,
        deep_level_l: int,
        stimulate_level_r: int,
        deep_level_r: int,
        source: str = "device",
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> PumpTelemetryEvent:
        payload = {
            "source": _normalize_identifier(source or "device", field_name="source", max_length=64),
            "stimulate_level_l": _validate_positive_int(stimulate_level_l, field_name="stimulate_level_l"),
            "deep_level_l": _validate_positive_int(deep_level_l, field_name="deep_level_l"),
            "stimulate_level_r": _validate_positive_int(stimulate_level_r, field_name="stimulate_level_r"),
            "deep_level_r": _validate_positive_int(deep_level_r, field_name="deep_level_r"),
        }
        return await self._create_pump_fact_event(
            owner_user_id=owner_user_id,
            device_id=device_id,
            occurred_at=occurred_at,
            payload=payload,
            event_type=PUMP_THRESHOLD_EVENT_TYPE,
            idempotency_scope=PUMP_THRESHOLD_CREATE_IDEMPOTENCY_SCOPE,
            audit_action="devices.pump_threshold.create",
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    async def get_latest_pump_threshold(self, *, owner_user_id: UUID, device_id: str) -> PumpTelemetryEvent:
        return await self._get_latest_pump_fact_event(
            owner_user_id=owner_user_id,
            device_id=device_id,
            event_type=PUMP_THRESHOLD_EVENT_TYPE,
            not_found_message="Pump threshold not found.",
        )

    async def create_pump_health(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        occurred_at: datetime,
        health_l: int,
        health_r: int,
        source: str = "device",
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> PumpTelemetryEvent:
        payload = {
            "source": _normalize_identifier(source or "device", field_name="source", max_length=64),
            "health_l": _validate_health_value(health_l, field_name="health_l"),
            "health_r": _validate_health_value(health_r, field_name="health_r"),
        }
        return await self._create_pump_fact_event(
            owner_user_id=owner_user_id,
            device_id=device_id,
            occurred_at=occurred_at,
            payload=payload,
            event_type=PUMP_HEALTH_EVENT_TYPE,
            idempotency_scope=PUMP_HEALTH_CREATE_IDEMPOTENCY_SCOPE,
            audit_action="devices.pump_health.create",
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    async def get_latest_pump_health(self, *, owner_user_id: UUID, device_id: str) -> PumpTelemetryEvent:
        return await self._get_latest_pump_fact_event(
            owner_user_id=owner_user_id,
            device_id=device_id,
            event_type=PUMP_HEALTH_EVENT_TYPE,
            not_found_message="Pump health not found.",
        )

    def get_pump_energy_target(self) -> dict[str, int]:
        return {"lower_value": PUMP_ENERGY_TARGET_LOWER_VALUE, "upper_value": PUMP_ENERGY_TARGET_UPPER_VALUE}

    async def _replay_event(self, *, owner_user_id: UUID, response_ref: str) -> PumpTelemetryEvent:
        event_id = parse_idempotency_response_ref(response_ref)
        event = await self.repository.get_telemetry_event_for_owner(owner_user_id=owner_user_id, event_id=event_id)
        if event is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return event

    async def _create_pump_fact_event(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        occurred_at: datetime,
        payload: dict[str, Any],
        event_type: str,
        idempotency_scope: str,
        audit_action: str,
        request_id: str,
        idempotency_key: str | None,
    ) -> PumpTelemetryEvent:
        normalized_device_id = _normalize_identifier(device_id, field_name="device_id", max_length=120)
        idempotency_record = None
        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=idempotency_scope,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "device_id": normalized_device_id,
                        "occurred_at": occurred_at.isoformat(),
                        "payload": payload,
                    }
                ),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_event(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        if await self.repository.get_device_for_owner(owner_user_id=owner_user_id, device_id=normalized_device_id) is None:
            await self.repository.upsert_device(
                owner_user_id=owner_user_id,
                device_id=normalized_device_id,
                model="",
                firmware_version="",
                last_seen_at=occurred_at,
            )

        event = await self.repository.create_telemetry_event(
            owner_user_id=owner_user_id,
            device_id=normalized_device_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(event.id))
        await self._audit(
            owner_user_id=owner_user_id,
            action=audit_action,
            resource_type="pump_telemetry_event",
            resource_id=str(event.id),
            request_id=request_id,
        )
        return event

    async def _get_latest_pump_fact_event(
        self,
        *,
        owner_user_id: UUID,
        device_id: str,
        event_type: str,
        not_found_message: str,
    ) -> PumpTelemetryEvent:
        normalized_device_id = _normalize_identifier(device_id, field_name="device_id", max_length=120)
        event = await self.repository.get_latest_telemetry_event(
            owner_user_id=owner_user_id,
            device_id=normalized_device_id,
            event_type=event_type,
        )
        if event is None:
            raise ApiError(code="not_found", message=not_found_message, status=404)
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


def _validate_positive_int(value: int, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ApiError(code="validation_failed", message=f"{field_name} must be an integer.", status=422) from None
    if parsed <= 0:
        raise ApiError(code="validation_failed", message=f"{field_name} must be a positive integer.", status=422)
    return parsed


def _validate_health_value(value: int, *, field_name: str) -> int:
    parsed = _validate_positive_or_zero_int(value, field_name=field_name)
    if parsed not in {0, 1, 2}:
        raise ApiError(code="validation_failed", message=f"{field_name} must be one of 0, 1, 2.", status=422)
    return parsed


def _validate_positive_or_zero_int(value: int, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ApiError(code="validation_failed", message=f"{field_name} must be an integer.", status=422) from None
    if parsed < 0:
        raise ApiError(code="validation_failed", message=f"{field_name} must be zero or a positive integer.", status=422)
    return parsed
