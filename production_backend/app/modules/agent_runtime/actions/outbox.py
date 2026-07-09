from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ....workers.errors import PermanentJobError, RetryableJobError
from ...audit.models import OutboxJob
from ..event_stream.sink import AgentEventSink
from ..models import AgentAction
from ..repository import AgentRuntimeRepository
from ..service import AGENT_ACTION_APPLY_JOB


@dataclass(frozen=True)
class AgentActionApplyResult:
    resource_type: str = ""
    resource_id: str = ""
    details: dict[str, Any] | None = None


AgentActionApplyHandler = Callable[[AgentAction], Awaitable[AgentActionApplyResult]]


class AgentActionOutboxHandler:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        handlers: Mapping[str, AgentActionApplyHandler],
        event_sink: AgentEventSink | None = None,
    ) -> None:
        self.repository = repository
        self.handlers = handlers
        self.event_sink = event_sink

    async def __call__(self, job: OutboxJob) -> None:
        if job.job_type != AGENT_ACTION_APPLY_JOB:
            raise PermanentJobError("unsupported_agent_action_job")
        action_id = _action_id_from_job(job)
        action = await self.repository.get_action(action_id=action_id)
        if action is None:
            raise PermanentJobError("agent_action_not_found")
        run = await self.repository.get_run(run_id=action.run_id)
        if run is None:
            raise PermanentJobError("agent_run_not_found")
        if action.status == "applied":
            return
        if action.status not in {"confirmed", "applying"}:
            raise PermanentJobError("agent_action_not_confirmed")

        handler = self.handlers.get(action.action_type)
        if handler is None:
            await self._fail(action=action, run=run, error_code="agent_action_handler_not_found")
            raise PermanentJobError("agent_action_handler_not_found")

        await self.repository.mark_action_applying(action=action)
        try:
            result = await handler(action)
        except RetryableJobError as exc:
            await self._fail_if_final_attempt(job=job, action=action, run=run, error_code=exc.code)
            raise
        except PermanentJobError as exc:
            await self._fail(action=action, run=run, error_code=exc.code)
            raise
        except Exception as exc:
            error_code = "agent_action_handler_error"
            await self._fail_if_final_attempt(job=job, action=action, run=run, error_code=error_code)
            raise RetryableJobError(error_code) from exc

        applied = await self.repository.mark_action_applied(action=action, applied_at=_utcnow())
        await self._append_event(
            thread_id=run.thread_id,
            run_id=applied.run_id,
            event_type="action.applied",
            payload={
                **_action_event_payload(applied),
                "action_id": str(applied.id),
                "resource_type": result.resource_type,
                "resource_id": result.resource_id,
                "details": result.details or {},
            },
        )
        await self._complete_waiting_run(run=run, action=applied, decision="applied")

    async def _fail(self, *, action: AgentAction, run: Any, error_code: str) -> None:
        failed = await self.repository.mark_action_failed(action=action, failed_at=_utcnow(), error_code=error_code)
        await self._append_event(
            thread_id=run.thread_id,
            run_id=failed.run_id,
            event_type="action.failed",
            payload={**_action_event_payload(failed), "code": error_code},
        )
        await self._complete_waiting_run(run=run, action=failed, decision="failed")

    async def _fail_if_final_attempt(self, *, job: OutboxJob, action: AgentAction, run: Any, error_code: str) -> None:
        if job.attempts >= job.max_attempts:
            await self._fail(action=action, run=run, error_code=error_code)

    async def _append_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            await self.event_sink.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
            return
        await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)

    async def _complete_waiting_run(self, *, run: Any, action: AgentAction, decision: str) -> None:
        if run.status != "waiting_for_confirmation":
            return
        completed = await self.repository.mark_run_completed(run=run, completed_at=_utcnow())
        await self._append_event(
            thread_id=completed.thread_id,
            run_id=completed.id,
            event_type="run.completed",
            payload={"reason": f"action_{decision}", "action_id": str(action.id)},
        )
        if self.event_sink is not None:
            await self.event_sink.clear_active_run(thread_id=completed.thread_id, run_id=completed.id)


def _action_id_from_job(job: OutboxJob) -> UUID:
    if job.action_id is not None:
        return job.action_id
    return _require_uuid(job.payload.get("action_id"), "missing_action_id")


def _action_event_payload(action: AgentAction) -> dict[str, Any]:
    return {
        "action_id": str(action.id),
        "action_status": action.status,
        "action_type": action.action_type,
        "target_type": action.target_type,
        "target_id": action.target_id,
    }


def _require_uuid(raw: object, code: str) -> UUID:
    try:
        return UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise PermanentJobError(code) from exc


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
