from datetime import date
from uuid import UUID

from fastapi import Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService
from ..auth import CurrentUser
from .repository import BabyRecordRepository
from .schemas import BabyRecordItems, BabyRecordBatchWrite, BabyRecordDeletion, BabyRecordList, BabyRecordRead, BabyRecordUpdate, BabyRecordVersion, BabyRecordWrite, RecordKind
from .service import BabyRecordService

router = SurfaceAPIRouter(prefix='/babies/{baby_id}/records', tags=['baby-records'],
    api_surface_metadata=api_surface('public_app_api', owner='baby', clients=['flutter']))


def get_baby_records(response: Response, session: AsyncSession = Depends(get_session)) -> BabyRecordService:
    response.headers['Cache-Control'] = 'private, no-store'
    audit = AuditRepository(session)
    return BabyRecordService(BabyRecordRepository(session), AuditService(repository=audit), IdempotencyService(repository=audit))


@router.get('', response_model=BabyRecordList)
async def list_records(baby_id: UUID, start_date: date, end_date: date, timezone: str = Query(min_length=1, max_length=80), kind: RecordKind | None = None,
    offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200),
    actor: CurrentUser = Depends(require_current_user), service: BabyRecordService = Depends(get_baby_records)) -> BabyRecordList:
    return await service.list(actor.user_id, baby_id, start_date, end_date, timezone_name=timezone, kind=kind, offset=offset, limit=limit)


@router.post('', response_model=BabyRecordRead, status_code=201)
async def create(baby_id: UUID, body: BabyRecordWrite, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=255),
    actor: CurrentUser = Depends(require_current_user), service: BabyRecordService = Depends(get_baby_records)) -> BabyRecordRead:
    return BabyRecordRead.model_validate(await service.create(actor.user_id, baby_id, body.observation, idempotency_key, request.state.request_id))


@router.put('/{record_id}', response_model=BabyRecordRead)
async def update(baby_id: UUID, record_id: UUID, body: BabyRecordUpdate, request: Request,
    actor: CurrentUser = Depends(require_current_user), service: BabyRecordService = Depends(get_baby_records)) -> BabyRecordRead:
    return BabyRecordRead.model_validate(await service.update(actor.user_id, baby_id, record_id, body, request.state.request_id))


@router.post('/batch', response_model=BabyRecordItems, status_code=201)
async def create_batch(baby_id: UUID, body: BabyRecordBatchWrite, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=255),
    actor: CurrentUser = Depends(require_current_user), service: BabyRecordService = Depends(get_baby_records)) -> BabyRecordItems:
    values = await service.create_batch(actor.user_id, baby_id, body, idempotency_key, request.state.request_id)
    return BabyRecordItems(items=[BabyRecordRead.model_validate(value) for value in values])


@router.get('/latest-growth', response_model=BabyRecordItems)
async def latest_growth(baby_id: UUID, actor: CurrentUser = Depends(require_current_user), service: BabyRecordService = Depends(get_baby_records)) -> BabyRecordItems:
    values = await service.latest_growth(actor.user_id, baby_id)
    return BabyRecordItems(items=[BabyRecordRead.model_validate(value) for value in values])


@router.delete('/{record_id}', response_model=BabyRecordDeletion)
async def delete(baby_id: UUID, record_id: UUID, request: Request, expected_version: int = Header(alias='If-Match', ge=1),
    actor: CurrentUser = Depends(require_current_user), service: BabyRecordService = Depends(get_baby_records)) -> BabyRecordDeletion:
    value = await service.set_deleted(actor.user_id, baby_id, record_id, expected_version, True, request.state.request_id)
    return BabyRecordDeletion(id=value.id, version=value.version)


@router.post('/{record_id}/restore', response_model=BabyRecordRead)
async def restore(baby_id: UUID, record_id: UUID, body: BabyRecordVersion, request: Request,
    actor: CurrentUser = Depends(require_current_user), service: BabyRecordService = Depends(get_baby_records)) -> BabyRecordRead:
    return BabyRecordRead.model_validate(await service.set_deleted(actor.user_id, baby_id, record_id, body.expected_version, False, request.state.request_id))
