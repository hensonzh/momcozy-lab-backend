from __future__ import annotations

from datetime import date, timedelta
from typing import Any
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
from ..records.repository import RecordsRepository
from ..records.service import RecordsService
from .agent_contracts import (
    AgentPlanDetail,
    AgentPlansActionRequest,
    AgentPlansApplyResponse,
    AgentPlansCalendarReadResponse,
    AgentPlansCurrentReadResponse,
    AgentPlanSummary,
    AgentPlanTaskSummary,
)
from .agent_service import AgentPlansActionService
from .repository import PlansRepository
from .schedule_domain import SCHEDULE_DOMAIN_ORDER
from .schedule_timeline import ScheduleTimelineService
from .schedule_timeline_schema import ScheduleTimelineReadOutput
from .service import PlansService


router = SurfaceAPIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="plans",
        clients=["agent-runtime"],
    ),
)


def get_agent_plans_read_service(
    session: AsyncSession = Depends(get_session),
) -> PlansService:
    return PlansService(repository=PlansRepository(session))


def get_agent_plans_action_service(
    session: AsyncSession = Depends(get_session),
) -> AgentPlansActionService:
    audit_repository = AuditRepository(session)
    return AgentPlansActionService(
        plans_service=PlansService(repository=PlansRepository(session)),
        idempotency_service=IdempotencyService(repository=audit_repository),
        audit_service=AuditService(repository=audit_repository),
    )


def get_agent_schedule_timeline_service(
    session: AsyncSession = Depends(get_session),
) -> ScheduleTimelineService:
    return ScheduleTimelineService(
        records_service=RecordsService(
            repository=RecordsRepository(session),
        ),
        plans_service=PlansService(
            repository=PlansRepository(session),
        ),
    )


@router.get(
    "/plans/current",
    response_model=AgentPlansCurrentReadResponse,
)
async def read_agent_current_plans(
    actor_user_id: UUID,
    plan_type: str | None = Query(
        default=None,
        min_length=1,
        max_length=64,
    ),
    limit: int = Query(default=5, ge=1, le=20),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: PlansService = Depends(get_agent_plans_read_service),
) -> AgentPlansCurrentReadResponse:
    plans = await service.list_plans(
        owner_user_id=actor_user_id,
        plan_type=plan_type or "",
        status="active",
        limit=limit,
    )
    tasks = await service.list_tasks(
        owner_user_id=actor_user_id,
        plan_type=plan_type or "",
        limit=limit,
    )
    return AgentPlansCurrentReadResponse(
        plans=[_plan_summary(plan) for plan in plans],
        tasks=[_task_summary(task) for task in tasks],
        counts={"plans": len(plans), "tasks": len(tasks)},
    )


@router.get(
    "/plans/calendar",
    response_model=AgentPlansCalendarReadResponse,
)
async def read_agent_plan_calendar(
    actor_user_id: UUID,
    task_date: date | None = None,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        max_length=32,
    ),
    limit: int = Query(default=10, ge=1, le=50),
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: PlansService = Depends(get_agent_plans_read_service),
) -> AgentPlansCalendarReadResponse:
    tasks = await service.list_tasks(
        owner_user_id=actor_user_id,
        task_date=task_date,
        status=status_filter,
        limit=limit,
    )
    return AgentPlansCalendarReadResponse(
        tasks=[_task_summary(task) for task in tasks],
        count=len(tasks),
        filters={
            "task_date": task_date.isoformat() if task_date else "",
            "status": status_filter or "",
            "limit": limit,
        },
    )


@router.get(
    "/plans/{plan_id}",
    response_model=AgentPlanDetail,
)
async def read_agent_plan_detail(
    plan_id: UUID,
    actor_user_id: UUID,
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: PlansService = Depends(get_agent_plans_read_service),
) -> AgentPlanDetail:
    plan = await service.get_plan(
        owner_user_id=actor_user_id,
        plan_id=plan_id,
    )
    return AgentPlanDetail(
        **_plan_summary(plan).model_dump(),
        payload=dict(plan.payload or {}),
    )


@router.get(
    "/schedule-timeline",
    response_model=ScheduleTimelineReadOutput,
)
async def read_agent_schedule_timeline(
    actor_user_id: UUID,
    as_of_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    timezone_name: str = Query(default="UTC", min_length=1, max_length=64),
    domains: list[str] | None = Query(default=None),
    states: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1_000),
    include_executions: bool = True,
    _service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: ScheduleTimelineService = Depends(
        get_agent_schedule_timeline_service
    ),
) -> ScheduleTimelineReadOutput:
    effective_as_of_date = as_of_date or date.today()
    return await service.read(
        owner_user_id=actor_user_id,
        as_of_date=effective_as_of_date,
        start_date=start_date or effective_as_of_date - timedelta(days=7),
        end_date=end_date or effective_as_of_date + timedelta(days=7),
        timezone_name=timezone_name,
        limit=limit,
        domains=tuple(domains) if domains else SCHEDULE_DOMAIN_ORDER,
        states=tuple(states or ()),
        include_executions=include_executions,
    )


@router.post(
    "/actions/plans/apply",
    response_model=AgentPlansApplyResponse,
)
async def apply_agent_plans_action(
    payload: AgentPlansActionRequest,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    service_client: ServiceClient = Depends(require_agent_runtime_client),
    service: AgentPlansActionService = Depends(
        get_agent_plans_action_service
    ),
) -> AgentPlansApplyResponse:
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
        command=payload,
        idempotency_key=idempotency_key,
        actor_service=service_client.name,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return AgentPlansApplyResponse(
        status="applied",
        action_id=payload.action_id,
        resource_type=result.resource_type,
        resource_id=result.resource_id,
        details=result.details,
        application_events=list(result.application_events),
    )


def _plan_summary(plan: Any) -> AgentPlanSummary:
    summary = str(getattr(plan, "summary", "") or "").strip()
    if len(summary) > 500:
        summary = summary[:500].rstrip() + "..."
    return AgentPlanSummary(
        id=plan.id,
        plan_type=str(plan.plan_type or ""),
        title=str(plan.title or ""),
        summary=summary,
        status=str(plan.status or ""),
        source=str(plan.source or ""),
        starts_on=plan.starts_on,
        ends_on=plan.ends_on,
        version=int(plan.version),
        updated_at=plan.updated_at,
    )


def _task_summary(task: Any) -> AgentPlanTaskSummary:
    return AgentPlanTaskSummary.model_validate(task)
