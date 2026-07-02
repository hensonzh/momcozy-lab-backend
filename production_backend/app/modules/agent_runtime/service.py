from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import IdempotencyService, request_hash
from .models import AgentEvent, AgentRun, AgentThread
from .repository import AgentRuntimeRepository


AGENT_RUN_CREATE_IDEMPOTENCY_SCOPE = "agent.runs.create"
DEFAULT_RUNTIME_PATTERN = "langgraph_sdk"
DEFAULT_GRAPH_VERSION = "momcozy-agent-v1"
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "expired"}


class AgentRuntimeService:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.idempotency_service = idempotency_service

    async def create_thread(
        self,
        *,
        owner_user_id: UUID,
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AgentThread:
        return await self.repository.create_thread(
            owner_user_id=owner_user_id,
            title=_normalize_text(title, max_length=255),
            metadata=metadata or {},
        )

    async def list_threads(self, *, owner_user_id: UUID, limit: int = 50) -> list[AgentThread]:
        _validate_limit(limit)
        return await self.repository.list_threads_for_owner(owner_user_id=owner_user_id, limit=limit)

    async def get_thread(self, *, owner_user_id: UUID, thread_id: UUID) -> AgentThread:
        thread = await self.repository.get_thread_for_owner(thread_id=thread_id, owner_user_id=owner_user_id)
        if thread is None:
            raise ApiError(code="not_found", message="Agent thread not found.", status=404)
        return thread

    async def create_run(
        self,
        *,
        actor_user_id: UUID,
        thread_id: UUID | None,
        message: str,
        attachments: list[dict[str, Any]] | None = None,
        runtime_pattern: str | None = None,
        graph_version: str | None = None,
        prompt_version: str | None = None,
        request_id: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> AgentRun:
        normalized_runtime_pattern = runtime_pattern or DEFAULT_RUNTIME_PATTERN
        if normalized_runtime_pattern != DEFAULT_RUNTIME_PATTERN:
            raise ApiError(code="validation_failed", message="Only langgraph_sdk runtime is supported.", status=422)
        normalized_message = _normalize_text(message, max_length=8000, required=True)
        safe_attachments = attachments or []
        idempotency_record = await self._reserve_run_idempotency(
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "thread_id": str(thread_id or ""),
                "message": normalized_message,
                "attachments": safe_attachments,
                "runtime_pattern": normalized_runtime_pattern,
                "graph_version": graph_version or DEFAULT_GRAPH_VERSION,
                "prompt_version": prompt_version or "",
            },
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_run(owner_user_id=actor_user_id, response_ref=idempotency_record.response_ref)

        thread = await self._get_or_create_thread(actor_user_id=actor_user_id, thread_id=thread_id, title=_title_from_message(normalized_message))
        run = await self.repository.create_run(
            thread_id=thread.id,
            actor_user_id=actor_user_id,
            runtime_pattern=normalized_runtime_pattern,
            graph_version=graph_version or DEFAULT_GRAPH_VERSION,
            prompt_version=prompt_version or "",
            request_id=request_id,
            trace_id=trace_id,
        )
        message_record = await self.repository.create_message(
            thread_id=thread.id,
            run_id=run.id,
            role="user",
            message_type="text",
            content={"text": normalized_message, "attachments": safe_attachments},
            status="completed",
        )
        await self.repository.append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="run.queued",
            payload={"thread_id": str(thread.id), "message_id": str(message_record.id)},
        )
        await self.repository.append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="message.completed",
            payload={"message_id": str(message_record.id), "role": "user"},
        )
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(run.id))
        return run

    async def get_run(self, *, owner_user_id: UUID, run_id: UUID) -> AgentRun:
        run = await self.repository.get_run_for_owner(run_id=run_id, owner_user_id=owner_user_id)
        if run is None:
            raise ApiError(code="not_found", message="Agent run not found.", status=404)
        return run

    async def cancel_run(self, *, owner_user_id: UUID, run_id: UUID, reason: str = "") -> AgentRun:
        run = await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        cancelled = await self.repository.mark_run_cancelled(
            run=run,
            cancelled_at=_utcnow(),
            error_code="cancelled_by_user",
        )
        await self.repository.append_event(
            thread_id=cancelled.thread_id,
            run_id=cancelled.id,
            event_type="run.cancelled",
            payload={"reason": _normalize_text(reason, max_length=500)},
        )
        return cancelled

    async def list_events(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[AgentEvent]:
        if after_sequence < 0:
            raise ApiError(code="validation_failed", message="after_sequence must be non-negative.", status=422)
        _validate_limit(limit, max_limit=500)
        await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        return await self.repository.list_events_for_owner(
            run_id=run_id,
            owner_user_id=owner_user_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def _get_or_create_thread(self, *, actor_user_id: UUID, thread_id: UUID | None, title: str) -> AgentThread:
        if thread_id is None:
            return await self.repository.create_thread(owner_user_id=actor_user_id, title=title, metadata={})
        thread = await self.repository.get_thread_for_owner(thread_id=thread_id, owner_user_id=actor_user_id)
        if thread is None:
            raise ApiError(code="not_found", message="Agent thread not found.", status=404)
        return thread

    async def _reserve_run_idempotency(self, *, actor_user_id: UUID, key: str | None, payload: dict[str, Any]):
        if not key:
            return None
        if self.idempotency_service is None:
            raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
        decision = await self.idempotency_service.reserve(
            actor_user_id=actor_user_id,
            scope=AGENT_RUN_CREATE_IDEMPOTENCY_SCOPE,
            key=key,
            request_hash=request_hash(payload),
            expires_at=_utcnow() + timedelta(hours=24),
        )
        if decision.status == "replay" and not decision.record.response_ref:
            raise ApiError(code="idempotency_in_progress", message="Request is still in progress.", status=409)
        return decision.record

    async def _complete_idempotency(self, *, idempotency_record, response_ref: str) -> None:
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=response_ref)

    async def _replay_run(self, *, owner_user_id: UUID, response_ref: str) -> AgentRun:
        run = await self.repository.get_run_for_owner(run_id=UUID(response_ref), owner_user_id=owner_user_id)
        if run is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return run


def _normalize_text(value: str | None, *, max_length: int, required: bool = False) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ApiError(code="validation_failed", message="value is required.", status=422)
    if len(normalized) > max_length:
        raise ApiError(code="validation_failed", message="value is too long.", status=422)
    return normalized


def _title_from_message(message: str) -> str:
    return message[:80]


def _validate_limit(limit: int, *, max_limit: int = 100) -> None:
    if limit < 1 or limit > max_limit:
        raise ApiError(code="validation_failed", message=f"limit must be between 1 and {max_limit}.", status=422)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
