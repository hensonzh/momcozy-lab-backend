from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...infrastructure.db import get_session
from ..auth import CurrentUser
from ..notifications import NotificationsService
from ..notifications.repository import NotificationsRepository
from ..plans import PlansService
from ..plans.repository import PlansRepository
from ..profiles import ProfileService
from ..profiles.repository import ProfileRepository
from ..records import RecordsService
from ..records.repository import RecordsRepository
from .schemas import StatusPageTodayRead
from .service import StatusPageService


router = APIRouter(prefix="/status-page", tags=["status-page"])


def get_status_page_service(session: AsyncSession = Depends(get_session)) -> StatusPageService:
    return StatusPageService(
        profile_service=ProfileService(repository=ProfileRepository(session)),
        records_service=RecordsService(repository=RecordsRepository(session)),
        plans_service=PlansService(repository=PlansRepository(session)),
        notifications_service=NotificationsService(repository=NotificationsRepository(session)),
    )


@router.get("/today", response_model=StatusPageTodayRead)
async def get_today_status_page(
    day: date | None = Query(default=None),
    current_user: CurrentUser = Depends(require_current_user),
    service: StatusPageService = Depends(get_status_page_service),
) -> StatusPageTodayRead:
    return await service.get_today_status(owner_user_id=current_user.user_id, day=day or datetime.now(timezone.utc).date())
