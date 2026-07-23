from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.errors import ApiError
from app.agent_runtime.actions.executor import AgentActionExecutionOutcome, AgentActionExecutor
from app.agent_runtime.actions.policy import AgentActionPolicy, action_presentation_payload
from app.agent_runtime.context.client import sanitize_agent_client_context
from app.agent_runtime.context.facts.service import AgentFactService
from app.agent_runtime.context.items import ContextItemAppend, message_context_item
from app.agent_runtime.context.memory.service import AgentMemoryService
from app.agent_runtime.events.semantics import with_run_event_semantic
from app.modules.audit import IdempotencyKey, IdempotencyService, parse_idempotency_response_ref, request_hash

from .controls import AgentRunControls
from .models import AgentAction, AgentArtifact, AgentEvent, AgentRun, AgentThread, AgentWorkflowState
from .registry import DEFAULT_RUNTIME_VERSION, SDK_ONLY_RUNTIME_PATTERN, validate_runtime
from .repository import AgentRuntimeRepository
from .state_store import AgentRuntimeStateStore


AGENT_RUN_CREATE_IDEMPOTENCY_SCOPE = "agent.runs.create"
DEFAULT_RUNTIME_PATTERN = SDK_ONLY_RUNTIME_PATTERN
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "expired"}
MAX_AGENT_RUN_ATTACHMENTS = 20
MAX_FORM_SUBMISSION_BYTES = 16_384
MODEL_IMAGE_CONTENT_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})


