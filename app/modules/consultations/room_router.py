from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ...infrastructure.video.provider import build_video_provider
from ..appointments.router import get_appointment_service
from ..appointments.schemas import VersionWrite
from ..appointments.service import AppointmentService
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService
from ..auth import CurrentUser
from .repository import ConsultationRepository
from .room_repository import RoomRepository
from .room_schemas import EndWrite, JoinRead, LocationRead, LocationWrite, PresenceWrite, RoomContextRead
from .room_service import RoomService

router = SurfaceAPIRouter(prefix="/care/appointments/{appointment_id}", tags=["consultation-room"],
    api_surface_metadata=api_surface("public_app_api", owner="care", clients=["flutter", "ibclc"]))


def get_room_service(request: Request, session: AsyncSession = Depends(get_session), appointments: AppointmentService = Depends(get_appointment_service)) -> RoomService:
    audit = AuditRepository(session)
    settings = request.app.state.settings
    return RoomService(RoomRepository(session), ConsultationRepository(session), appointments,
        AuditService(repository=audit), IdempotencyService(repository=audit), build_video_provider(settings),
        demo_early_join=settings.consultation_demo_early_join and not settings.is_production)


@router.get("/room", response_model=RoomContextRead)
async def room_context(appointment_id: UUID, user: CurrentUser = Depends(require_current_user), service: RoomService = Depends(get_room_service)) -> RoomContextRead:
    return await service.context(user, appointment_id)


@router.post("/location-check", response_model=LocationRead)
async def check_location(appointment_id: UUID, body: LocationWrite, request: Request,
    user: CurrentUser = Depends(require_current_user), service: RoomService = Depends(get_room_service)) -> LocationRead:
    return await service.check_location(user, appointment_id, body, request.state.request_id)


@router.post("/room", response_model=RoomContextRead)
async def prepare_room(appointment_id: UUID, request: Request, user: CurrentUser = Depends(require_current_user), service: RoomService = Depends(get_room_service)) -> RoomContextRead:
    return await service.prepare(user, appointment_id, request.state.request_id)


@router.post("/room/join", response_model=JoinRead)
async def join_room(appointment_id: UUID, request: Request, response: Response, idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    user: CurrentUser = Depends(require_current_user), service: RoomService = Depends(get_room_service)) -> JoinRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.join(user, appointment_id, idempotency_key, request.state.request_id)


@router.put("/room/presence", response_model=RoomContextRead)
async def room_presence(appointment_id: UUID, body: PresenceWrite, request: Request,
    user: CurrentUser = Depends(require_current_user), service: RoomService = Depends(get_room_service)) -> RoomContextRead:
    return await service.presence(user, appointment_id, body, request.state.request_id)


@router.post("/room/start", response_model=RoomContextRead)
async def start_consultation(appointment_id: UUID, body: VersionWrite, request: Request,
    user: CurrentUser = Depends(require_current_user), service: RoomService = Depends(get_room_service)) -> RoomContextRead:
    return await service.start(user, appointment_id, body, request.state.request_id)


@router.post("/room/end", response_model=RoomContextRead)
async def end_consultation(appointment_id: UUID, body: EndWrite, request: Request,
    user: CurrentUser = Depends(require_current_user), service: RoomService = Depends(get_room_service)) -> RoomContextRead:
    return await service.end(user, appointment_id, body, request.state.request_id)
