from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import (
    optional_idempotency_key,
    require_agent_runtime_client,
)
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import ServiceClient
from .agent_contracts import (
    AgentSupportTicketApplyRequest,
    AgentSupportTicketApplyResponse,
)
from .agent_service import AgentSupportTicketActionService
from .repository import SupportTicketsRepository


router = SurfaceAPIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="support",
        clients=["agent-runtime"],
    ),
)


def get_agent_support_action_service(
    session: AsyncSession = Depends(get_session),
) -> AgentSupportTicketActionService:
    audit_repository = AuditRepository(session)
    return AgentSupportTicketActionService(
        repository=SupportTicketsRepository(session),
        idempotency_service=IdempotencyService(
            repository=audit_repository
        ),
        audit_service=AuditService(repository=audit_repository),
    )


@router.post(
    "/actions/support.ticket/apply",
    response_model=AgentSupportTicketApplyResponse,
)
async def apply_agent_support_ticket_action(
    payload: AgentSupportTicketApplyRequest,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentSupportTicketActionService = Depends(
        get_agent_support_action_service
    ),
) -> AgentSupportTicketApplyResponse:
    if idempotency_key is None:
        raise ApiError(
            code="validation_failed",
            message="Idempotency-Key is required.",
            status=422,
        )
    if idempotency_key != f"agent-action:{payload.action_id}":
        raise ApiError(
            code="validation_failed",
            message="Idempotency-Key must be bound to action_id.",
            status=422,
        )
    result = await service.apply_idempotent(
        owner_user_id=payload.actor_user_id,
        payload=payload.payload,
        idempotency_key=idempotency_key,
        action_id=payload.action_id,
        run_id=payload.run_id,
        actor_service=service_client.name,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return AgentSupportTicketApplyResponse(
        status="applied",
        action_id=payload.action_id,
        resource_type="support_ticket",
        resource_id=result.resource_id,
        details=result.details,
        application_events=list(result.application_events),
    )
