from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ...core.errors import ApiError
from ..audit import IdempotencyKey, IdempotencyService, OutboxService, parse_idempotency_response_ref, request_hash
from .actions.policy import AgentActionPolicy
from .client_context import sanitize_agent_client_context
from .run_lifecycle.controls import AgentRunControls
from .models import AgentAction, AgentArtifact, AgentEvent, AgentRun, AgentThread
from .repository import AgentRuntimeRepository


AGENT_RUN_CREATE_IDEMPOTENCY_SCOPE = "agent.runs.create"
AGENT_ACTION_APPLY_JOB = "agent.action.apply"
DEFAULT_RUNTIME_PATTERN = "langgraph_sdk"
DEFAULT_GRAPH_VERSION = "momcozy-agent-v1"
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "expired"}
MAX_AGENT_RUN_ATTACHMENTS = 20
MAX_FORM_SUBMISSION_BYTES = 16_384


class AgentRuntimeService:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        idempotency_service: IdempotencyService | None = None,
        outbox_service: OutboxService | None = None,
        controls: AgentRunControls | None = None,
        action_policy: AgentActionPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.idempotency_service = idempotency_service
        self.outbox_service = outbox_service
        self.controls = controls
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
        client_context: dict[str, Any] | None = None,
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
        requested_attachments = attachments or []
        safe_client_context = sanitize_agent_client_context(client_context)
        idempotency_record = await self._reserve_run_idempotency(
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "thread_id": str(thread_id or ""),
                "message": normalized_message,
                "attachments": requested_attachments,
                "client_context": safe_client_context,
                "runtime_pattern": normalized_runtime_pattern,
                "graph_version": graph_version or DEFAULT_GRAPH_VERSION,
                "prompt_version": prompt_version or "",
            },
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_run(owner_user_id=actor_user_id, response_ref=idempotency_record.response_ref)

        try:
            safe_attachments = await self._verified_run_attachments(
                actor_user_id=actor_user_id,
                attachments=requested_attachments,
            )
            thread = await self._get_or_create_thread(
                actor_user_id=actor_user_id, thread_id=thread_id, title=_title_from_message(normalized_message)
            )
            await self._ensure_no_active_thread_run(owner_user_id=actor_user_id, thread_id=thread.id)
        except ApiError:
            await self._release_idempotency(idempotency_record=idempotency_record)
            raise
        run = await self.repository.create_run(
            thread_id=thread.id,
            actor_user_id=actor_user_id,
            runtime_pattern=normalized_runtime_pattern,
            graph_version=graph_version or DEFAULT_GRAPH_VERSION,
            prompt_version=prompt_version or "",
            request_id=request_id,
            trace_id=trace_id,
        )
        message_content: dict[str, Any] = {
            "text": normalized_message,
            "attachments": safe_attachments,
        }
        if safe_client_context:
            message_content["client_context"] = safe_client_context
        message_record = await self.repository.create_message(
            thread_id=thread.id,
            run_id=run.id,
            role="user",
            message_type="text",
            content=message_content,
            status="completed",
        )
        await self.repository.touch_thread(thread=thread, updated_at=_utcnow())

        await self._append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="run.queued",
            payload={
                "thread_id": str(thread.id),
                "message_id": str(message_record.id),
                "phase": "queued",
                "label": "我已经收到你的消息啦～",
                "semantic": {
                    "phase": "thinking",
                    "label": "我已经收到你的消息啦～",
                    "surface": "status_bar",
                    "visibility": "status",
                    "merge_key": f"run:{run.id}",
                    "priority": 10,
                    "lifecycle": "running",
                },
            },
        )
        await self._append_event(
            thread_id=thread.id,
            run_id=run.id,
            event_type="message.completed",
            payload={"message_id": str(message_record.id), "role": "user"},
        )
        if self.controls is not None:
            await self.controls.set_active_run(thread_id=thread.id, run_id=run.id)
            self._register_run_queue_wakeup(run_id=run.id)
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(run.id))
        return run

    async def _verified_run_attachments(
        self,
        *,
        actor_user_id: UUID,
        attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if len(attachments) > MAX_AGENT_RUN_ATTACHMENTS:
            raise ApiError(code="validation_failed", message="Too many agent attachments.", status=422)
        verified: list[dict[str, Any]] = []
        for attachment in attachments:
            if str(attachment.get("type") or "").strip() != "form_submission":
                verified.append(dict(attachment))
                continue
            verified.append(
                await self._verify_form_submission_attachment(
                    actor_user_id=actor_user_id,
                    attachment=attachment,
                )
            )
        return verified

    async def _verify_form_submission_attachment(
        self,
        *,
        actor_user_id: UUID,
        attachment: dict[str, Any],
    ) -> dict[str, Any]:
        form_id = _normalize_text(str(attachment.get("form_id") or ""), max_length=120, required=True)
        artifact_id = _parse_uuid(attachment.get("artifact_id"), error_code="invalid_form_submission")
        values = attachment.get("values")
        if not isinstance(values, dict) or not values or len(values) > 100:
            raise ApiError(code="invalid_form_submission", message="Form submission values are invalid.", status=422)
        serialized_values = json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(serialized_values.encode("utf-8")) > MAX_FORM_SUBMISSION_BYTES:
            raise ApiError(code="invalid_form_submission", message="Form submission is too large.", status=422)

        artifact = await self.repository.get_artifact_for_owner(
            artifact_id=artifact_id,
            owner_user_id=actor_user_id,
        )
        artifact_form = artifact.payload.get("form") if artifact is not None and isinstance(artifact.payload, dict) else None
        artifact_form_id = str(artifact_form.get("id") or "").strip() if isinstance(artifact_form, dict) else ""
        if artifact is None or artifact.status == "deleted" or artifact.artifact_type != "form" or artifact_form_id != form_id:
            raise ApiError(code="invalid_form_submission", message="Form submission does not match an active owned form.", status=422)

        submission_id = uuid5(
            NAMESPACE_URL,
            f"momcozy-form-submission:{actor_user_id}:{artifact_id}:{form_id}:{request_hash(values)}",
        )
        return {
            "type": "form_submission",
            "submission_id": str(submission_id),
            "artifact_id": str(artifact_id),
            "form_id": form_id,
            "values": dict(values),
            "verified": True,
        }

    async def get_run(self, *, owner_user_id: UUID, run_id: UUID) -> AgentRun:
        run = await self.repository.get_run_for_owner(run_id=run_id, owner_user_id=owner_user_id)
        if run is None:
            raise ApiError(code="not_found", message="Agent run not found.", status=404)
        return run

    async def cancel_run(self, *, owner_user_id: UUID, run_id: UUID, reason: str = "") -> AgentRun:
        run = await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return run
        previous_status = run.status
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
            if previous_status != "running":
                await self.controls.clear_cancel(run_id=cancelled.id)
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

    async def record_client_event(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
        client_event_type: str,
        payload: dict[str, Any] | None = None,
        client_sequence: int | None = None,
    ) -> AgentEvent:
        run = await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        normalized_client_event_type = _normalize_text(client_event_type, max_length=120, required=True)
        if client_sequence is not None and client_sequence < 0:
            raise ApiError(code="validation_failed", message="client_sequence must be non-negative.", status=422)
        event_payload: dict[str, Any] = {
            "client_event_type": normalized_client_event_type,
            "payload": payload or {},
        }
        if client_sequence is not None:
            event_payload["client_sequence"] = client_sequence
        return await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="client.event",
            payload=event_payload,
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
        action, _ = await self.propose_action_once(
            owner_user_id=owner_user_id,
            run_id=run_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            side_effect_level=side_effect_level,
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            reuse_existing=False,
        )
        return action

    async def propose_action_once(
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
        reuse_existing: bool = True,
    ) -> tuple[AgentAction, bool]:
        run = await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        decision = self.action_policy.validate(
            action_type=_normalize_text(action_type, max_length=120, required=True),
            target_type=_normalize_text(target_type, max_length=120),
            side_effect_level=_normalize_text(side_effect_level, max_length=32),
        )
        if not decision.requires_confirmation and self.outbox_service is None:
            raise ApiError(code="outbox_not_configured", message="Agent action outbox is not configured.", status=500)
        normalized_idempotency_key = _normalize_text(idempotency_key, max_length=255)
        if normalized_idempotency_key and reuse_existing:
            lock = getattr(self.repository, "lock_run_for_action_proposal", None)
            if callable(lock):
                await lock(run_id=run.id)
            find_existing = getattr(self.repository, "get_reusable_action_by_idempotency_key", None)
            if callable(find_existing):
                existing = await find_existing(
                    run_id=run.id,
                    actor_user_id=owner_user_id,
                    action_type=decision.action_type,
                    idempotency_key=normalized_idempotency_key,
                )
                if existing is not None:
                    return existing, False
        initial_status = "confirmation_required" if decision.requires_confirmation else "proposed"
        action = await self.repository.create_action(
            run_id=run.id,
            actor_user_id=owner_user_id,
            action_type=decision.action_type,
            target_type=decision.target_type,
            target_id=_normalize_text(target_id, max_length=120),
            status=initial_status,
            side_effect_level=decision.side_effect_level,
            preview_payload=preview_payload or {},
            apply_payload=apply_payload or {},
            idempotency_key=normalized_idempotency_key,
            expires_at=expires_at,
        )
        if not decision.requires_confirmation:
            action_idempotency_key = action.idempotency_key or f"agent-action:{action.id}"
            confirmed = await self.repository.mark_action_confirmed(
                action=action,
                confirmed_at=_utcnow(),
                apply_payload=None,
                idempotency_key=action_idempotency_key,
            )
            await self._queue_action_apply(owner_user_id=owner_user_id, run=run, action=confirmed)
            return confirmed, True
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
        return action, True

    async def get_action(self, *, owner_user_id: UUID, action_id: UUID) -> AgentAction:
        action = await self.repository.get_action_for_owner(action_id=action_id, owner_user_id=owner_user_id)
        if action is None:
            raise ApiError(code="not_found", message="Agent action not found.", status=404)
        return action

    async def delete_artifact(self, *, owner_user_id: UUID, artifact_id: UUID) -> AgentArtifact:
        artifact = await self.repository.get_artifact_for_owner(artifact_id=artifact_id, owner_user_id=owner_user_id)
        if artifact is None:
            raise ApiError(code="not_found", message="Agent artifact not found.", status=404)
        if artifact.status == "deleted":
            return artifact
        run = await self.get_run(owner_user_id=owner_user_id, run_id=artifact.run_id)
        deleted = await self.repository.mark_artifact_deleted(artifact=artifact)
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="artifact.deleted",
            payload={"artifact_id": str(deleted.id), "artifact_type": deleted.artifact_type},
        )
        return deleted

    async def create_artifact(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
        artifact_type: str,
        payload: dict[str, Any],
        schema_version: str = "v1",
        status: str = "created",
        raw_payload_ref: str = "",
        emit_event: bool = True,
    ) -> AgentArtifact:
        run = await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        artifact = await self.repository.create_artifact(
            run_id=run.id,
            owner_user_id=owner_user_id,
            artifact_type=_normalize_text(artifact_type, max_length=120, required=True),
            schema_version=_normalize_text(schema_version, max_length=80) or "v1",
            status=_normalize_text(status, max_length=32) or "created",
            payload=payload,
            raw_payload_ref=_normalize_text(raw_payload_ref, max_length=512),
        )
        if emit_event:
            await self._append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="artifact.created",
                payload=_artifact_event_payload(artifact),
            )
        return artifact

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
        if action.status == "expired":
            return action
        if action.status not in {"proposed", "confirmation_required"}:
            raise ApiError(code="conflict", message="Agent action cannot be confirmed from its current status.", status=409)
        if _is_expired(action.expires_at):
            return await self._expire_action(owner_user_id=owner_user_id, action=action)
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
        await self._queue_action_apply(owner_user_id=owner_user_id, run=run, action=confirmed)
        return confirmed

    async def _queue_action_apply(self, *, owner_user_id: UUID, run: AgentRun, action: AgentAction) -> None:
        if self.outbox_service is None:
            raise ApiError(code="outbox_not_configured", message="Agent action outbox is not configured.", status=500)
        outbox_job = await self.outbox_service.enqueue(
            job_type=AGENT_ACTION_APPLY_JOB,
            payload={
                "action_id": str(action.id),
                "run_id": str(run.id),
                "actor_user_id": str(owner_user_id),
                "action_type": action.action_type,
                "target_type": action.target_type,
                "target_id": action.target_id,
                "apply_payload": action.apply_payload,
            },
            idempotency_key=_action_outbox_idempotency_key(action_id=action.id),
            action_id=action.id,
            request_id=run.request_id,
            trace_id=run.trace_id,
        )
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="action.queued",
            payload={
                "action_id": str(action.id),
                "action_status": action.status,
                "action_type": action.action_type,
                "target_type": action.target_type,
                "target_id": action.target_id,
                "outbox_status": outbox_job.status,
                "outbox_job_id": str(outbox_job.id),
            },
        )

    async def reject_action(self, *, owner_user_id: UUID, action_id: UUID, reason: str = "") -> AgentAction:
        action = await self.get_action(owner_user_id=owner_user_id, action_id=action_id)
        if action.status in {"rejected", "expired", "failed"}:
            return action
        if action.status not in {"proposed", "confirmation_required"}:
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
        await self._complete_waiting_run_after_action_decision(run=run, action_id=rejected.id, decision="rejected")
        return rejected

    async def _expire_action(self, *, owner_user_id: UUID, action: AgentAction) -> AgentAction:
        expired = await self.repository.mark_action_expired(action=action, failed_at=_utcnow(), error_code="action_expired")
        run = await self.get_run(owner_user_id=owner_user_id, run_id=expired.run_id)
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="action.expired",
            payload={
                "action_id": str(expired.id),
                "action_status": expired.status,
                "action_type": expired.action_type,
                "target_type": expired.target_type,
                "target_id": expired.target_id,
                "code": expired.error_code,
            },
        )
        await self._complete_waiting_run_after_action_decision(run=run, action_id=expired.id, decision="expired")
        return expired

    async def _complete_waiting_run_after_action_decision(self, *, run: AgentRun, action_id: UUID, decision: str) -> None:
        if run.status != "waiting_for_confirmation":
            return
        completed = await self.repository.mark_run_completed(run=run, completed_at=_utcnow())
        await self._append_event(
            thread_id=completed.thread_id,
            run_id=completed.id,
            event_type="run.completed",
            payload={"reason": f"action_{decision}", "action_id": str(action_id)},
        )
        if self.controls is not None:
            await self.controls.clear_active_run(thread_id=completed.thread_id, run_id=completed.id)

    async def _get_or_create_thread(self, *, actor_user_id: UUID, thread_id: UUID | None, title: str) -> AgentThread:
        if thread_id is None:
            return await self.repository.create_thread(owner_user_id=actor_user_id, title=title, metadata={})
        thread = await self.repository.get_thread_for_owner(thread_id=thread_id, owner_user_id=actor_user_id)
        if thread is None:
            raise ApiError(code="not_found", message="Agent thread not found.", status=404)
        return thread

    async def _ensure_no_active_thread_run(self, *, owner_user_id: UUID, thread_id: UUID) -> None:
        active_run = await self.repository.get_active_run_for_thread(thread_id=thread_id, owner_user_id=owner_user_id)
        if active_run is None:
            return
        raise ApiError(
            code="agent_run_in_progress",
            message="An agent run is already active for this thread.",
            status=409,
            details={"run_id": str(active_run.id), "status": active_run.status},
        )

    async def _append_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict[str, Any]) -> AgentEvent:
        event = await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
        if self.controls is not None:
            await self.controls.set_stream_cursor(run_id=run_id, sequence=event.sequence)
        return event

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

    def _register_run_queue_wakeup(self, *, run_id: UUID) -> None:
        if self.controls is None:
            return
        notify_run_queued = getattr(self.controls, "notify_run_queued", None)
        add_after_commit_callback = getattr(self.repository, "add_after_commit_callback", None)
        if not callable(notify_run_queued) or not callable(add_after_commit_callback):
            return

        async def notify_after_commit() -> None:
            await notify_run_queued(run_id=run_id)

        add_after_commit_callback(notify_after_commit)

    async def _release_idempotency(self, *, idempotency_record: IdempotencyKey | None) -> None:
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.release(record=idempotency_record)

    async def _replay_run(self, *, owner_user_id: UUID, response_ref: str) -> AgentRun:
        run_id = parse_idempotency_response_ref(response_ref)
        run = await self.repository.get_run_for_owner(run_id=run_id, owner_user_id=owner_user_id)
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


