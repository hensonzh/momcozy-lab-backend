from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ..core.errors import ApiError
from ..modules.agent_runtime.actions.executor import AgentActionExecutor
from ..modules.agent_runtime.event_stream.publisher import AgentEventPublisher
from ..modules.agent_runtime.event_stream.transient import AgentTransientStream
from ..modules.agent_runtime.run_lifecycle.controls import AgentRunControls
from ..modules.agent_runtime.run_lifecycle.execution import AgentRunExecutionResult, AgentRunHandler
from ..modules.agent_runtime.models import AgentAction, AgentEvent, AgentRun
from ..modules.agent_runtime.repository import AgentRuntimeRepository
from ..modules.agent_runtime.response_text import (
    APPEND_ONLY_TEXT_STREAM_SCHEMA_VERSION,
    agent_response_text_integrity,
)


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "expired"}
INTERRUPTED_RUN_ERROR_CODE = "runtime_interrupted"
AgentRunWorkerResult = AgentRunExecutionResult
LOGGER = logging.getLogger("production_backend.agent_worker")


@dataclass(frozen=True)
class AgentRunQueueWorkerResult:
    scanned: int
    processed: int
    terminal: int
    interrupted: int = 0


async def missing_agent_run_handler(_run: AgentRun) -> AgentRunExecutionResult:
    raise ApiError(code="runtime_handler_not_configured", message="Agent run handler is not configured.", status=503)


class AgentRunQueueWorker:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        run_worker: "AgentRunWorker",
        batch_limit: int = 10,
        interrupt_running_older_than_seconds: int | None = 900,
    ) -> None:
        if batch_limit < 1:
            raise ValueError("batch_limit must be positive")
        self.repository = repository
        self.run_worker = run_worker
        self.batch_limit = batch_limit
        self.interrupt_running_older_than_seconds = interrupt_running_older_than_seconds

    async def run_once(self) -> AgentRunQueueWorkerResult:
        stale_running_runs = await self.repository.list_stale_running_runs(
            cutoff=self._interrupt_running_before(),
            limit=self.batch_limit,
        )
        interrupted = 0
        for run in stale_running_runs:
            after_run = await self.run_worker.interrupt_running(run_id=run.id)
            if after_run is not None and after_run.status in TERMINAL_RUN_STATUSES:
                interrupted += 1

        queued_limit = max(0, self.batch_limit - len(stale_running_runs))
        runs = await self.repository.list_runnable_runs(limit=queued_limit) if queued_limit else []
        processed = 0
        terminal = 0
        for run in runs:
            before_status = run.status
            after_run = await self.run_worker.run_once(run_id=run.id)
            if after_run is None:
                continue
            if after_run.status != before_status:
                processed += 1
            if after_run.status in TERMINAL_RUN_STATUSES:
                terminal += 1
        return AgentRunQueueWorkerResult(
            scanned=len(stale_running_runs) + len(runs),
            processed=interrupted + processed,
            terminal=interrupted + terminal,
            interrupted=interrupted,
        )

    def _interrupt_running_before(self) -> datetime | None:
        if self.interrupt_running_older_than_seconds is None:
            return None
        return _utcnow() - timedelta(seconds=self.interrupt_running_older_than_seconds)


