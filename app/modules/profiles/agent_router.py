from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import optional_idempotency_key, require_agent_runtime_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import ServiceClient
from ..records.repository import RecordsRepository
from ..records.service import RecordsService
from .agent_contracts import AgentBusinessActionResult, AgentProfileUpdateApply
from .agent_service import AgentProfileUpdateService
from .lactation_context import LactationContextService
from .lactation_context_schema import MaternalInfantProfileReadOutput
from .repository import ProfileRepository
from .service import ProfileService


router = SurfaceAPIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="profiles",
        clients=["agent-runtime"],
    ),
)


def get_agent_profile_read_service(
    session: AsyncSession = Depends(get_session),
) -> LactationContextService:
    return LactationContextService(
        profile_repository=ProfileRepository(session),
        records_service=RecordsService(repository=RecordsRepository(session)),
    )


def get_agent_profile_update_service(
    session: AsyncSession = Depends(get_session),
) -> AgentProfileUpdateService:
    audit_repository = AuditRepository(session)
    profile_repository = ProfileRepository(session)
    return AgentProfileUpdateService(
        profile_service=ProfileService(
            repository=profile_repository,
        ),
        lactation_context_service=LactationContextService(
            profile_repository=profile_repository,
            records_service=RecordsService(repository=RecordsRepository(session)),
        ),
        idempotency_service=IdempotencyService(repository=audit_repository),
        audit_service=AuditService(repository=audit_repository),
    )


@router.get("/profile", response_model=MaternalInfantProfileReadOutput)
async def read_agent_profile(
    actor_user_id: UUID,
    infant_scope: Literal["current_delivery", "all"] = Query(default="current_delivery"),
    as_of_date: date | None = None,
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: LactationContextService = Depends(get_agent_profile_read_service),
) -> MaternalInfantProfileReadOutput:
    result = await service.read(
        owner_user_id=actor_user_id,
        as_of_date=as_of_date,
        infant_scope=infant_scope,
    )
    return MaternalInfantProfileReadOutput.model_validate(result)


@router.post(
    "/actions/profile.update/apply",
    response_model=AgentBusinessActionResult,
)
async def apply_agent_profile_update(
    payload: AgentProfileUpdateApply,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentProfileUpdateService = Depends(get_agent_profile_update_service),
) -> AgentBusinessActionResult:
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
        payload=payload.payload.model_dump(mode="json", exclude_unset=True),
        idempotency_key=idempotency_key,
        action_id=payload.action_id,
        run_id=payload.run_id,
        actor_service=service_client.name,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return AgentBusinessActionResult.model_validate(
        {
            "status": "applied",
            "action_id": payload.action_id,
            "resource_type": result.resource_type,
            "resource_id": result.resource_id,
            "details": result.details,
        }
    )
