from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...core.errors import ApiError
from ...infrastructure.db import get_session
from ..audit import IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser
from .repository import AgentRuntimeRepository
from .schemas import (
    AgentActionConfirm,
    AgentActionRead,
    AgentActionReject,
    AgentEventPage,
    AgentEventRead,
    AgentRunCancel,
    AgentRunCreate,
    AgentRunRead,
    AgentThreadCreate,
    AgentThreadListResponse,
    AgentThreadRead,
)
from .service import AgentRuntimeService
from .controls import AgentRunControls


router = APIRouter(prefix="/agent", tags=["agent"])


def get_agent_runtime_service(request: Request, session: AsyncSession = Depends(get_session)) -> AgentRuntimeService:
    return AgentRuntimeService(
        repository=AgentRuntimeRepository(session),
        idempotency_service=IdempotencyService(repository=AuditRepository(session)),
        controls=AgentRunControls(request.app.state.redis_client),
    )


@router.post("/threads", response_model=AgentThreadRead, status_code=status.HTTP_201_CREATED)
async def create_thread(
    payload: AgentThreadCreate,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentThreadRead:
    thread = await service.create_thread(
        owner_user_id=current_user.user_id,
        title=payload.title or "",
        metadata=payload.metadata,
    )
    return AgentThreadRead.model_validate(thread)


@router.get("/threads", response_model=AgentThreadListResponse)
async def list_threads(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentThreadListResponse:
    threads = await service.list_threads(owner_user_id=current_user.user_id, limit=limit)
    return AgentThreadListResponse(items=[AgentThreadRead.model_validate(thread) for thread in threads])


@router.get("/threads/{thread_id}", response_model=AgentThreadRead)
async def get_thread(
    thread_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentThreadRead:
    thread = await service.get_thread(owner_user_id=current_user.user_id, thread_id=thread_id)
    return AgentThreadRead.model_validate(thread)


@router.post("/runs", response_model=AgentRunRead, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: AgentRunCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentRunRead:
    run = await service.create_run(
        actor_user_id=current_user.user_id,
        thread_id=payload.thread_id,
        message=payload.message,
        attachments=payload.attachments,
        runtime_pattern=payload.runtime_pattern,
        graph_version=payload.graph_version,
        prompt_version=payload.prompt_version,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        trace_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=_normalize_idempotency_key(idempotency_key) or _normalize_idempotency_key(payload.idempotency_key),
    )
    return AgentRunRead.model_validate(run)


@router.get("/runs/{run_id}", response_model=AgentRunRead)
async def get_run(
    run_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentRunRead:
    run = await service.get_run(owner_user_id=current_user.user_id, run_id=run_id)
    return AgentRunRead.model_validate(run)


@router.get("/runs/{run_id}/events", response_model=AgentEventPage)
async def list_run_events(
    run_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentEventPage:
    events = await service.list_events(
        owner_user_id=current_user.user_id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    next_sequence = events[-1].sequence if events else None
    return AgentEventPage(items=[AgentEventRead.model_validate(event) for event in events], next_sequence=next_sequence)


@router.post("/runs/{run_id}/cancel", response_model=AgentRunRead)
async def cancel_run(
    run_id: UUID,
    payload: AgentRunCancel,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentRunRead:
    run = await service.cancel_run(owner_user_id=current_user.user_id, run_id=run_id, reason=payload.reason or "")
    return AgentRunRead.model_validate(run)


@router.get("/actions/{action_id}", response_model=AgentActionRead)
async def get_action(
    action_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentActionRead:
    action = await service.get_action(owner_user_id=current_user.user_id, action_id=action_id)
    return AgentActionRead.model_validate(action)


@router.post("/actions/{action_id}/confirm", response_model=AgentActionRead)
async def confirm_action(
    action_id: UUID,
    payload: AgentActionConfirm,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentActionRead:
    action = await service.confirm_action(
        owner_user_id=current_user.user_id,
        action_id=action_id,
        edited_apply_payload=payload.edited_apply_payload,
        idempotency_key=_normalize_idempotency_key(idempotency_key) or _normalize_idempotency_key(payload.idempotency_key) or "",
    )
    return AgentActionRead.model_validate(action)


@router.post("/actions/{action_id}/reject", response_model=AgentActionRead)
async def reject_action(
    action_id: UUID,
    payload: AgentActionReject,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentActionRead:
    action = await service.reject_action(owner_user_id=current_user.user_id, action_id=action_id, reason=payload.reason or "")
    return AgentActionRead.model_validate(action)


def _normalize_idempotency_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 255:
        raise ApiError(code="validation_failed", message="Idempotency-Key is too long.", status=422)
    return key