class AgentRunWorker:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        controls: AgentRunControls | None = None,
        handler: AgentRunHandler | None = None,
        action_executor: AgentActionExecutor | None = None,
        after_event_append: Callable[[], Awaitable[None]] | None = None,
        transient_stream: AgentTransientStream | None = None,
    ) -> None:
        self.repository = repository
        self.controls = controls
        self.handler = handler or missing_agent_run_handler
        self.action_executor = action_executor
        self.after_event_append = after_event_append
        self.transient_stream = transient_stream
        self.event_publisher = AgentEventPublisher(
            repository=repository,
            controls=controls,
            after_append=after_event_append,
            transient_stream=transient_stream,
        )

    async def run_once(self, *, run_id: UUID) -> AgentRun | None:
        run = await self.repository.get_run(run_id=run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return run

        if self.controls is None:
            return await self._execute_locked(run)

        async with self.controls.run_lock(run_id=run.id) as acquired:
            if not acquired:
                return run
            return await self._execute_locked(run)

    async def interrupt_running(self, *, run_id: UUID) -> AgentRun | None:
        run = await self.repository.get_run(run_id=run_id)
        if run is None or run.status != "running":
            return run

        if self.controls is None:
            return await self._interrupt_running_locked(run)

        async with self.controls.run_lock(run_id=run.id) as acquired:
            if not acquired:
                return run
            run = await self.repository.refresh_run(run=run)
            if run.status != "running":
                return run
            return await self._interrupt_running_locked(run)

    async def _execute_locked(self, run: AgentRun) -> AgentRun:
        if run.status == "waiting_for_confirmation":
            return run
        if run.status == "running":
            return run
        if await self._cancel_requested(run):
            return await self._cancel(run=run, error_code="cancelled_before_start")

        if run.status == "queued":
            run = await self.repository.mark_run_running(run=run, started_at=_utcnow())
            await self._append_event(run=run, event_type="run.started", payload={})
        else:
            return run

        if await self._cancel_requested(run):
            return await self._cancel(run=run, error_code="cancelled_during_startup")

        try:
            result = await self._resume_action(run)
            if result is None:
                result = await self.handler(run)
        except ApiError as exc:
            error_details = _api_error_details(exc)
            _log_run_failure(run=run, error_code=exc.code, error_details=error_details)
            return await self._fail(run=run, error_code=exc.code, error_details=error_details)
        except Exception as exc:
            error_details = {"exception_type": exc.__class__.__name__}
            _log_run_failure(run=run, error_code="runtime_error", error_details=error_details)
            return await self._fail(run=run, error_code="runtime_error", error_details=error_details)

        run = await self.repository.refresh_run(run=run)
        if run.status in TERMINAL_RUN_STATUSES:
            await self._clear_controls(run)
            return run

        if await self._cancel_requested(run):
            return await self._cancel(run=run, error_code="cancelled_during_run")

        if result.status == "waiting_for_confirmation":
            waiting = await self.repository.mark_run_waiting_for_confirmation(run=run)
            await self._append_event(
                run=waiting,
                event_type="run.waiting_for_confirmation",
                payload={"action_id": str(result.pending_action_id or "")},
            )
            return waiting

        if result.status == "completed":
            if result.final_text:
                content: dict[str, Any] = {"text": result.final_text}
                message = await self.repository.create_message(
                    message_id=result.assistant_message_id,
                    thread_id=run.thread_id,
                    run_id=run.id,
                    role="assistant",
                    message_type="text",
                    content=content,
                    status="completed",
                )
                text_integrity = agent_response_text_integrity(result.final_text)
                payload: dict[str, Any] = {
                    "message_id": str(message.id),
                    "message_stream_id": str(message.id),
                    "role": "assistant",
                    "text": result.final_text,
                    "stream_schema_version": APPEND_ONLY_TEXT_STREAM_SCHEMA_VERSION,
                    "segment_count": max(0, result.stream_segment_count),
                    "content_utf8_bytes": text_integrity.utf8_bytes,
                    "content_sha256": text_integrity.sha256,
                }
                live_payload = dict(payload)
                if _has_exactly_three_quick_replies(result.quick_replies):
                    live_payload["quick_replies"] = result.quick_replies
                await self._append_event(
                    run=run,
                    event_type="message.completed",
                    payload=payload,
                    live_payload=live_payload,
                    live_durable=not _has_exactly_three_quick_replies(result.quick_replies),
                    live_before_append=_has_exactly_three_quick_replies(result.quick_replies),
                )
            completed = await self.repository.mark_run_completed(run=run, completed_at=_utcnow())
            completion_payload: dict[str, Any] = {}
            if result.completion_reason:
                completion_payload["reason"] = result.completion_reason
            if result.completed_action_id is not None:
                completion_payload["action_id"] = str(result.completed_action_id)
            await self._append_event(run=completed, event_type="run.completed", payload=completion_payload)
            await self._clear_controls(completed)
            return completed

        return await self._fail(run=run, error_code="unsupported_runtime_outcome", error_details={})

    async def _cancel_requested(self, run: AgentRun) -> bool:
        if self.controls is None:
            return False
        return await self.controls.is_cancel_requested(run_id=run.id)

    async def _cancel(self, *, run: AgentRun, error_code: str) -> AgentRun:
        cancelled = await self.repository.mark_run_cancelled(run=run, cancelled_at=_utcnow(), error_code=error_code)
        await self._append_event(run=cancelled, event_type="run.cancelled", payload={"code": error_code})
        await self._clear_controls(cancelled)
        return cancelled

    async def _fail(self, *, run: AgentRun, error_code: str, error_details: dict[str, Any]) -> AgentRun:
        failed = await self.repository.mark_run_failed(run=run, completed_at=_utcnow(), error_code=error_code, error_details=error_details)
        await self._append_event(run=failed, event_type="run.failed", payload={"code": error_code})
        await self._clear_controls(failed)
        return failed

    async def _interrupt_running_locked(self, run: AgentRun) -> AgentRun:
        action = await self._latest_resumable_action(run=run)
        if action is not None:
            queued = await self.repository.mark_run_queued(run=run)
            await self._append_event(
                run=queued,
                event_type="run.queued",
                payload={"reason": "action_resume", "action_id": str(action.id), "phase": "queued"},
            )
            await self._notify_run_queued(run_id=queued.id)
            return queued
        return await self._fail(
            run=run,
            error_code=INTERRUPTED_RUN_ERROR_CODE,
            error_details={"reason": "stale_running_run_not_resumed"},
        )

    async def _resume_action(self, run: AgentRun) -> AgentRunExecutionResult | None:
        action = await self._latest_resumable_action(run=run)
        if action is None:
            return None
        if self.action_executor is None:
            raise ApiError(
                code="action_executor_not_configured",
                message="Agent action executor is not configured.",
                status=503,
            )
        outcome = await self.action_executor.apply(action)
        action = outcome.action

        get_latest_message = getattr(self.repository, "get_latest_assistant_message_for_run", None)
        latest_message = await get_latest_message(run_id=run.id) if callable(get_latest_message) else None
        final_text = "" if latest_message is not None else _action_completion_text(action)
        return AgentRunExecutionResult(
            status="completed",
            final_text=final_text,
            completed_action_id=action.id,
            completion_reason=f"action_{action.status}",
        )

    async def _latest_resumable_action(self, *, run: AgentRun) -> AgentAction | None:
        list_actions = getattr(self.repository, "list_actions_for_run", None)
        if not callable(list_actions):
            return None
        actions = await list_actions(run_id=run.id)
        return next(
            (
                action
                for action in reversed(actions)
                if action.status in {"confirmed", "applying", "applied", "failed"}
            ),
            None,
        )

    async def _notify_run_queued(self, *, run_id: UUID) -> None:
        if self.controls is None:
            return
        notify = getattr(self.controls, "notify_run_queued", None)
        if callable(notify):
            await notify(run_id=run_id)

    async def _append_event(
        self,
        *,
        run: AgentRun,
        event_type: str,
        payload: dict[str, Any],
        live_payload: dict[str, Any] | None = None,
        live_durable: bool = True,
        live_before_append: bool = False,
    ) -> AgentEvent:
        if live_before_append:
            await self._publish_live_event(
                run=run,
                event_type=event_type,
                payload=payload if live_payload is None else live_payload,
                durable=live_durable,
            )
        event = await self.event_publisher.append_event(thread_id=run.thread_id, run_id=run.id, event_type=event_type, payload=payload)
        if not live_before_append:
            await self._publish_live_event(
                run=run,
                event_type=event_type,
                payload=payload if live_payload is None else live_payload,
                durable=live_durable,
            )
        return event

    async def _publish_live_event(self, *, run: AgentRun, event_type: str, payload: dict[str, Any], durable: bool) -> None:
        if self.transient_stream is None or event_type not in LIVE_DURABLE_EVENT_TYPES:
            return
        dedupe_key = _live_event_dedupe_key(run_id=run.id, event_type=event_type, payload=payload)
        if not dedupe_key:
            return
        try:
            await self.event_publisher.publish_application_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type=event_type,
                payload=payload,
                dedupe_key=dedupe_key,
                optimistic=False,
                durable=durable,
            )
        except Exception:
            LOGGER.warning("Failed to publish durable live agent event.", exc_info=True)

    async def _clear_controls(self, run: AgentRun) -> None:
        if self.controls is None:
            return
        await self.controls.clear_active_run(thread_id=run.thread_id, run_id=run.id)
        await self.controls.clear_cancel(run_id=run.id)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _api_error_details(exc: ApiError) -> dict[str, Any]:
    details: dict[str, Any] = {"code": exc.code, "status": exc.status}
    for key, value in exc.details.items():
        if isinstance(value, str):
            details[key] = value[:500]
        elif isinstance(value, int | float | bool) or value is None:
            details[key] = value
        else:
            details[key] = str(value)[:500]
    return details


