from __future__ import annotations

from datetime import date

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService
from ..auth import CurrentUser
from .repository import MotherDiaryRepository
from .schemas import MotherDiaryList, MotherDiaryRead, MotherDiaryWrite
from .service import MotherDiaryService


router = SurfaceAPIRouter(prefix="/mother/diary", tags=["mother"],
    api_surface_metadata=api_surface("public_app_api", owner="mother", clients=["flutter"]))


def get_mother_diary_service(session: AsyncSession = Depends(get_session)) -> MotherDiaryService:
    return MotherDiaryService(MotherDiaryRepository(session), AuditService(repository=AuditRepository(session)))


@router.get("", response_model=MotherDiaryList)
async def list_diary(start: date, end: date,
    user: CurrentUser = Depends(require_current_user),
    service: MotherDiaryService = Depends(get_mother_diary_service),
) -> MotherDiaryList:
    return MotherDiaryList(items=[MotherDiaryRead.model_validate(entry) for entry in await service.list(user.user_id, start, end)])


@router.put("/{entry_date}", response_model=MotherDiaryRead)
async def save_diary(entry_date: date, payload: MotherDiaryWrite, request: Request,
    user: CurrentUser = Depends(require_current_user),
    service: MotherDiaryService = Depends(get_mother_diary_service),
) -> MotherDiaryRead:
    return MotherDiaryRead.model_validate(await service.save(user.user_id, entry_date, payload,
        str(getattr(request.state, "request_id", ""))))
