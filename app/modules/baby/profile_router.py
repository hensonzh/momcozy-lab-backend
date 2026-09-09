from uuid import UUID

from fastapi import Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService
from ..auth import CurrentUser
from .profile_schemas import BabyProfileList, BabyProfileRead, BabyProfileUpdate, BabyProfileWrite
from .profile_service import BabyProfileService
from .repository import BabyRecordRepository

router = SurfaceAPIRouter(prefix='/babies', tags=['baby-profiles'],
    api_surface_metadata=api_surface('public_app_api', owner='baby', clients=['flutter']))


def get_baby_profiles(response: Response, session: AsyncSession = Depends(get_session)) -> BabyProfileService:
    response.headers['Cache-Control'] = 'private, no-store'
    audit = AuditRepository(session)
    return BabyProfileService(BabyRecordRepository(session), AuditService(repository=audit), IdempotencyService(repository=audit))


@router.get('', response_model=BabyProfileList)
async def list_profiles(actor: CurrentUser = Depends(require_current_user), service: BabyProfileService = Depends(get_baby_profiles)) -> BabyProfileList:
    return await service.list(actor.user_id)


@router.post('', response_model=BabyProfileRead, status_code=201)
async def create(body: BabyProfileWrite, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=255),
    actor: CurrentUser = Depends(require_current_user), service: BabyProfileService = Depends(get_baby_profiles)) -> BabyProfileRead:
    return BabyProfileRead.model_validate(await service.create(actor.user_id, body, idempotency_key, request.state.request_id))


@router.put('/{baby_id}', response_model=BabyProfileRead)
async def update(baby_id: UUID, body: BabyProfileUpdate, request: Request,
    actor: CurrentUser = Depends(require_current_user), service: BabyProfileService = Depends(get_baby_profiles)) -> BabyProfileRead:
    return BabyProfileRead.model_validate(await service.update(actor.user_id, baby_id, body, request.state.request_id))
