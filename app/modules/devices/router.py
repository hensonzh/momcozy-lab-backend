from __future__ import annotations

from fastapi import Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import optional_idempotency_key, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import DevicesRepository
from .schemas import (
    PumpDeviceListResponse,
    PumpDeviceRead,
    PumpDeviceUpsert,
    PumpEnergyTargetRead,
    PumpHealthCreate,
    PumpHealthRead,
    PumpTelemetryEventCreate,
    PumpTelemetryEventListResponse,
    PumpTelemetryEventRead,
    PumpThresholdCreate,
    PumpThresholdRead,
    PumpWorkstateCreate,
    PumpWorkstateRead,
)
from .service import DevicesService


router = SurfaceAPIRouter(
    prefix="/devices",
    tags=["devices"],
    api_surface_metadata=api_surface("public_app_api", owner="devices", clients=["flutter"]),
)


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


@router.post("/pump-workstate", response_model=PumpWorkstateRead, status_code=status.HTTP_201_CREATED)
async def create_pump_workstate(
    payload: PumpWorkstateCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpWorkstateRead:
    event = await service.create_pump_workstate(
        owner_user_id=current_user.user_id,
        device_id=payload.device_id,
        occurred_at=payload.occurred_at,
        state=payload.state,
        source=payload.source or "device",
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return PumpWorkstateRead.from_event(PumpTelemetryEventRead.model_validate(event))


@router.get("/pump-workstate/latest", response_model=PumpWorkstateRead)
async def get_latest_pump_workstate(
    device_id: str = Query(min_length=1, max_length=120),
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpWorkstateRead:
    event = await service.get_latest_pump_workstate(owner_user_id=current_user.user_id, device_id=device_id)
    return PumpWorkstateRead.from_event(PumpTelemetryEventRead.model_validate(event))


@router.post("/pump-threshold", response_model=PumpThresholdRead, status_code=status.HTTP_201_CREATED)
async def create_pump_threshold(
    payload: PumpThresholdCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpThresholdRead:
    event = await service.create_pump_threshold(
        owner_user_id=current_user.user_id,
        device_id=payload.device_id,
        occurred_at=payload.occurred_at,
        stimulate_level_l=payload.stimulate_level_l,
        deep_level_l=payload.deep_level_l,
        stimulate_level_r=payload.stimulate_level_r,
        deep_level_r=payload.deep_level_r,
        source=payload.source or "device",
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return PumpThresholdRead.from_event(PumpTelemetryEventRead.model_validate(event))


@router.get("/pump-threshold/latest", response_model=PumpThresholdRead)
async def get_latest_pump_threshold(
    device_id: str = Query(min_length=1, max_length=120),
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpThresholdRead:
    event = await service.get_latest_pump_threshold(owner_user_id=current_user.user_id, device_id=device_id)
    return PumpThresholdRead.from_event(PumpTelemetryEventRead.model_validate(event))


@router.post("/pump-health", response_model=PumpHealthRead, status_code=status.HTTP_201_CREATED)
async def create_pump_health(
    payload: PumpHealthCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpHealthRead:
    event = await service.create_pump_health(
        owner_user_id=current_user.user_id,
        device_id=payload.device_id,
        occurred_at=payload.occurred_at,
        health_l=payload.health_l,
        health_r=payload.health_r,
        source=payload.source or "device",
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return PumpHealthRead.from_event(PumpTelemetryEventRead.model_validate(event))


@router.get("/pump-health/latest", response_model=PumpHealthRead)
async def get_latest_pump_health(
    device_id: str = Query(min_length=1, max_length=120),
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpHealthRead:
    event = await service.get_latest_pump_health(owner_user_id=current_user.user_id, device_id=device_id)
    return PumpHealthRead.from_event(PumpTelemetryEventRead.model_validate(event))


@router.get("/pump-energy-target", response_model=PumpEnergyTargetRead)
async def get_pump_energy_target(
    current_user: CurrentUser = Depends(require_current_user),
    service: DevicesService = Depends(get_devices_service),
) -> PumpEnergyTargetRead:
    _ = current_user
    return PumpEnergyTargetRead(**service.get_pump_energy_target())
