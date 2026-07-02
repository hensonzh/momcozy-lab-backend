from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import normalize_idempotency_key, optional_idempotency_key, require_current_user, require_service_client
from ...infrastructure.db import get_session
from ..audit import IdempotencyService, OutboxService
from ..audit.repository import AuditRepository, OutboxRepository
from ..auth import CurrentUser, ServiceClient
from .evals import AgentEvalService
from .repository import AgentRuntimeRepository
from .replay import AgentReplayService
from .schemas import (
    AgentActionConfirm,
    AgentEvalCaseCreate,
    AgentEvalCaseRead,
    AgentActionRead,
    AgentActionReject,
    AgentEventPage,
    AgentEventRead,
    AgentReplayBundle,
    AgentRunCancel,
    AgentRunCreate,
    AgentRunRead,
    AgentThreadCreate,
    AgentThreadListResponse,
    AgentThreadRead,
)
from .service import AgentRuntimeService
from .controls import AgentRunControls
from .safety import AgentSafetyService
from .streaming import encode_sse_events


router = APIRouter(prefix="/agent", tags=["agent"])


def get_agent_runtime_service(request: Request, session: AsyncSession = Depends(get_session)) -> AgentRuntimeService:
    repository = AgentRuntimeRepository(session)
    audit_repository = AuditRepository(session)
    return AgentRuntimeService(
        repository=repository,
        idempotency_service=IdempotencyService(repository=audit_repository),
        outbox_service=OutboxService(repository=OutboxRepository(session)),
        controls=AgentRunControls(request.app.state.redis_client),
        safety_service=AgentSafetyService(repository=repository),
    )


def get_agent_replay_service(session: AsyncSession = Depends(get_session)) -> AgentReplayService:
    return AgentReplayService(repository=AgentRuntimeRepository(session))


def get_agent_eval_service(session: AsyncSession = Depends(get_session)) -> AgentEvalService:
    repository = AgentRuntimeRepository(session)
    return AgentEvalService(repository=repository)


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
    idempotency_key: str | None = Depends(optional_idempotency_key),
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
        idempotency_key=idempotency_key or normalize_idempotency_key(payload.idempotency_key),
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


@router.get("/runs/{run_id}/stream")
async def stream_run_events(
    run_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Response:
    events = await service.list_events(
        owner_user_id=current_user.user_id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return StreamingResponse(
        iter([encode_sse_events(events)]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


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
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentActionRead:
    action = await service.confirm_action(
        owner_user_id=current_user.user_id,
        action_id=action_id,
        edited_apply_payload=payload.edited_apply_payload,
        idempotency_key=idempotency_key or normalize_idempotency_key(payload.idempotency_key) or "",
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


@router.get("/admin/runs/{run_id}/replay", response_model=AgentReplayBundle)
async def export_run_replay_bundle(
    run_id: UUID,
    include_message_content: bool = Query(default=False),
    _service_client: ServiceClient = Depends(require_service_client),
    replay_service: AgentReplayService = Depends(get_agent_replay_service),
) -> AgentReplayBundle:
    bundle = await replay_service.export_run_bundle(run_id=run_id, include_message_content=include_message_content)
    return AgentReplayBundle.model_validate(bundle)


@router.post("/admin/runs/{run_id}/eval-cases", response_model=AgentEvalCaseRead, status_code=status.HTTP_201_CREATED)
async def create_eval_case_from_run(
    run_id: UUID,
    payload: AgentEvalCaseCreate,
    _service_client: ServiceClient = Depends(require_service_client),
    eval_service: AgentEvalService = Depends(get_agent_eval_service),
) -> AgentEvalCaseRead:
    eval_case = await eval_service.create_case_from_run(
        run_id=run_id,
        suite=payload.suite,
        name=payload.name,
        domain=payload.domain,
        owner_team=payload.owner_team,
    )
    return AgentEvalCaseRead.model_validate(eval_case)
