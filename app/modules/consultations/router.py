from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..appointments.router import get_appointment_service
from ..appointments.service import AppointmentService
from ..audit.repository import AuditRepository
from ..audit.service import AuditService
from ..auth import CurrentUser
from .repository import ConsultationRepository
from .schemas import ConsentListRead, ConsentRead, ConsentWrite, IntakeContextRead, IntakeRead, IntakeWrite
from .service import ConsultationService

router = SurfaceAPIRouter(tags=["consultations"], api_surface_metadata=api_surface("public_app_api", owner="care", clients=["flutter"]))


def get_consultation_service(session: AsyncSession = Depends(get_session), appointments: AppointmentService = Depends(get_appointment_service)) -> ConsultationService:
    return ConsultationService(ConsultationRepository(session), appointments, AuditService(repository=AuditRepository(session)))


@router.get("/care/appointments/{appointment_id}/intake", response_model=IntakeContextRead)
async def intake_context(appointment_id: UUID, user: CurrentUser = Depends(require_current_user), service: ConsultationService = Depends(get_consultation_service)) -> IntakeContextRead:
    return await service.intake_context(user.user_id, appointment_id)


@router.put("/care/appointments/{appointment_id}/intake", response_model=IntakeRead)
async def save_intake(appointment_id: UUID, body: IntakeWrite, request: Request, user: CurrentUser = Depends(require_current_user), service: ConsultationService = Depends(get_consultation_service)) -> IntakeRead:
    return await service.save_intake(user.user_id, appointment_id, body, request.state.request_id)


@router.get("/care/episodes/{episode_id}/consents", response_model=ConsentListRead)
async def get_consents(episode_id: UUID, user: CurrentUser = Depends(require_current_user), service: ConsultationService = Depends(get_consultation_service)) -> ConsentListRead:
    return ConsentListRead(items=await service.consents(user.user_id, episode_id))


@router.post("/care/episodes/{episode_id}/consents", response_model=ConsentRead)
async def set_consent(episode_id: UUID, body: ConsentWrite, request: Request, user: CurrentUser = Depends(require_current_user), service: ConsultationService = Depends(get_consultation_service)) -> ConsentRead:
    return await service.set_consent(user.user_id, episode_id, body, request.state.request_id)


@router.get("/ibclc/appointments/{appointment_id}/intake", response_model=IntakeRead)
async def expert_intake(appointment_id: UUID, request: Request, response: Response, user: CurrentUser = Depends(require_current_user), service: ConsultationService = Depends(get_consultation_service)) -> IntakeRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.expert_intake(user, appointment_id, request.state.request_id)
