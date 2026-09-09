from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService
from ..auth import CurrentUser
from .repository import LactationRepository
from .schemas import LactationList, LactationRead, LactationUpdate, LactationWrite, RecordDeletion, RecordVersion
from .service import LactationService

router = SurfaceAPIRouter(prefix="/lactation/records", tags=["lactation"],
    api_surface_metadata=api_surface("public_app_api", owner="lactation", clients=["flutter"]))


def get_lactation_service(session: AsyncSession = Depends(get_session)) -> LactationService:
    audit = AuditRepository(session)
    return LactationService(LactationRepository(session), AuditService(repository=audit), IdempotencyService(repository=audit))


@router.get("", response_model=LactationList)
async def list_records(start: datetime, end: datetime, user: CurrentUser = Depends(require_current_user),
    service: LactationService = Depends(get_lactation_service)) -> LactationList:
    return LactationList(items=[LactationRead.model_validate(record) for record in await service.list(user.user_id, start, end)])


@router.post("", response_model=LactationRead, status_code=201)
async def create_record(body: LactationWrite, request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    user: CurrentUser = Depends(require_current_user), service: LactationService = Depends(get_lactation_service)) -> LactationRead:
    return LactationRead.model_validate(await service.create(user.user_id, body.observation, idempotency_key, request.state.request_id))


@router.put("/{record_id}", response_model=LactationRead)
async def update_record(record_id: UUID, body: LactationUpdate, request: Request,
    user: CurrentUser = Depends(require_current_user), service: LactationService = Depends(get_lactation_service)) -> LactationRead:
    return LactationRead.model_validate(await service.update(user.user_id, record_id, body, request.state.request_id))


@router.delete("/{record_id}", response_model=RecordDeletion)
async def delete_record(record_id: UUID, request: Request, expected_version: int = Header(alias="If-Match", ge=1),
    user: CurrentUser = Depends(require_current_user), service: LactationService = Depends(get_lactation_service)) -> RecordDeletion:
    record = await service.set_deleted(user.user_id, record_id, expected_version, True, request.state.request_id)
    return RecordDeletion(id=record.id, version=record.version)


@router.post("/{record_id}/restore", response_model=LactationRead)
async def restore_record(record_id: UUID, body: RecordVersion, request: Request,
    user: CurrentUser = Depends(require_current_user), service: LactationService = Depends(get_lactation_service)) -> LactationRead:
    return LactationRead.model_validate(await service.set_deleted(user.user_id, record_id, body.expected_version, False, request.state.request_id))