def _parse_uuid(value: Any, *, error_code: str) -> UUID:
    try:
        return UUID(str(value or ""))
    except (TypeError, ValueError) as exc:
        raise ApiError(code=error_code, message="Attachment resource id is invalid.", status=422) from exc


def _action_outbox_idempotency_key(*, action_id: UUID) -> str:
    return f"agent-action:{action_id}:apply"


def _title_from_message(message: str) -> str:
    return message[:80]


def _validate_limit(limit: int, *, max_limit: int = 100) -> None:
    if limit < 1 or limit > max_limit:
        raise ApiError(code="validation_failed", message=f"limit must be between 1 and {max_limit}.", status=422)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    comparable_expires_at = expires_at
    if comparable_expires_at.tzinfo is None:
        comparable_expires_at = comparable_expires_at.replace(tzinfo=timezone.utc)
    return comparable_expires_at <= _utcnow()


def _artifact_event_payload(artifact: AgentArtifact) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_id": str(artifact.id),
        "artifact_type": artifact.artifact_type,
        "schema_version": artifact.schema_version,
        "status": artifact.status,
        "artifact": {
            "id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            "status": artifact.status,
            "payload": artifact.payload,
            "raw_payload_ref": artifact.raw_payload_ref,
        },
    }
    if isinstance(artifact.payload, dict):
        payload.update(
            {key: value for key, value in artifact.payload.items() if key in {"form", "card", "card_json", "cart_update", "summary"}}
        )
    return payload
