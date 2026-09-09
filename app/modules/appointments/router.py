from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService
from ..auth import CurrentUser
from .repository import AppointmentRepository
from .schemas import AppointmentRead, AvailabilityRead, BookingContextRead, BookingEligibilityRead, BookingPrecheckWrite, HoldWrite, VersionWrite
from .service import AppointmentService

router = SurfaceAPIRouter(prefix="/care", tags=["appointments"], api_surface_metadata=api_surface("public_app_api", owner="care", clients=["flutter"]))


def get_appointment_service(request: Request, session: AsyncSession = Depends(get_session)) -> AppointmentService:
    audit = AuditRepository(session)
    return AppointmentService(AppointmentRepository(session), AuditService(repository=audit), IdempotencyService(repository=audit),
        sandbox_enabled=not request.app.state.settings.is_production)


@router.get("/episodes/{episode_id}/booking", response_model=BookingContextRead)
async def booking_context(episode_id: UUID, user: CurrentUser = Depends(require_current_user),
    service: AppointmentService = Depends(get_appointment_service)) -> BookingContextRead:
    return await service.context(user.user_id, episode_id)


@router.post("/episodes/{episode_id}/booking-eligibility", response_model=BookingEligibilityRead, status_code=201)
async def booking_precheck(episode_id: UUID, body: BookingPrecheckWrite, request: Request, user: CurrentUser = Depends(require_current_user),
    service: AppointmentService = Depends(get_appointment_service)) -> BookingEligibilityRead:
    return await service.precheck(user.user_id, episode_id, body, request.state.request_id)


@router.get("/episodes/{episode_id}/availability", response_model=AvailabilityRead)
async def booking_availability(episode_id: UUID, eligibility_id: UUID, provider_id: UUID, date: date,
    user: CurrentUser = Depends(require_current_user), service: AppointmentService = Depends(get_appointment_service)) -> AvailabilityRead:
    return await service.availability(user.user_id, episode_id, eligibility_id, provider_id, date)


@router.post("/episodes/{episode_id}/holds", response_model=AppointmentRead, status_code=201)
async def hold_appointment(episode_id: UUID, body: HoldWrite, request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255), user: CurrentUser = Depends(require_current_user),
    service: AppointmentService = Depends(get_appointment_service)) -> AppointmentRead:
    return await service.hold(user.user_id, episode_id, body, idempotency_key, request.state.request_id)


@router.get("/appointments/{appointment_id}", response_model=AppointmentRead)
async def get_appointment(appointment_id: UUID, user: CurrentUser = Depends(require_current_user),
    service: AppointmentService = Depends(get_appointment_service)) -> AppointmentRead:
    return await service.read(user.user_id, appointment_id)


@router.post("/appointments/{appointment_id}/confirm", response_model=AppointmentRead)
async def confirm_appointment(appointment_id: UUID, body: VersionWrite, request: Request, user: CurrentUser = Depends(require_current_user),
    service: AppointmentService = Depends(get_appointment_service)) -> AppointmentRead:
    return await service.confirm(user.user_id, appointment_id, body, request.state.request_id)


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentRead)
async def cancel_appointment(appointment_id: UUID, body: VersionWrite, request: Request, user: CurrentUser = Depends(require_current_user),
    service: AppointmentService = Depends(get_appointment_service)) -> AppointmentRead:
    return await service.cancel(user.user_id, appointment_id, body, request.state.request_id)
