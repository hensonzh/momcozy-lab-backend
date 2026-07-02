from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import IdempotencyKey, IdempotencyService, OutboxService, request_hash
from .action_policy import AgentActionPolicy
from .controls import AgentRunControls
from .models import AgentAction, AgentEvent, AgentRun, AgentThread
from .repository import AgentRuntimeRepository
from .safety import AgentSafetyService


AGENT_RUN_CREATE_IDEMPOTENCY_SCOPE = "agent.runs.create"
AGENT_ACTION_APPLY_JOB = "agent.action.apply"
DEFAULT_RUNTIME_PATTERN = "langgraph_sdk"
DEFAULT_GRAPH_VERSION = "momcozy-agent-v1"
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "expired"}


class AgentRuntimeService:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        idempotency_service: IdempotencyService | None = None,
        outbox_service: OutboxService | None = None,
        controls: AgentRunControls | None = None,
        safety_service: AgentSafetyService | None = None,
        action_policy: AgentActionPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.idempotency_service = idempotency_service
        self.outbox_service = outbox_service
        self.controls = controls
        self.safety_service = safety_service
        self.action_policy = action_policy or AgentActionPolicy()

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
        safety_blocked = await self._apply_input_safety_gate(
            owner_user_id=actor_user_id,
            run=run,
            message_record_id=message_record.id,
            text=normalized_message,
        )
        if safety_blocked is not None:
            await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(safety_blocked.id))
            return safety_blocked

        await self._append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="run.queued",
            payload={"thread_id": str(thread.id), "message_id": str(message_record.id)},
        )
        await self._append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="message.completed",
            payload={"message_id": str(message_record.id), "role": "user"},
        )
        if self.controls is not None:
            await self.controls.set_active_run(thread_id=thread.id, run_id=run.id)
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
        if self.controls is not None:
            await self.controls.request_cancel(run_id=run.id)
        cancelled = await self.repository.mark_run_cancelled(
            run=run,
            cancelled_at=_utcnow(),
            error_code="cancelled_by_user",
        )
        await self._append_event(
            thread_id=cancelled.thread_id,
            run_id=cancelled.id,
            event_type="run.cancelled",
            payload={"reason": _normalize_text(reason, max_length=500)},
        )
        if self.controls is not None:
            await self.controls.clear_active_run(thread_id=cancelled.thread_id, run_id=cancelled.id)
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

    async def propose_action(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
        action_type: str,
        target_type: str = "",
        target_id: str = "",
        side_effect_level: str = "medium",
        preview_payload: dict[str, Any] | None = None,
        apply_payload: dict[str, Any] | None = None,
        idempotency_key: str = "",
        expires_at: datetime | None = None,
    ) -> AgentAction:
        run = await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        decision = self.action_policy.validate(
            action_type=_normalize_text(action_type, max_length=120, required=True),
            target_type=_normalize_text(target_type, max_length=120),
            side_effect_level=_normalize_text(side_effect_level, max_length=32),
        )
        action = await self.repository.create_action(
            run_id=run.id,
            actor_user_id=owner_user_id,
            action_type=decision.action_type,
            target_type=decision.target_type,
            target_id=_normalize_text(target_id, max_length=120),
            status="confirmation_required",
            side_effect_level=decision.side_effect_level,
            preview_payload=preview_payload or {},
            apply_payload=apply_payload or {},
            idempotency_key=_normalize_text(idempotency_key, max_length=255),
            expires_at=expires_at,
        )
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="action.confirmation_required",
            payload={
                "action_id": str(action.id),
                "action_type": action.action_type,
                "action_status": action.status,
                "target_type": action.target_type,
                "target_id": action.target_id,
                "side_effect_level": action.side_effect_level,
                "preview_payload": action.preview_payload,
            },
        )
        return action

    async def get_action(self, *, owner_user_id: UUID, action_id: UUID) -> AgentAction:
        action = await self.repository.get_action_for_owner(action_id=action_id, owner_user_id=owner_user_id)
        if action is None:
            raise ApiError(code="not_found", message="Agent action not found.", status=404)
        return action

    async def confirm_action(
        self,
        *,
        owner_user_id: UUID,
        action_id: UUID,
        edited_apply_payload: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> AgentAction:
        action = await self.get_action(owner_user_id=owner_user_id, action_id=action_id)
        if action.status in {"confirmed", "applying", "applied"}:
            return action
        if action.status not in {"proposed", "confirmation_required"}:
            raise ApiError(code="conflict", message="Agent action cannot be confirmed from its current status.", status=409)
        if self.outbox_service is None:
            raise ApiError(code="outbox_not_configured", message="Agent action outbox is not configured.", status=500)
        action_idempotency_key = _normalize_text(idempotency_key, max_length=255) or f"agent-action:{action.id}"
        confirmed = await self.repository.mark_action_confirmed(
            action=action,
            confirmed_at=_utcnow(),
            apply_payload=edited_apply_payload,
            idempotency_key=action_idempotency_key,
        )
        run = await self.get_run(owner_user_id=owner_user_id, run_id=confirmed.run_id)
        outbox_job = await self.outbox_service.enqueue(
            job_type=AGENT_ACTION_APPLY_JOB,
            payload={
                "action_id": str(confirmed.id),
                "run_id": str(run.id),
                "actor_user_id": str(owner_user_id),
                "action_type": confirmed.action_type,
                "target_type": confirmed.target_type,
                "target_id": confirmed.target_id,
                "apply_payload": confirmed.apply_payload,
            },
            idempotency_key=confirmed.idempotency_key,
            action_id=confirmed.id,
            request_id=run.request_id,
            trace_id=run.trace_id,
        )
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="action.queued",
            payload={
                "action_id": str(confirmed.id),
                "action_status": confirmed.status,
                "action_type": confirmed.action_type,
                "target_type": confirmed.target_type,
                "target_id": confirmed.target_id,
                "outbox_status": outbox_job.status,
                "outbox_job_id": str(outbox_job.id),
            },
        )
        return confirmed

    async def reject_action(self, *, owner_user_id: UUID, action_id: UUID, reason: str = "") -> AgentAction:
        action = await self.get_action(owner_user_id=owner_user_id, action_id=action_id)
        if action.status == "rejected":
            return action
        if action.status in {"applying", "applied"}:
            raise ApiError(code="conflict", message="Agent action cannot be rejected from its current status.", status=409)
        rejected = await self.repository.mark_action_rejected(action=action, failed_at=_utcnow(), error_code="rejected_by_user")
        run = await self.get_run(owner_user_id=owner_user_id, run_id=rejected.run_id)
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="action.rejected",
            payload={
                "action_id": str(rejected.id),
                "action_status": rejected.status,
                "action_type": rejected.action_type,
                "target_type": rejected.target_type,
                "target_id": rejected.target_id,
                "reason": _normalize_text(reason, max_length=500),
            },
        )
        return rejected

    async def _get_or_create_thread(self, *, actor_user_id: UUID, thread_id: UUID | None, title: str) -> AgentThread:
        if thread_id is None:
            return await self.repository.create_thread(owner_user_id=actor_user_id, title=title, metadata={})
        thread = await self.repository.get_thread_for_owner(thread_id=thread_id, owner_user_id=actor_user_id)
        if thread is None:
            raise ApiError(code="not_found", message="Agent thread not found.", status=404)
        return thread

    async def _append_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict[str, Any]) -> AgentEvent:
        event = await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
        if self.controls is not None:
            await self.controls.set_stream_cursor(run_id=run_id, sequence=event.sequence)
        return event

    async def _apply_input_safety_gate(
        self,
        *,
        owner_user_id: UUID,
        run: AgentRun,
        message_record_id: UUID,
        text: str,
    ) -> AgentRun | None:
        if self.safety_service is None:
            return None
        decision, safety_event = await self.safety_service.evaluate_and_record(owner_user_id=owner_user_id, text=text, run_id=run.id)
        if not decision.should_block_normal_flow:
            return None
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="message.completed",
            payload={"message_id": str(message_record_id), "role": "user"},
        )
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="safety.blocked",
            payload={
                "category": decision.category,
                "severity": decision.severity,
                "decision": decision.decision,
                "safety_event_id": str(safety_event.id) if safety_event else "",
            },
        )
        failed = await self.repository.mark_run_failed(
            run=run,
            completed_at=_utcnow(),
            error_code=decision.category,
            error_details={"decision": decision.decision, "severity": decision.severity},
        )
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="run.failed",
            payload={"code": decision.category, "decision": decision.decision},
        )
        return failed

    async def _reserve_run_idempotency(self, *, actor_user_id: UUID, key: str | None, payload: dict[str, Any]) -> IdempotencyKey | None:
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

    async def _complete_idempotency(self, *, idempotency_record: IdempotencyKey | None, response_ref: str) -> None:
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