class AgentRuntimeService:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        idempotency_service: IdempotencyService | None = None,
        action_executor: AgentActionExecutor | None = None,
        file_repository: Any | None = None,
        image_access_service: Any | None = None,
        controls: AgentRunControls | None = None,
        action_policy: AgentActionPolicy | None = None,
        fact_service: AgentFactService | None = None,
        memory_service: AgentMemoryService | None = None,
        client_context_sanitizer: Callable[[Any], dict[str, Any]] = sanitize_agent_client_context,
    ) -> None:
        self.repository = repository
        self.idempotency_service = idempotency_service
        self.action_executor = action_executor
        self.file_repository = file_repository
        self.image_access_service = image_access_service
        self.controls = controls
        self.action_policy = action_policy or AgentActionPolicy()
        self.state_store = AgentRuntimeStateStore(repository=repository)
        self.fact_service = fact_service
        self.memory_service = memory_service
        self.client_context_sanitizer = client_context_sanitizer

    async def get_latest_workflow_state(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        workflow_type: str,
    ) -> AgentWorkflowState | None:
        return await self.repository.get_latest_workflow_state_for_thread(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            workflow_type=workflow_type,
        )

    async def upsert_workflow_state(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        run_id: UUID,
        workflow_type: str,
        status: str,
        schema_version: str,
        state: dict[str, Any],
        active_step: str,
        expires_at: datetime | None = None,
    ) -> AgentWorkflowState:
        return await self.state_store.upsert_active_workflow(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            run_id=run_id,
            workflow_type=workflow_type,
            status=status,
            schema_version=schema_version,
            state=state,
            active_step=active_step,
            expires_at=expires_at,
        )

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
        runtime_version: str | None = None,
        request_id: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> AgentRun:
        normalized_runtime_pattern = _normalize_runtime_pattern(runtime_pattern)
        if normalized_runtime_pattern != DEFAULT_RUNTIME_PATTERN:
            raise ApiError(code="validation_failed", message="Only the sdk_only runtime pattern is supported.", status=422)
        normalized_runtime_version = runtime_version or DEFAULT_RUNTIME_VERSION
        validate_runtime(version=normalized_runtime_version, pattern=normalized_runtime_pattern)
        normalized_message = _normalize_text(message, max_length=8000, required=True)
        requested_attachments = attachments or []
        safe_client_context = self.client_context_sanitizer(client_context)
        idempotency_payload = {
            "thread_id": str(thread_id or ""),
            "message": normalized_message,
            "attachments": requested_attachments,
            "client_context": safe_client_context,
            "runtime_pattern": normalized_runtime_pattern,
            "runtime_version": normalized_runtime_version,
        }
        idempotency_record = await self._reserve_run_idempotency(
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload=idempotency_payload,
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
            await self._ensure_image_access_bindings(
                actor_user_id=actor_user_id,
                thread_id=thread.id,
                attachments=safe_attachments,
            )
        except ApiError:
            await self._release_idempotency(idempotency_record=idempotency_record)
            raise
        run = await self.repository.create_run(
            thread_id=thread.id,
            actor_user_id=actor_user_id,
            runtime_pattern=normalized_runtime_pattern,
            runtime_version=normalized_runtime_version,
            request_id=request_id,
            trace_id=trace_id,
        )
        context_item = message_context_item(
            role="user",
            content={"text": normalized_message, "attachments": safe_attachments},
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
        await self.repository.append_context_items(
            thread_id=thread.id,
            run_id=run.id,
            items=(
                ContextItemAppend(
                    item_key=f"message:{message_record.id}",
                    item=context_item,
                ),
            ),
        )
        await self.repository.touch_thread(thread=thread, updated_at=_utcnow())
        fact_job_enqueued = False
        if await self._fact_capture_enabled(owner_user_id=actor_user_id):
            await self._sync_verified_form_facts(
                actor_user_id=actor_user_id,
                attachments=safe_attachments,
                observed_at=message_record.created_at if isinstance(message_record.created_at, datetime) else _utcnow(),
                request_id=request_id,
            )
            fact_job_enqueued = await self._enqueue_conversation_fact_extraction(
                actor_user_id=actor_user_id,
                run_id=run.id,
                message_id=message_record.id,
                request_id=request_id,
                trace_id=trace_id,
            )
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
            if fact_job_enqueued:
                self._register_fact_queue_wakeup(run_id=run.id)
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(run.id))
        return run

    async def _sync_verified_form_facts(
        self,
        *,
        actor_user_id: UUID,
        attachments: list[dict[str, Any]],
        observed_at: datetime,
        request_id: str,
    ) -> None:
        if self.fact_service is None:
            return
        for attachment in attachments:
            if attachment.get("type") != "form_submission" or attachment.get("verified") is not True:
                continue
            values = attachment.get("values")
            if not isinstance(values, dict):
                continue
            await self.fact_service.sync_form_submission(
                owner_user_id=actor_user_id,
                form_id=str(attachment.get("form_id") or ""),
                values=values,
                submission_id=str(attachment.get("submission_id") or ""),
                observed_at=observed_at,
                request_id=request_id,
            )

    async def _fact_capture_enabled(self, *, owner_user_id: UUID) -> bool:
        if self.fact_service is None:
            return False
        if self.memory_service is not None:
            return await self.memory_service.is_memory_enabled(owner_user_id=owner_user_id)
        capture_enabled = getattr(self.fact_service, "is_capture_enabled", None)
        if callable(capture_enabled):
            return bool(await capture_enabled(owner_user_id=owner_user_id))
        return True

    async def _enqueue_conversation_fact_extraction(
        self,
        *,
        actor_user_id: UUID,
        run_id: UUID,
        message_id: UUID,
        request_id: str,
        trace_id: str,
    ) -> bool:
        if self.fact_service is None:
            return False
        enqueue = getattr(self.fact_service, "enqueue_conversation_extraction", None)
        if not callable(enqueue):
            return False
        job = await enqueue(
            owner_user_id=actor_user_id,
            run_id=run_id,
            message_id=message_id,
            request_id=request_id,
            trace_id=trace_id,
        )
        return job is not None

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
            attachment_type = str(attachment.get("type") or "").strip()
            if attachment_type == "form_submission":
                verified.append(
                    await self._verify_form_submission_attachment(
                        actor_user_id=actor_user_id,
                        attachment=attachment,
                    )
                )
            elif attachment_type == "image":
                verified.append(
                    await self._verify_owned_image_attachment(
                        actor_user_id=actor_user_id,
                        attachment=attachment,
                    )
                )
            elif attachment_type == "file":
                verified.append(
                    await self._verify_owned_file_attachment(
                        actor_user_id=actor_user_id,
                        attachment=attachment,
                    )
                )
            else:
                safe_attachment = dict(attachment)
                safe_attachment.pop("verified", None)
                safe_attachment.pop("runtime_validated", None)
                safe_attachment.pop("trust_source", None)
                verified.append(safe_attachment)
        return verified

    async def _verify_owned_image_attachment(
        self,
        *,
        actor_user_id: UUID,
        attachment: dict[str, Any],
    ) -> dict[str, Any]:
        asset_id = _parse_uuid(attachment.get("asset_id"), error_code="invalid_agent_attachment")
        if self.file_repository is None:
            raise ApiError(code="invalid_agent_attachment", message="Image attachment verification is unavailable.", status=422)
        image = await self.file_repository.get_for_owner(file_id=asset_id, owner_user_id=actor_user_id)
        content_type = str(getattr(image, "content_type", "") or "").lower()
        if (
            image is None
            or getattr(image, "deleted_at", None) is not None
            or str(getattr(image, "status", "") or "") != "active"
            or content_type not in MODEL_IMAGE_CONTENT_TYPES
        ):
            raise ApiError(code="invalid_agent_attachment", message="Image attachment is not an active owned image.", status=422)
        detail = str(attachment.get("detail") or "").strip()
        return {
            "type": "image",
            "asset_id": str(image.id),
            "content_type": content_type,
            "original_filename": str(getattr(image, "original_filename", "") or "")[:255],
            "detail": detail if detail in {"auto", "low", "high"} else "auto",
            "runtime_validated": True,
            "trust_source": "owned_image_asset",
        }

    async def _ensure_image_access_bindings(
        self,
        *,
        actor_user_id: UUID,
        thread_id: UUID,
        attachments: list[dict[str, Any]],
    ) -> None:
        image_asset_ids = [
            _parse_uuid(attachment.get("asset_id"), error_code="invalid_agent_attachment")
            for attachment in attachments
            if attachment.get("type") == "image"
        ]
        if not image_asset_ids:
            return
        if self.image_access_service is None:
            raise ApiError(code="agent_image_url_unavailable", message="Agent image URL access is not configured.", status=503)
        for asset_id in image_asset_ids:
            await self.image_access_service.ensure_for_thread(
                thread_id=thread_id,
                owner_user_id=actor_user_id,
                asset_id=asset_id,
            )

    async def _verify_owned_file_attachment(
        self,
        *,
        actor_user_id: UUID,
        attachment: dict[str, Any],
    ) -> dict[str, Any]:
        file_id = _parse_uuid(attachment.get("file_id"), error_code="invalid_agent_attachment")
        if self.file_repository is None:
            raise ApiError(code="invalid_agent_attachment", message="File attachment verification is unavailable.", status=422)
        file_object = await self.file_repository.get_for_owner(file_id=file_id, owner_user_id=actor_user_id)
        if (
            file_object is None
            or getattr(file_object, "deleted_at", None) is not None
            or str(getattr(file_object, "status", "") or "") != "active"
            or str(getattr(file_object, "content_type", "") or "").lower() != "application/pdf"
        ):
            raise ApiError(code="invalid_agent_attachment", message="File attachment is not an active owned PDF.", status=422)
        return {
            "type": "file",
            "file_id": str(file_object.id),
            "content_type": "application/pdf",
            "original_filename": str(getattr(file_object, "original_filename", "") or "")[:255],
            "runtime_validated": True,
            "trust_source": "owned_file_record",
        }

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
        outcome, created = await self.propose_action_once_with_outcome(
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
            reuse_existing=reuse_existing,
        )
        return outcome.action, created

    async def propose_action_once_with_outcome(
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
    ) -> tuple[AgentActionExecutionOutcome, bool]:
        run = await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        decision = self.action_policy.validate(
            action_type=_normalize_text(action_type, max_length=120, required=True),
            target_type=_normalize_text(target_type, max_length=120),
            side_effect_level=_normalize_text(side_effect_level, max_length=32),
        )
        action_executor = self.action_executor
        if not decision.requires_confirmation and action_executor is None:
            raise ApiError(code="action_executor_not_configured", message="Agent action executor is not configured.", status=500)
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
                    if not decision.requires_confirmation and existing.status in {"confirmed", "applying"}:
                        assert action_executor is not None
                        outcome = await action_executor.apply(existing)
                        return outcome, False
                    return AgentActionExecutionOutcome(action=existing, replayed=True), False
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
            assert action_executor is not None
            action_idempotency_key = action.idempotency_key or f"agent-action:{action.id}"
            confirmed = await self.repository.mark_action_confirmed(
                action=action,
                confirmed_at=_utcnow(),
                apply_payload=None,
                idempotency_key=action_idempotency_key,
            )
            outcome = await action_executor.apply(confirmed)
            return outcome, True
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
                **action_presentation_payload(action=action, action_policy=self.action_policy),
            },
        )
        return AgentActionExecutionOutcome(action=action), True

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

    async def create_artifact_once(
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
    ) -> tuple[AgentArtifact, bool]:
        run = await self.get_run(owner_user_id=owner_user_id, run_id=run_id)
        normalized_artifact_type = _normalize_text(artifact_type, max_length=120, required=True)
        lock = getattr(self.repository, "lock_run_for_action_proposal", None)
        if callable(lock):
            await lock(run_id=run.id)
        artifacts = await self.repository.list_artifacts_for_run(run_id=run.id)
        existing = next(
            (
                artifact
                for artifact in reversed(artifacts)
                if artifact.owner_user_id == owner_user_id
                and artifact.artifact_type == normalized_artifact_type
                and artifact.status != "deleted"
            ),
            None,
        )
        if existing is not None:
            return existing, False
        artifact = await self.repository.create_artifact(
            run_id=run.id,
            owner_user_id=owner_user_id,
            artifact_type=normalized_artifact_type,
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
        return artifact, True

    async def confirm_action(
        self,
        *,
        owner_user_id: UUID,
        action_id: UUID,
        edited_apply_payload: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> AgentAction:
        lock_action = getattr(self.repository, "lock_action_for_confirmation", None)
        if callable(lock_action):
            await lock_action(action_id=action_id, owner_user_id=owner_user_id)
        action = await self.get_action(owner_user_id=owner_user_id, action_id=action_id)
        if action.status in {"confirmed", "applying", "applied"}:
            return action
        if action.status == "expired":
            return action
        if action.status not in {"proposed", "confirmation_required"}:
            raise ApiError(code="conflict", message="Agent action cannot be confirmed from its current status.", status=409)
        if _is_expired(action.expires_at):
            return await self._expire_action(owner_user_id=owner_user_id, action=action)
        decision = self.action_policy.validate(
            action_type=action.action_type,
            target_type=action.target_type,
            side_effect_level=action.side_effect_level,
        )
        if edited_apply_payload is not None and not decision.allows_apply_payload_edit:
            raise ApiError(
                code="action_payload_edit_not_allowed",
                message="This agent action must be confirmed without changing its reviewed payload.",
                status=409,
            )
        run = await self.get_run(owner_user_id=owner_user_id, run_id=action.run_id)
        if run.status != "waiting_for_confirmation":
            raise ApiError(
                code="agent_run_not_waiting_for_confirmation",
                message="Agent run is not waiting for this confirmation.",
                status=409,
            )
        action_idempotency_key = _normalize_text(idempotency_key, max_length=255) or f"agent-action:{action.id}"
        confirmed = await self.repository.mark_action_confirmed(
            action=action,
            confirmed_at=_utcnow(),
            apply_payload=edited_apply_payload,
            idempotency_key=action_idempotency_key,
        )
        await self.repository.mark_run_queued(run=run)
        presentation = action_presentation_payload(action=confirmed, action_policy=self.action_policy)
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="action.confirmed",
            payload={
                "action_id": str(confirmed.id),
                "action_status": confirmed.status,
                "action_type": confirmed.action_type,
                "target_type": confirmed.target_type,
                "target_id": confirmed.target_id,
                **presentation,
            },
        )
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="run.queued",
            payload={
                "reason": "action_confirmed",
                "action_id": str(confirmed.id),
                "phase": "queued",
            },
        )
        self._register_run_queue_wakeup(run_id=run.id)
        return confirmed

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
                **action_presentation_payload(action=rejected, action_policy=self.action_policy),
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
                **action_presentation_payload(action=expired, action_policy=self.action_policy),
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
        payload = with_run_event_semantic(payload, event_type=event_type, run_id=str(run_id))
        event = await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
        if self.controls is not None:
            await self.controls.set_stream_cursor(run_id=run_id, sequence=event.sequence)
        return event

    async def _reserve_run_idempotency(
        self,
        *,
        actor_user_id: UUID,
        key: str | None,
        payload: dict[str, Any],
    ) -> IdempotencyKey | None:
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

    def _register_fact_queue_wakeup(self, *, run_id: UUID) -> None:
        if self.controls is None:
            return
        notify_fact_queued = getattr(self.controls, "notify_fact_queued", None)
        add_after_commit_callback = getattr(self.repository, "add_after_commit_callback", None)
        if not callable(notify_fact_queued) or not callable(add_after_commit_callback):
            return

        async def notify_after_commit() -> None:
            await notify_fact_queued(run_id=run_id)

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


def _normalize_runtime_pattern(value: str | None) -> str:
    return str(value or DEFAULT_RUNTIME_PATTERN).strip()


def _parse_uuid(value: Any, *, error_code: str) -> UUID:
    try:
        return UUID(str(value or ""))
    except (TypeError, ValueError) as exc:
        raise ApiError(code=error_code, message="Attachment resource id is invalid.", status=422) from exc


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
