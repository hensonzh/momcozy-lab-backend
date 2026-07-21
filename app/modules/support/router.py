from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import optional_idempotency_key, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import SupportTicketsRepository
from .schemas import SupportTicketCreate, SupportTicketListResponse, SupportTicketRead
from .service import SupportTicketsService


router = SurfaceAPIRouter(
    prefix="/support",
    tags=["support"],
    api_surface_metadata=api_surface("public_app_api", owner="support", clients=["flutter"]),
)


def get_support_tickets_service(session: AsyncSession = Depends(get_session)) -> SupportTicketsService:
    audit_repository = AuditRepository(session)
    return SupportTicketsService(
        repository=SupportTicketsRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )


@router.post("/tickets", response_model=SupportTicketRead, status_code=status.HTTP_201_CREATED)
async def create_support_ticket(
    payload: SupportTicketCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: SupportTicketsService = Depends(get_support_tickets_service),
) -> SupportTicketRead:
    if payload.source == "agent_form":
        submit_agent_form = getattr(request.app.state, "support_agent_form_submitter", None)
        if not callable(submit_agent_form):
            raise ApiError(code="agent_runtime_not_configured", message="Agent runtime is unavailable.", status=503)
        ticket = await submit_agent_form(
            payload=payload,
            current_user=current_user,
            service=service,
            idempotency_key=idempotency_key,
        )
        return SupportTicketRead.model_validate(ticket)
    ticket = await service.create_ticket(
        owner_user_id=current_user.user_id,
        issue_type=payload.issue_type or "other",
        issue_summary=payload.issue_summary,
        product_model=payload.product_model or "",
        order_number=payload.order_number or "",
        purchase_channel=payload.purchase_channel or "",
        user_contact=payload.user_contact or "",
        urgency=payload.urgency or "normal",
        source=payload.source or "agent",
        payload=_support_payload(payload),
        metadata=_metadata_from_payload(payload),
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return SupportTicketRead.model_validate(ticket)


@router.get("/tickets", response_model=SupportTicketListResponse)
async def list_support_tickets(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: SupportTicketsService = Depends(get_support_tickets_service),
) -> SupportTicketListResponse:
    tickets = await service.list_tickets(owner_user_id=current_user.user_id, status=status_filter, limit=limit)
    return SupportTicketListResponse(items=[SupportTicketRead.model_validate(ticket) for ticket in tickets])


@router.get("/tickets/{ticket_id}", response_model=SupportTicketRead)
async def get_support_ticket(
    ticket_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: SupportTicketsService = Depends(get_support_tickets_service),
) -> SupportTicketRead:
    ticket = await service.get_ticket(owner_user_id=current_user.user_id, ticket_id=ticket_id)
    return SupportTicketRead.model_validate(ticket)


def _metadata_from_payload(payload: SupportTicketCreate) -> dict[str, Any]:
    metadata = {}
    if payload.thread_id:
        metadata["thread_id"] = payload.thread_id
    if payload.locale:
        metadata["locale"] = payload.locale
    if payload.timezone:
        metadata["timezone"] = payload.timezone
    if payload.message_sent_at:
        metadata["message_sent_at"] = payload.message_sent_at.isoformat()
    return metadata


def _support_payload(payload: SupportTicketCreate) -> dict[str, Any]:
    return dict(payload.payload or {})
