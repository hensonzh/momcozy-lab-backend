from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService
from ..auth import CurrentUser
from .repository import WorkbenchRepository
from .schemas import WorkbenchAppointmentsRead, WorkbenchClientDetailRead, WorkbenchClientsRead, WorkbenchIdentityRead, WorkbenchCalendarRead
from .service import WorkbenchService
from .schemas import WorkbenchRemindersRead
from .schemas import WorkbenchFollowupsRead

router = SurfaceAPIRouter(prefix="/ibclc", tags=["ibclc-workbench"],
    api_surface_metadata=api_surface("public_app_api", owner="ibclc", clients=["ibclc"]))


def get_workbench_service(request: Request, response: Response, session: AsyncSession = Depends(get_session)) -> WorkbenchService:
    response.headers["Cache-Control"] = "private, no-store"
    return WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), request.app.state.settings)


@router.get("/me", response_model=WorkbenchIdentityRead)
async def identity(actor: CurrentUser = Depends(require_current_user), service: WorkbenchService = Depends(get_workbench_service)) -> WorkbenchIdentityRead:
    return await service.identity(actor)


@router.get('/reminders', response_model=WorkbenchRemindersRead)
async def reminders(request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    actor: CurrentUser = Depends(require_current_user), service: WorkbenchService = Depends(get_workbench_service)) -> WorkbenchRemindersRead:
    return await service.reminders(actor, offset=offset, limit=limit, request_id=request.state.request_id)


@router.put('/reminders/{event_id}/read', status_code=204)
async def read_reminder(event_id: UUID, request: Request, actor: CurrentUser = Depends(require_current_user),
    service: WorkbenchService = Depends(get_workbench_service)) -> Response:
    await service.read_reminder(actor, event_id, request.state.request_id)
    return Response(status_code=204, headers={'Cache-Control': 'private, no-store'})


@router.get('/followups', response_model=WorkbenchFollowupsRead)
async def followups(request: Request, q: str = Query(default='', max_length=120), status: Literal['all', 'pending', 'completed'] = 'all',
    offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50), actor: CurrentUser = Depends(require_current_user),
    service: WorkbenchService = Depends(get_workbench_service)) -> WorkbenchFollowupsRead:
    return await service.followups(actor, query=q, status=status, offset=offset, limit=limit, request_id=request.state.request_id)


@router.get("/appointments", response_model=WorkbenchAppointmentsRead)
async def appointments(request: Request, date: date | None = None, offset: int = Query(default=0, ge=0), limit: int = Query(default=10, ge=1, le=50),
    actor: CurrentUser = Depends(require_current_user), service: WorkbenchService = Depends(get_workbench_service)) -> WorkbenchAppointmentsRead:
    return await service.appointments(actor, day=date, offset=offset, limit=limit, request_id=request.state.request_id)


@router.get("/clients", response_model=WorkbenchClientsRead)
async def clients(request: Request, q: str = Query(default="", max_length=120), status: Literal["all", "active", "completed"] = "all",
    offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50), actor: CurrentUser = Depends(require_current_user),
    service: WorkbenchService = Depends(get_workbench_service)) -> WorkbenchClientsRead:
    return await service.clients(actor, query=q, status=status, offset=offset, limit=limit, request_id=request.state.request_id)


@router.get("/calendar", response_model=WorkbenchCalendarRead)
async def calendar(request: Request, date: date | None = None, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200),
    actor: CurrentUser = Depends(require_current_user), service: WorkbenchService = Depends(get_workbench_service)) -> WorkbenchCalendarRead:
    return await service.calendar(actor, selected=date, offset=offset, limit=limit, request_id=request.state.request_id)


@router.get("/clients/{patient_ref}", response_model=WorkbenchClientDetailRead)
async def client_detail(patient_ref: UUID, request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50), actor: CurrentUser = Depends(require_current_user),
    service: WorkbenchService = Depends(get_workbench_service)) -> WorkbenchClientDetailRead:
    return await service.client_detail(actor, patient_ref, request.state.request_id, offset=offset, limit=limit)
