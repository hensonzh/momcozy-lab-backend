from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..appointments.router import get_appointment_service
from ..appointments.schemas import VersionWrite
from ..appointments.service import AppointmentService
from ..audit.repository import AuditRepository
from ..audit.service import AuditService, IdempotencyService
from ..auth import CurrentUser
from .repository import DocumentationRepository
from .schemas import (DocumentationRead, NoteAmendWrite, NoteRead, NoteVersionWrite, NoteWrite, PatientPlanRead,
    PlanDraftRead, PlanPublicationRead, PlanWrite, TaskProgressWrite)
from .service import DocumentationService

router = SurfaceAPIRouter(prefix="/care", tags=["care-documentation"],
    api_surface_metadata=api_surface("public_app_api", owner="care", clients=["flutter", "ibclc"]))
MutationKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


def get_documentation_service(session: AsyncSession = Depends(get_session), appointments: AppointmentService = Depends(get_appointment_service)) -> DocumentationService:
    audit = AuditRepository(session)
    return DocumentationService(DocumentationRepository(session), appointments, AuditService(repository=audit), IdempotencyService(repository=audit))


@router.get("/appointments/{appointment_id}/documentation", response_model=DocumentationRead)
async def documentation(appointment_id: UUID, request: Request, user: CurrentUser = Depends(require_current_user),
    service: DocumentationService = Depends(get_documentation_service)) -> DocumentationRead:
    return await service.read(user, appointment_id, request.state.request_id)


@router.get("/appointments/{appointment_id}/notes/{note_id}", response_model=NoteRead)
async def note_revision(appointment_id: UUID, note_id: UUID, request: Request, user: CurrentUser = Depends(require_current_user),
    service: DocumentationService = Depends(get_documentation_service)) -> NoteRead:
    return await service.note_revision(user, appointment_id, note_id, request.state.request_id)


@router.put("/appointments/{appointment_id}/note", response_model=NoteRead)
async def save_note(appointment_id: UUID, body: NoteWrite, request: Request, idempotency_key: MutationKey,
    user: CurrentUser = Depends(require_current_user), service: DocumentationService = Depends(get_documentation_service)) -> NoteRead:
    return await service.save_note(user, appointment_id, body, idempotency_key, request.state.request_id)


@router.post("/appointments/{appointment_id}/note/sign", response_model=NoteRead)
async def sign_note(appointment_id: UUID, body: NoteVersionWrite, request: Request, idempotency_key: MutationKey,
    user: CurrentUser = Depends(require_current_user), service: DocumentationService = Depends(get_documentation_service)) -> NoteRead:
    return await service.sign_note(user, appointment_id, body, idempotency_key, request.state.request_id)


@router.post("/appointments/{appointment_id}/note/amend", response_model=NoteRead)
async def amend_note(appointment_id: UUID, body: NoteAmendWrite, request: Request, idempotency_key: MutationKey,
    user: CurrentUser = Depends(require_current_user), service: DocumentationService = Depends(get_documentation_service)) -> NoteRead:
    return await service.amend_note(user, appointment_id, body, idempotency_key, request.state.request_id)


@router.put("/appointments/{appointment_id}/plan", response_model=PlanDraftRead)
async def save_plan(appointment_id: UUID, body: PlanWrite, request: Request, idempotency_key: MutationKey,
    user: CurrentUser = Depends(require_current_user), service: DocumentationService = Depends(get_documentation_service)) -> PlanDraftRead:
    return await service.save_plan(user, appointment_id, body, idempotency_key, request.state.request_id)


@router.post("/appointments/{appointment_id}/plan/publish", response_model=PlanPublicationRead)
async def publish_plan(appointment_id: UUID, body: VersionWrite, request: Request, idempotency_key: MutationKey,
    user: CurrentUser = Depends(require_current_user), service: DocumentationService = Depends(get_documentation_service)) -> PlanPublicationRead:
    return await service.publish(user, appointment_id, body, idempotency_key, request.state.request_id)


@router.get("/appointments/{appointment_id}/summary", response_model=PatientPlanRead)
async def patient_summary(appointment_id: UUID, user: CurrentUser = Depends(require_current_user),
    service: DocumentationService = Depends(get_documentation_service)) -> PatientPlanRead:
    return await service.patient_plan(user.user_id, appointment_id)


@router.put("/plan-publications/{publication_id}/tasks/{source_key}", response_model=PlanPublicationRead)
async def update_task(publication_id: UUID, source_key: str, body: TaskProgressWrite, request: Request,
    user: CurrentUser = Depends(require_current_user), service: DocumentationService = Depends(get_documentation_service)) -> PlanPublicationRead:
    return await service.update_task(user.user_id, publication_id, source_key, body, request.state.request_id)
