from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..appointments.router import get_appointment_service
from ..appointments.service import AppointmentService
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService
from ..auth import CurrentUser
from ..documentation.repository import DocumentationRepository
from ..documentation.service import DocumentationService
from .repository import ScheduleRepository
from .schemas import PersonalScheduleRead, PersonalScheduleUpdate, PersonalScheduleWrite, SchedulePageRead
from .service import ScheduleService

router = SurfaceAPIRouter(prefix="/schedule", tags=["schedule"],
    api_surface_metadata=api_surface("public_app_api", owner="schedule", clients=["flutter", "agent"]))
MutationKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


def get_schedule_service(session: AsyncSession = Depends(get_session), appointments: AppointmentService = Depends(get_appointment_service)) -> ScheduleService:
    audit_repo = AuditRepository(session)
    audit = AuditService(repository=audit_repo)
    docs = DocumentationService(DocumentationRepository(session), appointments, audit, IdempotencyService(repository=audit_repo))
    return ScheduleService(ScheduleRepository(session), audit=audit, idempotency=IdempotencyService(repository=audit_repo), documentation=docs)


@router.get("", response_model=SchedulePageRead)
async def read_schedule(start_date: date, end_date: date, timezone_name: str = Query(alias="timezone"), offset: int = Query(0, ge=0, le=10000), limit: int = Query(50, ge=1, le=100),
    user: CurrentUser = Depends(require_current_user), service: ScheduleService = Depends(get_schedule_service)) -> SchedulePageRead:
    return await service.read(user.user_id, start_date, end_date, timezone_name, offset, limit)


@router.post("/personal", response_model=PersonalScheduleRead, status_code=status.HTTP_201_CREATED)
async def create_personal(body: PersonalScheduleWrite, request: Request, key: MutationKey, user: CurrentUser = Depends(require_current_user), service: ScheduleService = Depends(get_schedule_service)) -> PersonalScheduleRead:
    return await service.create(user.user_id, body, key, request.state.request_id)


@router.patch("/personal/{task_id}", response_model=PersonalScheduleRead)
async def update_personal(task_id: UUID, body: PersonalScheduleUpdate, request: Request, user: CurrentUser = Depends(require_current_user), service: ScheduleService = Depends(get_schedule_service)) -> PersonalScheduleRead:
    return await service.update(user.user_id, task_id, body, request.state.request_id)


@router.delete("/personal/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_personal(task_id: UUID, request: Request, expected_updated_at: datetime = Query(), user: CurrentUser = Depends(require_current_user), service: ScheduleService = Depends(get_schedule_service)) -> Response:
    await service.delete(user.user_id, task_id, expected_updated_at, request.state.request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
