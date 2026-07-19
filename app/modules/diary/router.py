from __future__ import annotations

from datetime import date

from fastapi import Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit import AuditService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .models import PregnancyDiaryEntry
from .repository import DiaryRepository
from .schemas import (
    PregnancyDiaryEntryCreate,
    PregnancyDiaryEntryListResponse,
    PregnancyDiaryEntryRead,
    PregnancyDiaryEntryUpdate,
)
from .service import DiaryService


router = SurfaceAPIRouter(
    prefix="/pregnancy-diary",
    tags=["pregnancy-diary"],
    api_surface_metadata=api_surface("public_app_api", owner="pregnancy-diary", clients=["flutter"]),
)


def get_diary_service(session: AsyncSession = Depends(get_session)) -> DiaryService:
    return DiaryService(
        repository=DiaryRepository(session),
        audit_service=AuditService(repository=AuditRepository(session)),
    )


@router.get("/entries", response_model=PregnancyDiaryEntryListResponse)
async def list_entries(
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: DiaryService = Depends(get_diary_service),
) -> PregnancyDiaryEntryListResponse:
    entries = await service.list_entries(
        owner_user_id=current_user.user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return PregnancyDiaryEntryListResponse(items=[_entry_read(entry) for entry in entries])


@router.get("/entries/{entry_date}", response_model=PregnancyDiaryEntryRead)
async def get_entry(
    entry_date: date,
    current_user: CurrentUser = Depends(require_current_user),
    service: DiaryService = Depends(get_diary_service),
) -> PregnancyDiaryEntryRead:
    entry = await service.get_entry(owner_user_id=current_user.user_id, entry_date=entry_date)
    return _entry_read(entry)


@router.post("/entries", response_model=PregnancyDiaryEntryRead, status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: PregnancyDiaryEntryCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: DiaryService = Depends(get_diary_service),
) -> PregnancyDiaryEntryRead:
    values = payload.model_dump(exclude={"entry_date"}, exclude_unset=True)
    entry = await service.create_entry(
        owner_user_id=current_user.user_id,
        entry_date=payload.entry_date,
        values=values,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return _entry_read(entry)


@router.patch("/entries/{entry_date}", response_model=PregnancyDiaryEntryRead)
async def update_entry(
    entry_date: date,
    payload: PregnancyDiaryEntryUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: DiaryService = Depends(get_diary_service),
) -> PregnancyDiaryEntryRead:
    entry = await service.update_entry(
        owner_user_id=current_user.user_id,
        entry_date=entry_date,
        values=payload.model_dump(exclude_unset=True),
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return _entry_read(entry)


@router.delete("/entries/{entry_date}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_date: date,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: DiaryService = Depends(get_diary_service),
) -> Response:
    await service.delete_entry(
        owner_user_id=current_user.user_id,
        entry_date=entry_date,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _entry_read(entry: PregnancyDiaryEntry) -> PregnancyDiaryEntryRead:
    return PregnancyDiaryEntryRead(
        id=entry.id,
        owner_user_id=entry.owner_user_id,
        entry_date=entry.entry_date,
        gestational_week=entry.gestational_week or "",
        mood=entry.mood or "",
        energy_level=entry.energy_level or "",
        sleep_summary=entry.sleep_summary or "",
        fetal_movement=entry.fetal_movement or "",
        symptom_tags=entry.symptom_tags or [],
        appointment_note=entry.appointment_note or "",
        nutrition_note=entry.nutrition_note or "",
        content=entry.content or "",
        attachments=entry.attachments or [],
        status=entry.status or "active",
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )
