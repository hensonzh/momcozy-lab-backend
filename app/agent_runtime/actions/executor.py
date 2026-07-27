from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ...core.errors import ApiError
from app.agent_runtime.runs.models import AgentAction
from app.agent_runtime.runs.repository import AgentRuntimeRepository

from .errors import PermanentActionError, RetryableActionError
from .policy import AgentActionPolicy, action_presentation_payload


@dataclass(frozen=True)
class AgentApplicationEvent:
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class AgentActionApplyResult:
    resource_type: str = ""
    resource_id: str = ""
    details: dict[str, Any] | None = None
    application_events: tuple[AgentApplicationEvent, ...] = ()


AgentActionApplyHandler = Callable[[AgentAction], Awaitable[AgentActionApplyResult]]


@dataclass(frozen=True)
class AgentActionExecutionOutcome:
    action: AgentAction
    apply_result: AgentActionApplyResult | None = None
    replayed: bool = False


class AgentActionExecutor:
    """Apply an authorized action inside the caller's agent-worker transaction.

    The executor deliberately has no queue or commit boundary. A direct tool call
    therefore commits the domain mutation, action state, domain events, and tool
    output together. A resumed confirmation run commits the same mutation batch
    with its assistant result and terminal run state.
    """

    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        handlers: Mapping[str, AgentActionApplyHandler],
        action_policy: AgentActionPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.handlers = handlers
        self.action_policy = action_policy or AgentActionPolicy()

    async def apply(self, action: AgentAction) -> AgentActionExecutionOutcome:
        if action.status not in {"confirmed", "applying", "applied", "failed"}:
            raise ApiError(
                code="agent_action_not_confirmed",
                message="Agent action is not authorized for execution.",
                status=409,
            )

        run = await self.repository.get_run_for_owner(
            run_id=action.run_id,
            owner_user_id=action.actor_user_id,
        )
        if run is None or run.actor_user_id != action.actor_user_id:
            if action.status in {"confirmed", "applying"}:
                failed = await self.repository.mark_action_failed(
                    action=action,
                    failed_at=_utcnow(),
                    error_code="agent_action_scope_violation",
                )
                return AgentActionExecutionOutcome(action=failed)
            raise ApiError(code="agent_action_scope_violation", message="Agent action owner scope is invalid.", status=403)
        if action.status in {"applied", "failed"}:
            return AgentActionExecutionOutcome(
                action=action,
                apply_result=_apply_result_from_payload(
                    getattr(action, "result_payload", None)
                ),
                replayed=True,
            )

        handler = self.handlers.get(action.action_type)
        if handler is None:
            failed = await self._fail(action=action, run=run, error_code="agent_action_handler_not_found")
            return AgentActionExecutionOutcome(action=failed)

        await self.repository.mark_action_applying(action=action)
        try:
            async with _action_apply_scope(self.repository):
                result = await handler(action)
                action.result_payload = _apply_result_payload(result)
                applied = await self.repository.mark_action_applied(action=action, applied_at=_utcnow())
                await self._append_success_events(run=run, action=applied, result=result)
        except Exception as exc:
            current = await self.repository.get_action(action_id=action.id)
            failed = await self._fail(
                action=current or action,
                run=run,
                error_code=_action_error_code(exc),
            )
            return AgentActionExecutionOutcome(action=failed)

        return AgentActionExecutionOutcome(action=applied, apply_result=result)

    async def _append_success_events(
        self,
        *,
        run: Any,
        action: AgentAction,
        result: AgentActionApplyResult,
    ) -> None:
        presentation = action_presentation_payload(action=action, action_policy=self.action_policy)
        await self.repository.append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="action.applied",
            payload={
                **_action_event_payload(action),
                **presentation,
                "resource_type": result.resource_type,
                "resource_id": result.resource_id,
                "details": result.details or {},
            },
        )
        for application_event in result.application_events:
            await self.repository.append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type=application_event.event_type,
                payload={
                    **application_event.payload,
                    "action_id": str(action.id),
                    **presentation,
                },
            )

    async def _fail(self, *, action: AgentAction, run: Any, error_code: str) -> AgentAction:
        failed = await self.repository.mark_action_failed(
            action=action,
            failed_at=_utcnow(),
            error_code=error_code,
        )
        await self.repository.append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="action.failed",
            payload={
                **_action_event_payload(failed),
                **action_presentation_payload(action=failed, action_policy=self.action_policy),
                "code": error_code,
            },
        )
        return failed


def _action_event_payload(action: AgentAction) -> dict[str, Any]:
    return {
        "action_id": str(action.id),
        "action_status": action.status,
        "action_type": action.action_type,
        "target_type": action.target_type,
        "target_id": action.target_id,
    }


def _action_error_code(exc: Exception) -> str:
    if isinstance(exc, (PermanentActionError, RetryableActionError, ApiError)):
        return exc.code
    return "agent_action_handler_error"


def _apply_result_payload(result: AgentActionApplyResult) -> dict[str, Any]:
    return {
        "resource_type": result.resource_type,
        "resource_id": result.resource_id,
        "details": dict(result.details or {}),
    }


def _apply_result_from_payload(payload: Any) -> AgentActionApplyResult | None:
    if not isinstance(payload, dict) or not payload:
        return None
    resource_type = str(payload.get("resource_type") or "")
    resource_id = str(payload.get("resource_id") or "")
    details = payload.get("details")
    if not isinstance(details, dict):
        details = {}
    if not resource_type and not resource_id and not details:
        return None
    return AgentActionApplyResult(
        resource_type=resource_type,
        resource_id=resource_id,
        details=dict(details),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def _action_apply_scope(repository: Any) -> AsyncIterator[None]:
    begin_nested = getattr(repository, "begin_nested", None)
    if not callable(begin_nested):
        yield
        return
    async with begin_nested():
        yield
