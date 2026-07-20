from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from time import monotonic
from uuid import UUID

from fastapi import Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import normalize_idempotency_key, optional_idempotency_key, require_current_user, require_service_client
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.logging import log_agent_runtime_event
from ...infrastructure.db import get_session
from ..audit import AuditService, IdempotencyService
from ..audit.repository import AuditRepository
from ..auth import CurrentUser, ServiceClient
from ..files.repository import FileRepository
from .evals.service import AgentEvalService
from .facts import AgentFactRepository, AgentFactService
from .memory.service import AgentMemoryRepository, AgentMemoryService
from .repository import AgentRuntimeRepository
from .event_stream.replay import AgentReplayService
from .schemas import (
    AgentActionConfirm,
    AgentEvalCaseCreate,
    AgentEvalCaseRead,
    AgentActionRead,
    AgentActionReject,
    AgentClientEventCreate,
    AgentEventPage,
    AgentEventRead,
    AgentFactListResponse,
    AgentFactRead,
    AgentReplayBundle,
    AgentMemoryListResponse,
    AgentMemoryRead,
    AgentMemorySettingsRead,
    AgentMemorySettingsUpdate,
    AgentRunCancel,
    AgentRunCreate,
    AgentRunRead,
    AgentThreadCreate,
    AgentThreadListResponse,
    AgentThreadRead,
)
from .service import AgentRuntimeService
from .run_lifecycle.controls import AgentRunControls
from .event_stream.sse import encode_sse_events, encode_transient_sse_events
from .event_stream.transient import AgentTransientStream, AgentTransientStreamEvent


router = SurfaceAPIRouter(
    prefix="/agent",
    tags=["agent"],
    api_surface_metadata=api_surface("public_app_api", owner="agent-runtime", clients=["flutter"]),
)

TERMINAL_STREAM_EVENT_TYPES = {"run.completed", "run.failed", "run.cancelled", "run.waiting_for_confirmation"}
DEFAULT_STREAM_POLL_INTERVAL_SECONDS = 0.1
MIN_STREAM_POLL_INTERVAL_SECONDS = 0.01
MIN_PERSISTED_FALLBACK_POLL_INTERVAL_SECONDS = 0.1


def get_agent_runtime_service(request: Request, session: AsyncSession = Depends(get_session)) -> AgentRuntimeService:
    repository = AgentRuntimeRepository(session)
    audit_repository = AuditRepository(session)
    memory_service = _build_agent_memory_service(session)
    fact_service = _build_agent_fact_service(
        request=request,
        session=session,
        audit_repository=audit_repository,
        memory_service=memory_service,
    )
    return AgentRuntimeService(
        repository=repository,
        idempotency_service=IdempotencyService(repository=audit_repository),
        file_repository=FileRepository(session),
        controls=AgentRunControls(request.app.state.redis_client),
        fact_service=fact_service,
        memory_service=memory_service,
    )


def get_agent_replay_service(session: AsyncSession = Depends(get_session)) -> AgentReplayService:
    return AgentReplayService(repository=AgentRuntimeRepository(session))


def get_agent_eval_service(session: AsyncSession = Depends(get_session)) -> AgentEvalService:
    repository = AgentRuntimeRepository(session)
    return AgentEvalService(repository=repository)


def get_agent_memory_service(session: AsyncSession = Depends(get_session)) -> AgentMemoryService:
    return _build_agent_memory_service(session)


def get_agent_fact_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentFactService:
    memory_service = _build_agent_memory_service(session)
    return _build_agent_fact_service(
        request=request,
        session=session,
        audit_repository=AuditRepository(session),
        memory_service=memory_service,
    )


def _build_agent_memory_service(session: AsyncSession) -> AgentMemoryService:
    return AgentMemoryService(repository=AgentMemoryRepository(session))


