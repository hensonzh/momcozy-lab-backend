from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from ..core.errors import ApiError
from ..modules.agent_runtime.run_lifecycle.controls import AgentRunControls
from ..modules.agent_runtime.run_lifecycle.execution import AgentRunExecutionResult, AgentRunHandler
from ..modules.agent_runtime.models import AgentEvent, AgentRun
from ..modules.agent_runtime.repository import AgentRuntimeRepository


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "expired"}
AgentRunWorkerResult = AgentRunExecutionResult


@dataclass(frozen=True)
class AgentRunQueueWorkerResult:
    scanned: int
    processed: int
    terminal: int


async def missing_agent_run_handler(_run: AgentRun) -> AgentRunExecutionResult:
    raise ApiError(code="runtime_handler_not_configured", message="Agent run handler is not configured.", status=503)


class AgentRunQueueWorker:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        run_worker: "AgentRunWorker",
        batch_limit: int = 10,
        recover_running_older_than_seconds: int | None = 900,
    ) -> None:
        if batch_limit < 1:
            raise ValueError("batch_limit must be positive")
        self.repository = repository
        self.run_worker = run_worker
        self.batch_limit = batch_limit
        self.recover_running_older_than_seconds = recover_running_older_than_seconds

    async def run_once(self) -> AgentRunQueueWorkerResult:
        runs = await self.repository.list_runnable_runs(
            limit=self.batch_limit,
            recover_running_before=self._recover_running_before(),
        )
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
        return AgentRunQueueWorkerResult(scanned=len(runs), processed=processed, terminal=terminal)

    def _recover_running_before(self) -> datetime | None:
        if self.recover_running_older_than_seconds is None:
            return None
        return _utcnow() - timedelta(seconds=self.recover_running_older_than_seconds)


class AgentRunWorker:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        controls: AgentRunControls | None = None,
        handler: AgentRunHandler | None = None,
        after_event_append: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.repository = repository
        self.controls = controls
        self.handler = handler or missing_agent_run_handler
        self.after_event_append = after_event_append

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

    async def _execute_locked(self, run: AgentRun) -> AgentRun:
        if run.status == "waiting_for_confirmation":
            return run
        if await self._cancel_requested(run):
            return await self._cancel(run=run, error_code="cancelled_before_start")

        if run.status == "queued":
            run = await self.repository.mark_run_running(run=run, started_at=_utcnow())
            await self._append_event(run=run, event_type="run.started", payload={})
        elif run.status != "running":
            return run

        if await self._cancel_requested(run):
            return await self._cancel(run=run, error_code="cancelled_during_startup")

        try:
            result = await self.handler(run)
        except ApiError as exc:
            return await self._fail(run=run, error_code=exc.code, error_details={"code": exc.code})
        except Exception:
            return await self._fail(run=run, error_code="runtime_error", error_details={})

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
                message = await self.repository.create_message(
                    thread_id=run.thread_id,
                    run_id=run.id,
                    role="assistant",
                    message_type="text",
                    content={"text": result.final_text},
                    status="completed",
                )
                await self._append_event(
                    run=run,
                    event_type="message.completed",
                    payload={
                        "message_id": str(message.id),
                        "role": "assistant",
                        "text": result.final_text,
                    },
                )
            completed = await self.repository.mark_run_completed(run=run, completed_at=_utcnow())
            await self._append_event(run=completed, event_type="run.completed", payload={})
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

    async def _fail(self, *, run: AgentRun, error_code: str, error_details: dict[str, str]) -> AgentRun:
        failed = await self.repository.mark_run_failed(run=run, completed_at=_utcnow(), error_code=error_code, error_details=error_details)
        await self._append_event(run=failed, event_type="run.failed", payload={"code": error_code})
        await self._clear_controls(failed)
        return failed

    async def _append_event(self, *, run: AgentRun, event_type: str, payload: dict[str, str]) -> AgentEvent:
        event = await self.repository.append_event(thread_id=run.thread_id, run_id=run.id, event_type=event_type, payload=payload)
        if self.controls is not None:
            await self.controls.set_stream_cursor(run_id=run.id, sequence=event.sequence)
        if self.after_event_append is not None:
            await self.after_event_append()
        return event

    async def _clear_controls(self, run: AgentRun) -> None:
        if self.controls is None:
            return
        await self.controls.clear_active_run(thread_id=run.thread_id, run_id=run.id)
        await self.controls.clear_cancel(run_id=run.id)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
