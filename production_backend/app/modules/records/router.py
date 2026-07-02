from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import RecordsRepository
from .schemas import FeedingRecordCreate, FeedingRecordListResponse, FeedingRecordRead
from .service import RecordsService


router = APIRouter(prefix="/records", tags=["records"])


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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
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
        idempotency_key=_normalize_idempotency_key(idempotency_key),
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


def _normalize_idempotency_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 255:
        raise ApiError(code="validation_failed", message="Idempotency-Key is too long.", status=422)
    return key
