from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import Depends, Query, Request
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
from ..baby.repository import BabyRecordRepository
from .agent_contracts import (
    AgentLactationRecordApplyRequest,
    AgentLactationRecordApplyResponse,
    AgentMilkAnalysisSnapshot,
)
from .agent_service import (
    AgentLactationReadService,
    AgentLactationRecordWriteService,
)
from .repository import RecordsRepository
from .service import RecordsService


router = SurfaceAPIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="records",
        clients=["agent-runtime"],
    ),
)


def get_agent_lactation_read_service(
    session: AsyncSession = Depends(get_session),
) -> AgentLactationReadService:
    records_service = RecordsService(repository=RecordsRepository(session))
    return AgentLactationReadService(
        records_service=records_service,
        baby_repository=BabyRecordRepository(session),
    )


def get_agent_lactation_write_service(
    session: AsyncSession = Depends(get_session),
) -> AgentLactationRecordWriteService:
    audit_repository = AuditRepository(session)
    return AgentLactationRecordWriteService(
        records_service=RecordsService(
            repository=RecordsRepository(session),
        ),
        idempotency_service=IdempotencyService(
            repository=audit_repository,
        ),
        audit_service=AuditService(repository=audit_repository),
    )


@router.get(
    "/lactation/milk-analysis-snapshot",
    response_model=AgentMilkAnalysisSnapshot,
)
async def read_agent_milk_analysis_snapshot(
    actor_user_id: UUID,
    as_of_date: date | None = None,
    timezone_name: str = Query(default="UTC", min_length=1, max_length=64),
    days: int = Query(default=7, ge=1, le=30),
    limit: int = Query(default=8, ge=1, le=20),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentLactationReadService = Depends(
        get_agent_lactation_read_service
    ),
) -> AgentMilkAnalysisSnapshot:
    result = await service.read_milk_analysis_snapshot(
        owner_user_id=actor_user_id,
        as_of_date=as_of_date or date.today(),
        timezone_name=timezone_name,
        days=days,
        detail_limit=limit,
    )
    return AgentMilkAnalysisSnapshot.model_validate(result)


@router.post(
    "/actions/lactation.record/apply",
    response_model=AgentLactationRecordApplyResponse,
)
async def apply_agent_lactation_record(
    payload: AgentLactationRecordApplyRequest,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentLactationRecordWriteService = Depends(
        get_agent_lactation_write_service
    ),
) -> AgentLactationRecordApplyResponse:
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
    return AgentLactationRecordApplyResponse(
        status="applied",
        action_id=payload.action_id,
        resource_type=result.resource_type,  # type: ignore[arg-type]
        resource_id=result.resource_id,
        details=result.details,
        application_events=list(result.application_events),
    )