def _build_agent_fact_service(
    *,
    request: Request,
    session: AsyncSession,
    audit_repository: AuditRepository,
    memory_service: AgentMemoryService,
) -> AgentFactService:
    settings = request.app.state.settings
    return AgentFactService(
        repository=AgentFactRepository(session),
        audit_service=AuditService(repository=audit_repository),
        memory_consent_reader=memory_service,
        extraction_enabled=settings.agent_fact_extraction_enabled,
        extraction_model=settings.agent_fact_extraction_model,
        extraction_version=settings.agent_fact_extraction_version,
        extraction_max_attempts=settings.agent_fact_worker_max_attempts,
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
    idempotency_key: str | None = Depends(optional_idempotency_key),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentRunRead:
    started_at = monotonic()
    run = await service.create_run(
        actor_user_id=current_user.user_id,
        thread_id=payload.thread_id,
        message=payload.message,
        attachments=payload.attachments,
        client_context=payload.client_context,
        runtime_pattern=payload.runtime_pattern,
        runtime_version=payload.runtime_version,
        request_id=str(getattr(request.state, "request_id", "") or ""),
        trace_id=str(getattr(request.state, "request_id", "") or ""),
        idempotency_key=idempotency_key or normalize_idempotency_key(payload.idempotency_key),
    )
    log_agent_runtime_event(
        "agent.run.api_create",
        request_id=str(getattr(request.state, "request_id", "") or ""),
        run_id=str(run.id),
        thread_id=str(run.thread_id),
        status=run.status,
        duration_ms=_elapsed_monotonic_ms(started_at),
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


@router.post("/runs/{run_id}/client-events", response_model=AgentEventRead, status_code=status.HTTP_201_CREATED)
async def record_client_event(
    run_id: UUID,
    payload: AgentClientEventCreate,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> AgentEventRead:
    event = await service.record_client_event(
        owner_user_id=current_user.user_id,
        run_id=run_id,
        client_event_type=payload.type,
        payload=payload.payload,
        client_sequence=payload.client_sequence,
    )
    return AgentEventRead.model_validate(event)


@router.get(
    "/runs/{run_id}/stream",
    openapi_extra=api_surface("runtime_stream_api", owner="agent-runtime", clients=["flutter"]),
)
async def stream_run_events(
    run_id: UUID,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    follow: bool = Query(default=False),
    poll_interval_seconds: float = Query(
        default=DEFAULT_STREAM_POLL_INTERVAL_SECONDS,
        ge=MIN_STREAM_POLL_INTERVAL_SECONDS,
        le=5.0,
    ),
    max_wait_seconds: int = Query(default=30, ge=1, le=300),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Response:
    redis_client = getattr(request.app.state, "redis_client", None)
    transient_stream = AgentTransientStream(redis_client) if redis_client is not None else None
    return StreamingResponse(
        _stream_run_event_chunks(
            service=service,
            owner_user_id=current_user.user_id,
            run_id=run_id,
            after_sequence=after_sequence,
            limit=limit,
            follow=follow,
            poll_interval_seconds=poll_interval_seconds,
            max_wait_seconds=max_wait_seconds,
            is_disconnected=request.is_disconnected,
            transient_stream=transient_stream,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
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


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Response:
    await service.delete_artifact(owner_user_id=current_user.user_id, artifact_id=artifact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.get("/facts", response_model=AgentFactListResponse)
async def list_facts(
    fact_kind: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentFactService = Depends(get_agent_fact_service),
) -> AgentFactListResponse:
    facts = await service.list_facts(
        owner_user_id=current_user.user_id,
        fact_kind=fact_kind,
        limit=limit,
        include_when_disabled=True,
    )
    return AgentFactListResponse(items=[AgentFactRead.model_validate(fact) for fact in facts])


@router.delete("/facts", status_code=status.HTTP_204_NO_CONTENT)
async def clear_facts(
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentFactService = Depends(get_agent_fact_service),
) -> Response:
    await service.clear_facts(
        owner_user_id=current_user.user_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fact(
    fact_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentFactService = Depends(get_agent_fact_service),
) -> Response:
    await service.delete_fact(
        owner_user_id=current_user.user_id,
        fact_id=fact_id,
        request_id=str(getattr(request.state, "request_id", "") or ""),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/memories", response_model=AgentMemoryListResponse)
async def list_memories(
    memory_type: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentMemoryService = Depends(get_agent_memory_service),
) -> AgentMemoryListResponse:
    memories = await service.list_active_memories(
        owner_user_id=current_user.user_id,
        memory_type=memory_type,
        limit=limit,
        include_when_disabled=True,
    )
    return AgentMemoryListResponse(items=[AgentMemoryRead.model_validate(memory) for memory in memories])


@router.get("/memories/settings", response_model=AgentMemorySettingsRead)
async def get_memory_settings(
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentMemoryService = Depends(get_agent_memory_service),
) -> AgentMemorySettingsRead:
    settings = await service.get_settings(owner_user_id=current_user.user_id)
    return AgentMemorySettingsRead.model_validate(settings)


@router.put("/memories/settings", response_model=AgentMemorySettingsRead)
async def update_memory_settings(
    payload: AgentMemorySettingsUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentMemoryService = Depends(get_agent_memory_service),
    fact_service: AgentFactService = Depends(get_agent_fact_service),
) -> AgentMemorySettingsRead:
    settings = await service.update_settings(owner_user_id=current_user.user_id, memory_enabled=payload.memory_enabled)
    if not payload.memory_enabled:
        await fact_service.cancel_pending_extractions(
            owner_user_id=current_user.user_id,
            reason="memory_disabled",
            request_id=str(getattr(request.state, "request_id", "") or ""),
        )
    return AgentMemorySettingsRead.model_validate(settings)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: UUID,
    current_user: CurrentUser = Depends(require_current_user),
    service: AgentMemoryService = Depends(get_agent_memory_service),
) -> Response:
    await service.archive_memory(owner_user_id=current_user.user_id, memory_id=memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/admin/runs/{run_id}/replay",
    response_model=AgentReplayBundle,
    openapi_extra=api_surface("admin_ops_api", owner="agent-runtime", clients=["ops-console", "eval-runner"]),
)
async def export_run_replay_bundle(
    run_id: UUID,
    include_message_content: bool = Query(default=False),
    _service_client: ServiceClient = Depends(require_service_client),
    replay_service: AgentReplayService = Depends(get_agent_replay_service),
) -> AgentReplayBundle:
    bundle = await replay_service.export_run_bundle(run_id=run_id, include_message_content=include_message_content)
    return AgentReplayBundle.model_validate(bundle)


@router.post(
    "/admin/runs/{run_id}/eval-cases",
    response_model=AgentEvalCaseRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=api_surface("admin_ops_api", owner="agent-runtime", clients=["ops-console", "eval-runner"]),
)
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


async def _stream_run_event_chunks(
    *,
    service: AgentRuntimeService,
    owner_user_id: UUID,
    run_id: UUID,
    after_sequence: int,
    limit: int,
    follow: bool,
    poll_interval_seconds: float,
    max_wait_seconds: int,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    transient_stream: AgentTransientStream | None = None,
) -> AsyncIterator[str]:
    stream_started_at = monotonic()
    first_event_ms: float | None = None
    chunks_sent = 0
    events_sent = 0
    persisted_poll_count = 0
    transient_read_count = 0
    end_reason = "unknown"
    cursor = after_sequence
    transient_cursor = "0-0"
    streamed_dedupe_keys: set[str] = set()
    transient_block_ms = _transient_block_ms(poll_interval_seconds)
    persisted_fallback_poll_interval_seconds = _persisted_fallback_poll_interval_seconds(poll_interval_seconds)
    deadline = monotonic() + max_wait_seconds
    next_persisted_poll_at = monotonic()

    def record_stream_chunk(event_count: int) -> None:
        nonlocal chunks_sent, events_sent, first_event_ms
        chunks_sent += 1
        events_sent += event_count
        first_event_ms = _first_event_ms(first_event_ms=first_event_ms, stream_started_at=stream_started_at)

    try:
        while True:
            if is_disconnected is not None and await is_disconnected():
                end_reason = "client_disconnected"
                return
            if monotonic() >= next_persisted_poll_at:
                persisted_poll_count += 1
                events = await service.list_events(
                    owner_user_id=owner_user_id,
                    run_id=run_id,
                    after_sequence=cursor,
                    limit=limit,
                )
                next_persisted_poll_at = monotonic() + persisted_fallback_poll_interval_seconds
                if events:
                    final_event_index = _first_final_event_index(events)
                    if final_event_index > 0:
                        pre_final_candidates = events[:final_event_index]
                        pre_final_events = _filter_events_seen_in_transient_stream(
                            pre_final_candidates,
                            run_id=run_id,
                            dedupe_keys=streamed_dedupe_keys,
                        )
                        streamed_dedupe_keys.update(
                            key for event in pre_final_events if (key := _persisted_event_dedupe_key(event, run_id=run_id))
                        )
                        encoded = encode_sse_events(pre_final_events)
                        if encoded:
                            record_stream_chunk(len(pre_final_events))
                            yield encoded
                        cursor = pre_final_candidates[-1].sequence
                    if final_event_index != -1:
                        transient_read_count += 1
                        transient_events = await _read_transient_events(
                            transient_stream=transient_stream,
                            run_id=run_id,
                            after_cursor=transient_cursor,
                            block_ms=0,
                        )
                        if transient_events:
                            transient_cursor = transient_events[-1].cursor
                            transient_events = _filter_transient_events_seen_in_stream(
                                transient_events,
                                dedupe_keys=streamed_dedupe_keys,
                            )
                            streamed_dedupe_keys.update(key for event in transient_events if (key := _transient_event_dedupe_key(event)))
                            encoded = encode_transient_sse_events(transient_events)
                            if encoded:
                                record_stream_chunk(len(transient_events))
                                yield encoded
                        final_events = events[final_event_index:]
                        visible_final_events = _filter_events_seen_in_transient_stream(
                            final_events,
                            run_id=run_id,
                            dedupe_keys=streamed_dedupe_keys,
                        )
                        streamed_dedupe_keys.update(
                            key for event in visible_final_events if (key := _persisted_event_dedupe_key(event, run_id=run_id))
                        )
                        encoded = encode_sse_events(visible_final_events)
                        if encoded:
                            record_stream_chunk(len(visible_final_events))
                            yield encoded
                        cursor = final_events[-1].sequence
                    elif final_event_index == -1:
                        visible_events = _filter_events_seen_in_transient_stream(
                            events,
                            run_id=run_id,
                            dedupe_keys=streamed_dedupe_keys,
                        )
                        streamed_dedupe_keys.update(
                            key for event in visible_events if (key := _persisted_event_dedupe_key(event, run_id=run_id))
                        )
                        encoded = encode_sse_events(visible_events)
                        if encoded:
                            record_stream_chunk(len(visible_events))
                            yield encoded
                        cursor = events[-1].sequence
                    if any(event.event_type in TERMINAL_STREAM_EVENT_TYPES for event in events):
                        end_reason = "terminal_persisted"
                        return
            if not follow:
                end_reason = "snapshot_complete"
                return
            if monotonic() >= deadline:
                end_reason = "deadline"
                return
            if transient_stream is not None:
                seconds_until_persisted_poll = next_persisted_poll_at - monotonic()
                if seconds_until_persisted_poll <= 0:
                    continue
                block_ms = min(transient_block_ms, max(1, int(seconds_until_persisted_poll * 1000)))
                read_started_at = monotonic()
                transient_read_count += 1
                transient_events = await _read_transient_events(
                    transient_stream=transient_stream,
                    run_id=run_id,
                    after_cursor=transient_cursor,
                    block_ms=block_ms,
                )
                if transient_events:
                    transient_cursor = transient_events[-1].cursor
                    transient_events = _filter_transient_events_seen_in_stream(
                        transient_events,
                        dedupe_keys=streamed_dedupe_keys,
                    )
                    streamed_dedupe_keys.update(key for event in transient_events if (key := _transient_event_dedupe_key(event)))
                    encoded = encode_transient_sse_events(transient_events)
                    if encoded:
                        record_stream_chunk(len(transient_events))
                        yield encoded
                    if _has_terminal_transient_event(transient_events):
                        end_reason = "terminal_transient"
                        return
                    continue
                remaining_block_seconds = (block_ms / 1000) - (monotonic() - read_started_at)
                if remaining_block_seconds > 0:
                    await asyncio.sleep(remaining_block_seconds)
            else:
                sleep_seconds = min(
                    persisted_fallback_poll_interval_seconds,
                    max(0.0, next_persisted_poll_at - monotonic()),
                )
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)
    finally:
        log_agent_runtime_event(
            "agent.run.sse_stream",
            run_id=str(run_id),
            follow=follow,
            after_sequence=after_sequence,
            duration_ms=_elapsed_monotonic_ms(stream_started_at),
            first_event_ms=first_event_ms,
            chunks_sent=chunks_sent,
            events_sent=events_sent,
            persisted_poll_count=persisted_poll_count,
            transient_read_count=transient_read_count,
            end_reason=end_reason,
        )


def _transient_block_ms(poll_interval_seconds: float) -> int:
    return max(1, int(max(poll_interval_seconds, MIN_STREAM_POLL_INTERVAL_SECONDS) * 1000))


def _persisted_fallback_poll_interval_seconds(poll_interval_seconds: float) -> float:
    return max(poll_interval_seconds, MIN_PERSISTED_FALLBACK_POLL_INTERVAL_SECONDS)


def _elapsed_monotonic_ms(started_at: float) -> float:
    return round((monotonic() - started_at) * 1000, 3)


def _first_event_ms(*, first_event_ms: float | None, stream_started_at: float) -> float:
    return first_event_ms if first_event_ms is not None else _elapsed_monotonic_ms(stream_started_at)


def _first_final_event_index(events: Sequence[object]) -> int:
    for index, event in enumerate(events):
        if _is_final_stream_event(event):
            return index
    return -1


def _is_final_stream_event(event: object) -> bool:
    event_type = str(getattr(event, "event_type", "") or "")
    if event_type in TERMINAL_STREAM_EVENT_TYPES:
        return True
    if event_type != "message.completed":
        return False
    payload = getattr(event, "payload", None)
    return isinstance(payload, dict) and payload.get("role") == "assistant"


async def _read_transient_events(
    *,
    transient_stream: AgentTransientStream | None,
    run_id: UUID,
    after_cursor: str,
    block_ms: int,
) -> list[AgentTransientStreamEvent]:
    if transient_stream is None:
        return []
    return await transient_stream.read(
        run_id=run_id,
        after_cursor=after_cursor,
        count=100,
        block_ms=block_ms,
    )


def _filter_events_seen_in_transient_stream(
    events: Sequence[object],
    *,
    run_id: UUID,
    dedupe_keys: set[str],
) -> list[object]:
    if not dedupe_keys:
        return list(events)
    return [event for event in events if _persisted_event_dedupe_key(event, run_id=run_id) not in dedupe_keys]


def _filter_transient_events_seen_in_stream(
    events: list[AgentTransientStreamEvent],
    *,
    dedupe_keys: set[str],
) -> list[AgentTransientStreamEvent]:
    if not dedupe_keys:
        return events
    return [event for event in events if _transient_event_dedupe_key(event) not in dedupe_keys]


def _has_terminal_transient_event(events: list[AgentTransientStreamEvent]) -> bool:
    return any(event.type in TERMINAL_STREAM_EVENT_TYPES for event in events)


def _transient_event_dedupe_key(event: AgentTransientStreamEvent) -> str | None:
    live_semantic = event.payload.get("_live_semantic")
    if isinstance(live_semantic, dict):
        dedupe_key = live_semantic.get("dedupe_key")
        if isinstance(dedupe_key, str) and dedupe_key.strip():
            return dedupe_key
    return _payload_semantic_dedupe_key(
        run_id=event.run_id,
        event_type=event.type,
        payload=event.payload,
    )


def _persisted_event_dedupe_key(event: object, *, run_id: UUID) -> str | None:
    event_type = str(getattr(event, "event_type", "") or "")
    payload = getattr(event, "payload", None)
    if not isinstance(payload, dict):
        return None
    unique_id = payload.get("tool_call_id") or payload.get("action_id") or payload.get("artifact_id") or payload.get("message_id")
    if not isinstance(unique_id, str) or not unique_id.strip():
        if event_type in TERMINAL_STREAM_EVENT_TYPES:
            return f"{run_id}:{event_type}"
        return _payload_semantic_dedupe_key(run_id=run_id, event_type=event_type, payload=payload)
    return f"{run_id}:{event_type}:{unique_id}"


def _payload_semantic_dedupe_key(*, run_id: UUID, event_type: str, payload: dict[str, object]) -> str | None:
    semantic = payload.get("semantic")
    if not isinstance(semantic, dict):
        return None
    merge_key = semantic.get("merge_key")
    if not isinstance(merge_key, str) or not merge_key.strip():
        return None
    return f"{run_id}:{event_type}:{merge_key}"