def _log_run_failure(*, run: AgentRun, error_code: str, error_details: dict[str, Any]) -> None:
    LOGGER.error(
        json.dumps(
            {
                "event": "agent_run.failed",
                "run_id": str(run.id),
                "thread_id": str(run.thread_id),
                "actor_user_id": str(run.actor_user_id),
                "error_code": error_code,
                "error_details": error_details,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _has_exactly_three_quick_replies(value: list[dict[str, Any]]) -> bool:
    return len(value) == 3


LIVE_DURABLE_EVENT_TYPES = {
    "run.queued",
    "message.completed",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.waiting_for_confirmation",
}


def _action_completion_text(action: AgentAction) -> str:
    if action.status == "failed":
        return "这次操作没有成功，数据没有被更改，请稍后重试。"
    success_messages = {
        "pregnancy.plan.create": "孕期计划已生成，并同步到「宝宝和我」。",
        "pregnancy_diary.entry.delete": "孕期日记已删除。",
        "plans.plan.delete": "计划已删除。",
        "support.ticket.create": "客服工单已提交。",
    }
    return success_messages.get(action.action_type, "操作已完成并保存。")


def _live_event_dedupe_key(*, run_id: UUID, event_type: str, payload: dict[str, Any]) -> str:
    unique_id = payload.get("message_id") or payload.get("action_id")
    if isinstance(unique_id, str) and unique_id.strip():
        return f"{run_id}:{event_type}:{unique_id.strip()}"
    return f"{run_id}:{event_type}"
