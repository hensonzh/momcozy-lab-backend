from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import optional_idempotency_key, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import RecordsRepository
from .schemas import (
    FeedingRecordCreate,
    FeedingRecordListResponse,
    FeedingRecordRead,
    GrowthRecordCreate,
    GrowthRecordListResponse,
    GrowthRecordRead,
    GrowthRecordUpdate,
    MilkTrendListResponse,
    PumpingRecordCreate,
    PumpingRecordListResponse,
    PumpingRecordRead,
)
from .service import RecordsService


router = SurfaceAPIRouter(
    prefix="/records",
    tags=["records"],
    api_surface_metadata=api_surface("public_app_api", owner="records", clients=["flutter"]),
)


def get_records_service(session: AsyncSession = Depends(get_session)) -> RecordsService:
    audit_repository = AuditRepository(session)
    return RecordsService(
        repository=RecordsRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )


@router.post("/feeding", response_model=FeedingRecordRead, status_code=status.HTTP_201_CREATED)
async def create_feeding(
    payload: FeedingRecordCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> FeedingRecordRead:
    record = await service.create_feeding(
        owner_user_id=current_user.user_id,
        infant_id=payload.infant_id,
        feed_time=payload.feed_time,
        feed_type=payload.feed_type,
        feed_action=payload.feed_action or "",
        volume_ml=payload.volume_ml,
        duration_seconds=payload.duration_seconds,
        title=payload.title or "",
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return FeedingRecordRead.model_validate(record)


@router.get("/feeding", response_model=FeedingRecordListResponse)
async def list_feedings(
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> FeedingRecordListResponse:
    records = await service.list_feedings(
        owner_user_id=current_user.user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return FeedingRecordListResponse(items=[FeedingRecordRead.model_validate(record) for record in records])


@router.delete("/feeding/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feeding(
    record_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> Response:
    await service.delete_feeding(
        owner_user_id=current_user.user_id,
        record_id=record_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/pumping", response_model=PumpingRecordRead, status_code=status.HTTP_201_CREATED)
async def create_pumping(
    payload: PumpingRecordCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> PumpingRecordRead:
    record = await service.create_pumping(
        owner_user_id=current_user.user_id,
        pump_start_time=payload.pump_start_time,
        pump_end_time=payload.pump_end_time,
        milk_volume_ml=payload.milk_volume_ml,
        pump_type=payload.pump_type or "",
        duration_seconds=payload.duration_seconds,
        source=payload.source or "manual",
        title=payload.title or "",
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return PumpingRecordRead.model_validate(record)


@router.get("/pumping", response_model=PumpingRecordListResponse)
async def list_pumpings(
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> PumpingRecordListResponse:
    records = await service.list_pumpings(
        owner_user_id=current_user.user_id,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return PumpingRecordListResponse(items=[PumpingRecordRead.model_validate(record) for record in records])


@router.get("/milk-trends", response_model=MilkTrendListResponse)
async def get_milk_trends(
    start_date: date | None = None,
    days: int = Query(default=30, ge=1, le=90),
    include_today: bool = Query(default=True),
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> MilkTrendListResponse:
    return await service.get_milk_trends(
        owner_user_id=current_user.user_id,
        start_date=start_date,
        days=days,
        include_today=include_today,
    )


@router.delete("/pumping/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pumping(
    record_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> Response:
    await service.delete_pumping(
        owner_user_id=current_user.user_id,
        record_id=record_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/growth", response_model=GrowthRecordRead, status_code=status.HTTP_201_CREATED)
async def create_growth(
    payload: GrowthRecordCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> GrowthRecordRead:
    record = await service.create_growth(
        owner_user_id=current_user.user_id,
        infant_id=payload.infant_id,
        measured_at=payload.measured_at,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        head_cm=payload.head_cm,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return GrowthRecordRead.model_validate(record)


@router.get("/growth", response_model=GrowthRecordListResponse)
async def list_growth(
    infant_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> GrowthRecordListResponse:
    records = await service.list_growth(owner_user_id=current_user.user_id, infant_id=infant_id, limit=limit)
    return GrowthRecordListResponse(items=[GrowthRecordRead.model_validate(record) for record in records])


@router.patch("/growth/{record_id}", response_model=GrowthRecordRead)
async def update_growth(
    record_id: UUID,
    payload: GrowthRecordUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> GrowthRecordRead:
    record = await service.update_growth(
        owner_user_id=current_user.user_id,
        record_id=record_id,
        updates=payload.model_dump(exclude_unset=True),
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return GrowthRecordRead.model_validate(record)


@router.delete("/growth/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_growth(
    record_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: RecordsService = Depends(get_records_service),
) -> Response:
    await service.delete_growth(
        owner_user_id=current_user.user_id,
        record_id=record_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
