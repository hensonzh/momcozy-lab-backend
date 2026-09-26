from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import optional_idempotency_key, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import PlansRepository
from .schemas import (
    PlanCreate,
    PlanListResponse,
    PlanRead,
    PlanTaskCompletionUpdate,
    PlanTaskCreate,
    PlanTaskListResponse,
    PlanTaskRead,
    PlanTaskStateUpdate,
    PlanTaskUpdate,
)
from .service import PlansService


router = SurfaceAPIRouter(
    prefix="/plans",
    tags=["plans"],
    api_surface_metadata=api_surface(
        "deprecated_api",
        owner="plans",
        clients=["legacy-flutter"],
        stability="deprecated",
        notes="Use /v1/schedule for personal schedule entries.",
    ),
)


def get_plans_service(session: AsyncSession = Depends(get_session)) -> PlansService:
    audit_repository = AuditRepository(session)
    return PlansService(
        repository=PlansRepository(session),
        audit_service=AuditService(repository=audit_repository),
        idempotency_service=IdempotencyService(repository=audit_repository),
    )


@router.post("", response_model=PlanRead, status_code=status.HTTP_201_CREATED)
async def create_plan(
    payload: PlanCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> PlanRead:
    plan = await service.create_plan(
        owner_user_id=current_user.user_id,
        plan_type=payload.plan_type or "",
        title=payload.title,
        summary=payload.summary or "",
        source=payload.source or "manual",
        payload=payload.payload,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return PlanRead.model_validate(plan)


@router.get("", response_model=PlanListResponse)
async def list_plans(
    plan_type: str = Query(default="", max_length=64),
    status_filter: str = Query(default="active", alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> PlanListResponse:
    plans = await service.list_plans(
        owner_user_id=current_user.user_id,
        plan_type=plan_type,
        status=status_filter,
        limit=limit,
    )
    return PlanListResponse(items=[PlanRead.model_validate(plan) for plan in plans])


@router.get("/{plan_id}", response_model=PlanRead)
async def get_plan(
    plan_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> PlanRead:
    plan = await service.get_plan(owner_user_id=current_user.user_id, plan_id=plan_id)
    return PlanRead.model_validate(plan)


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> Response:
    await service.delete_plan(
        owner_user_id=current_user.user_id,
        plan_id=plan_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)




@router.post("/tasks", response_model=PlanTaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: PlanTaskCreate,
    request: Request,
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> PlanTaskRead:
    task = await service.create_task(
        owner_user_id=current_user.user_id,
        plan_id=payload.plan_id,
        task_date=payload.task_date,
        task_time=payload.task_time or "",
        title=payload.title,
        description=payload.description or "",
        payload=payload.payload,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key,
    )
    return PlanTaskRead.model_validate(task)


@router.get("/tasks/list", response_model=PlanTaskListResponse)
async def list_tasks(
    task_date: date | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> PlanTaskListResponse:
    tasks = await service.list_tasks(
        owner_user_id=current_user.user_id,
        task_date=task_date,
        status=status_filter,
        limit=limit,
    )
    return PlanTaskListResponse(items=[PlanTaskRead.model_validate(task) for task in tasks])


@router.patch("/tasks/{task_id}", response_model=PlanTaskRead)
async def update_task(
    task_id: UUID,
    payload: PlanTaskUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> PlanTaskRead:
    task = await service.update_task(
        owner_user_id=current_user.user_id,
        task_id=task_id,
        updates=payload.model_dump(exclude_unset=True),
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return PlanTaskRead.model_validate(task)


@router.patch("/tasks/{task_id}/completion", response_model=PlanTaskRead)
async def update_task_completion(
    task_id: UUID,
    payload: PlanTaskCompletionUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> PlanTaskRead:
    task = await service.set_task_completed(
        owner_user_id=current_user.user_id,
        task_id=task_id,
        completed=payload.completed,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return PlanTaskRead.model_validate(task)


@router.patch("/tasks/{task_id}/state", response_model=PlanTaskRead)
async def update_task_state(
    task_id: UUID,
    payload: PlanTaskStateUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> PlanTaskRead:
    task = await service.set_task_state(
        owner_user_id=current_user.user_id,
        task_id=task_id,
        state=payload.state,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return PlanTaskRead.model_validate(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: PlansService = Depends(get_plans_service),
) -> Response:
    await service.delete_task(
        owner_user_id=current_user.user_id,
        task_id=task_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
