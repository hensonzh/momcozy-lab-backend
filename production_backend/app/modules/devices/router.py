from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import optional_idempotency_key, require_current_user
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import DevicesRepository
from .schemas import (
    PumpDeviceListResponse,
    PumpDeviceRead,
    PumpDeviceUpsert,
    PumpTelemetryEventCreate,
    PumpTelemetryEventListResponse,
    PumpTelemetryEventRead,
)
from .service import DevicesService


router = APIRouter(prefix="/devices", tags=["devices"])


def get_devices_service(session: AsyncSession = Depends(get_session)) -> DevicesService:
    audit_repository = AuditRepository(session)
    return DevicesService(
        repository=DevicesRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )


@router.put("/pumps/{device_id}", response_model=PumpDeviceRead)
async def upsert_pump_device(
    device_id: str,
    payload: PumpDeviceUpsert,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpDeviceRead:
    device = await service.upsert_device(
        owner_user_id=current_user.user_id,
        device_id=device_id,
        model=payload.model or "",
        firmware_version=payload.firmware_version or "",
        last_seen_at=payload.last_seen_at,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return PumpDeviceRead.model_validate(device)


@router.get("/pumps", response_model=PumpDeviceListResponse)
async def list_pump_devices(
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpDeviceListResponse:
    devices = await service.list_devices(owner_user_id=current_user.user_id)
    return PumpDeviceListResponse(items=[PumpDeviceRead.model_validate(device) for device in devices])


@router.post("/pump-telemetry", response_model=PumpTelemetryEventRead, status_code=status.HTTP_201_CREATED)
async def create_pump_telemetry(
    payload: PumpTelemetryEventCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpTelemetryEventRead:
    event = await service.create_telemetry_event(
        owner_user_id=current_user.user_id,
        device_id=payload.device_id,
        event_type=payload.event_type,
        occurred_at=payload.occurred_at,
        payload=payload.payload,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return PumpTelemetryEventRead.model_validate(event)


@router.get("/pump-telemetry", response_model=PumpTelemetryEventListResponse)
async def list_pump_telemetry(
    device_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpTelemetryEventListResponse:
    events = await service.list_telemetry_events(
        owner_user_id=current_user.user_id,
        device_id=device_id,
        event_type=event_type,
        limit=limit,
    )
    return PumpTelemetryEventListResponse(items=[PumpTelemetryEventRead.model_validate(event) for event in events])
