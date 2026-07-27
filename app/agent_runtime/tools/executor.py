from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, cast
from uuid import UUID

from app.core.errors import ApiError
from app.core.logging import log_agent_runtime_event
from app.core.metrics import RequestMetrics
from app.infrastructure.object_storage.base import ObjectStorage
from app.agent_runtime.events.publisher import AgentEventPublisher
from app.agent_runtime.events.transient import AgentTransientStream
from app.agent_runtime.runs.models import AgentToolCall
from app.agent_runtime.tools.payloads import DEFAULT_MAX_INLINE_PAYLOAD_BYTES, maybe_externalize_json_payload
from app.agent_runtime.runs.repository import AgentRuntimeRepository
from app.agent_runtime.tools.result import ToolResult
from app.modules.auth import CurrentUser

from .policy import ToolExecutionPolicy
from .registry import ToolContractRegistry
from .validation import validate_tool_input, validate_tool_output


@dataclass(frozen=True)
class ToolHandlerContext:
    actor: CurrentUser
    run_id: UUID
    tool_name: str
    call_id: str
    args: dict[str, Any]
    thread_id: UUID | None = None


ToolHandler = Callable[[ToolHandlerContext], Awaitable[ToolResult] | ToolResult]
DEFERRED_AGENT_EVENTS_KEY = "_deferred_agent_events"
LOGGER = logging.getLogger("production_backend.agent_runtime.tools")


class _ToolCommitFailure(Exception):
    pass


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_call: AgentToolCall
    canonical_output: dict[str, Any]
    tool_result: ToolResult


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolContractRegistry,
        repository: AgentRuntimeRepository,
        handlers: dict[str, ToolHandler] | None = None,
        event_sink: AgentEventPublisher | None = None,
        metrics: RequestMetrics | None = None,
        object_storage: ObjectStorage | None = None,
        max_inline_output_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
        transient_stream: AgentTransientStream | None = None,
        policy: ToolExecutionPolicy | None = None,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.handlers = handlers or {}
        self.event_sink = event_sink
        self.metrics = metrics
        self.object_storage = object_storage
        self.max_inline_output_bytes = max_inline_output_bytes
        self.transient_stream = transient_stream
        self.policy = policy or ToolExecutionPolicy()

    async def execute(
        self,
        *,
        actor: CurrentUser,
        run_id: UUID,
        tool_name: str,
        call_id: str,
        args: dict[str, Any],
        trusted_args: dict[str, Any] | None = None,
        use_internal_input_schema: bool = False,
        handler_override: ToolHandler | None = None,
    ) -> ToolExecutionResult:
        started_at = perf_counter()
        tool_call: AgentToolCall | None = None
        tool_call_id: UUID | None = None
        tool_scope: Any | None = None
        tool_started_committed = False
        finalizing_success = False
        try:
            contract = self.registry.get(tool_name)
            self._enforce_actor_scope(args=args)
            validation_schema = (
                contract.internal_input_schema
                if use_internal_input_schema
                else contract.input_schema
            )
            if validation_schema is None:
                raise ApiError(
                    code="tool_input_invalid",
                    message="Tool does not define a trusted internal input contract.",
                    status=422,
                )
            validate_tool_input(schema=validation_schema, value=args)
            handler = handler_override or self.handlers.get(tool_name)
            if handler is None:
                raise ApiError(code="unsupported_operation", message="Tool handler is not configured.", status=501)
            run = await self.repository.get_run(run_id=run_id)
            if run is None:
                raise ApiError(code="not_found", message="Agent run not found.", status=404)
            effect_scope = self.policy.effective_effect_scope(
                tool_name=tool_name,
                args=args,
                default=contract.effect_scope,
            )
            safe_args = self.policy.safe_args(tool_name=tool_name, args=args)

            tool_call = await self.repository.start_tool_call(
                run_id=run_id,
                tool_name=tool_name,
                call_id=call_id,
                safe_args=safe_args,
                started_at=_utcnow(),
            )
            tool_call_id = tool_call.id
            started_payload = {
                "tool_call_id": str(tool_call.id),
                "tool_name": tool_name,
                "call_id": call_id,
                "label": self.policy.event_label(tool_name=tool_name, payload=args),
                "safe_args": safe_args,
            }
            started_payload = self.policy.enrich_event(
                started_payload,
                event_type="tool.started",
                tool_name=tool_name,
                effect_scope=effect_scope,
            )
            await self._publish_optimistic_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.started",
                payload=started_payload,
            )
            await self._append_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.started",
                payload=started_payload,
            )
            tool_started_committed = True
            tool_scope = await _open_tool_scope(self.repository)
            raw_result = await asyncio.wait_for(
                _maybe_await(
                    handler(
                        ToolHandlerContext(
                            actor=actor,
                            run_id=run_id,
                            tool_name=tool_name,
                            call_id=call_id,
                            args={**args, **(trusted_args or {})},
                            thread_id=run.thread_id,
                        )
                    )
                ),
                timeout=contract.timeout_seconds,
            )
            if not isinstance(raw_result, ToolResult) or not isinstance(raw_result.canonical_output, dict):
                raise TypeError("Tool handlers must return ToolResult with a canonical object output.")
            output_payload = dict(raw_result.canonical_output)
            deferred_events = list(raw_result.deferred_events)
            validate_tool_output(schema=contract.output_schema, value=output_payload)
            externalized_output = await maybe_externalize_json_payload(
                payload=output_payload,
                object_storage=self.object_storage,
                run_id=run.id,
                payload_kind="tool-outputs",
                key_suffix=str(tool_call.id),
                max_inline_bytes=self.max_inline_output_bytes,
            )
            tool_result = ToolResult(
                canonical_output=output_payload,
                supplemental_content=raw_result.supplemental_content,
                serialization="json",
            )
            completed = await self.repository.complete_tool_call(tool_call=tool_call, completed_at=_utcnow())
            output = await self.repository.create_tool_output(
                tool_call_id=completed.id,
                output=externalized_output.inline_payload,
                output_ref=externalized_output.raw_payload_ref,
            )
            output_summary = self.policy.event_output_summary(
                tool_name=completed.tool_name,
                output=output_payload,
            )
            completed_payload = {
                "tool_call_id": str(completed.id),
                "tool_output_id": str(output.id),
                "tool_name": completed.tool_name,
                "call_id": completed.call_id,
                "label": self.policy.event_label(tool_name=completed.tool_name, payload=output_payload),
                "output_summary": output_summary,
            }
            completed_payload = self.policy.enrich_event(
                completed_payload,
                event_type="tool.completed",
                tool_name=completed.tool_name,
                output=output_payload,
                effect_scope=effect_scope,
            )
            await self._publish_optimistic_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.completed",
                payload=completed_payload,
            )
            event_batch: list[tuple[str, dict[str, Any]]] = [("tool.completed", completed_payload)]
            for deferred_event in deferred_events:
                payload = dict(deferred_event["payload"])
                payload.setdefault("tool_call_id", str(completed.id))
                event_batch.append((deferred_event["event_type"], payload))
            staged_events = await self._stage_tool_events(
                thread_id=run.thread_id,
                run_id=run.id,
                events=tuple(event_batch),
            )
            try:
                finalizing_success = True
                await _commit_tool_scope(tool_scope)
                tool_scope = None
                await self._finalize_staged_tool_events(run_id=run.id, events=staged_events)
                finalizing_success = False
            except Exception as exc:
                raise _ToolCommitFailure from exc
        except _ToolCommitFailure as exc:
            await self._recover_failed_commit(run_id=run_id, tool_call_id=tool_call_id)
            self._record(
                tool_name=tool_name,
                outcome="failed",
                error_code="tool_commit_failed",
                started_at=started_at,
            )
            raise ApiError(
                code="tool_commit_failed",
                message="Tool result could not be committed.",
                status=503,
                details={"fatal": True},
            ) from exc
        except asyncio.CancelledError as exc:
            if tool_scope is not None:
                await asyncio.shield(_rollback_tool_scope(tool_scope, exc=exc))
                tool_scope = None
                await asyncio.shield(
                    self._fail_persisted_started_tool_call(
                        tool_call_id=tool_call_id,
                        error_code="cancelled",
                    )
                )
            elif finalizing_success:
                await asyncio.shield(self._recover_failed_commit(run_id=run_id, tool_call_id=tool_call_id))
            elif tool_call_id is not None and not tool_started_committed:
                await asyncio.shield(self._recover_failed_commit(run_id=run_id, tool_call_id=tool_call_id))
            else:
                await asyncio.shield(
                    self._fail_persisted_started_tool_call(
                        tool_call_id=tool_call_id,
                        error_code="cancelled",
                    )
                )
            raise
        except ApiError as exc:
            await _rollback_tool_scope(tool_scope, exc=exc)
            tool_scope = None
            if tool_call_id is not None and not tool_started_committed:
                await self._recover_failed_commit(run_id=run_id, tool_call_id=tool_call_id)
                raise ApiError(
                    code="tool_commit_failed",
                    message="Tool start could not be committed.",
                    status=503,
                    details={"fatal": True},
                ) from exc
            failed_call = await self._reload_started_tool_call(tool_call_id=tool_call_id, fallback=tool_call)
            if failed_call is not None:
                await self.repository.fail_tool_call(tool_call=failed_call, completed_at=_utcnow(), error_code=exc.code)
                await self._append_tool_failed_event(tool_call=failed_call, error_code=exc.code)
            self._record(tool_name=tool_name, outcome="failed", error_code=exc.code, started_at=started_at)
            raise
        except TimeoutError as exc:
            await _rollback_tool_scope(tool_scope, exc=exc)
            tool_scope = None
            if tool_call_id is not None and not tool_started_committed:
                await self._recover_failed_commit(run_id=run_id, tool_call_id=tool_call_id)
                raise ApiError(
                    code="tool_commit_failed",
                    message="Tool start could not be committed.",
                    status=503,
                    details={"fatal": True},
                ) from exc
            failed_call = await self._reload_started_tool_call(tool_call_id=tool_call_id, fallback=tool_call)
            if failed_call is not None:
                await self.repository.fail_tool_call(tool_call=failed_call, completed_at=_utcnow(), error_code="timeout")
                await self._append_tool_failed_event(tool_call=failed_call, error_code="timeout")
            self._record(tool_name=tool_name, outcome="failed", error_code="timeout", started_at=started_at)
            raise ApiError(code="timeout", message="Tool execution timed out.", status=504) from exc
        except Exception as exc:
            log_agent_runtime_event(
                "agent.tool.unexpected_failure",
                run_id=str(run_id),
                tool_name=tool_name,
                exception_type=type(exc).__name__,
            )
            await _rollback_tool_scope(tool_scope, exc=exc)
            tool_scope = None
            if tool_call_id is not None and not tool_started_committed:
                await self._recover_failed_commit(run_id=run_id, tool_call_id=tool_call_id)
                raise ApiError(
                    code="tool_commit_failed",
                    message="Tool start could not be committed.",
                    status=503,
                    details={"fatal": True},
                ) from exc
            failed_call = await self._reload_started_tool_call(tool_call_id=tool_call_id, fallback=tool_call)
            if failed_call is not None:
                await self.repository.fail_tool_call(tool_call=failed_call, completed_at=_utcnow(), error_code="tool_failed")
                await self._append_tool_failed_event(tool_call=failed_call, error_code="tool_failed")
            self._record(tool_name=tool_name, outcome="failed", error_code="tool_failed", started_at=started_at)
            raise ApiError(code="tool_failed", message="Tool execution failed.", status=500) from exc

        self._record(tool_name=tool_name, outcome="completed", error_code="", started_at=started_at)
        return ToolExecutionResult(
            tool_call=completed,
            canonical_output=output_payload,
            tool_result=tool_result,
        )

    async def _reload_started_tool_call(
        self,
        *,
        tool_call_id: UUID | None,
        fallback: AgentToolCall | None,
    ) -> AgentToolCall | None:
        get_tool_call = getattr(self.repository, "get_tool_call", None)
        if tool_call_id is not None and callable(get_tool_call):
            return cast(AgentToolCall | None, await get_tool_call(tool_call_id=tool_call_id))
        return fallback

    async def _recover_failed_commit(self, *, run_id: UUID, tool_call_id: UUID | None) -> None:
        rollback = getattr(self.repository, "rollback", None)
        if callable(rollback):
            await rollback()
        await self.repository.get_run(run_id=run_id)
        if tool_call_id is None:
            return
        get_tool_call = getattr(self.repository, "get_tool_call", None)
        if not callable(get_tool_call):
            return
        tool_call = await get_tool_call(tool_call_id=tool_call_id)
        if tool_call is None:
            return
        await self._fail_persisted_started_tool_call(
            tool_call_id=tool_call_id,
            error_code="tool_commit_failed",
            loaded_tool_call=tool_call,
        )

    async def _fail_persisted_started_tool_call(
        self,
        *,
        tool_call_id: UUID | None,
        error_code: str,
        loaded_tool_call: AgentToolCall | None = None,
    ) -> None:
        if tool_call_id is None:
            return
        try:
            tool_call = loaded_tool_call
            if tool_call is None:
                get_tool_call = getattr(self.repository, "get_tool_call", None)
                if not callable(get_tool_call):
                    return
                tool_call = await get_tool_call(tool_call_id=tool_call_id)
            if tool_call is None or tool_call.status != "started":
                return
            failed_call = await self.repository.fail_tool_call(
                tool_call=tool_call,
                completed_at=_utcnow(),
                error_code=error_code,
            )
            await self._append_tool_failed_event(tool_call=failed_call, error_code=error_code)
        except Exception:
            LOGGER.exception("Failed to persist terminal tool state after interrupted execution.")

    @staticmethod
    def _enforce_actor_scope(*, args: dict[str, Any]) -> None:
        if "owner_user_id" in args:
            raise ApiError(code="owner_scope_violation", message="Tool ownership is derived from the current actor.", status=403)

    def _record(self, *, tool_name: str, outcome: str, error_code: str, started_at: float) -> None:
        if self.metrics is not None:
            self.metrics.record_agent_tool(
                tool_name=tool_name,
                outcome=outcome,
                error_code=error_code,
                duration_ms=(perf_counter() - started_at) * 1000,
            )

    async def _append_tool_failed_event(self, *, tool_call: AgentToolCall, error_code: str) -> None:
        run = await self.repository.get_run(run_id=tool_call.run_id)
        if run is None:
            return
        payload = {
            "tool_call_id": str(tool_call.id),
            "tool_name": tool_call.tool_name,
            "call_id": tool_call.call_id,
            "error_code": error_code,
            "label": self.policy.event_label(tool_name=tool_call.tool_name, payload=tool_call.safe_args),
        }
        try:
            contract = self.registry.get(tool_call.tool_name)
            payload = self.policy.enrich_event(
                payload,
                event_type="tool.failed",
                tool_name=tool_call.tool_name,
                effect_scope=self.policy.effective_effect_scope(
                    tool_name=tool_call.tool_name,
                    args=tool_call.safe_args,
                    default=contract.effect_scope,
                ),
            )
        except ApiError:
            payload = self.policy.enrich_event(
                payload,
                event_type="tool.failed",
                tool_name=tool_call.tool_name,
            )
        await self._publish_optimistic_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.failed",
            payload=payload,
        )
        await self._append_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.failed",
            payload=payload,
        )

    async def _append_tool_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            await self.event_sink.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
            return
        await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)

    async def _stage_tool_events(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        events: tuple[tuple[str, dict[str, Any]], ...],
    ) -> tuple[Any, ...]:
        if self.event_sink is not None:
            stage_events = getattr(self.event_sink, "stage_events", None)
            if callable(stage_events):
                return cast(tuple[Any, ...], await stage_events(thread_id=thread_id, run_id=run_id, events=events))
            await self.event_sink.append_events(thread_id=thread_id, run_id=run_id, events=events)
            return ()
        appended: list[Any] = []
        for event_type, payload in events:
            appended.append(
                await self.repository.append_event(
                    thread_id=thread_id,
                    run_id=run_id,
                    event_type=event_type,
                    payload=payload,
                )
            )
        return tuple(appended)

    async def _finalize_staged_tool_events(self, *, run_id: UUID, events: tuple[Any, ...]) -> None:
        if self.event_sink is None:
            return
        finalize_staged_events = getattr(self.event_sink, "finalize_staged_events", None)
        if callable(finalize_staged_events):
            await finalize_staged_events(run_id=run_id, events=events)

    async def _publish_optimistic_tool_event(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        dedupe_key = _tool_live_dedupe_key(run_id=run_id, event_type=event_type, payload=payload)
        if not dedupe_key:
            return
        if self.event_sink is not None:
            await self.event_sink.publish_application_event(
                thread_id=thread_id,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                dedupe_key=dedupe_key,
                optimistic=True,
                durable=False,
            )
            return
        if self.transient_stream is None:
            return
        try:
            await self.transient_stream.publish_application_event(
                thread_id=thread_id,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                dedupe_key=dedupe_key,
                optimistic=True,
                durable=False,
            )
        except Exception:
            LOGGER.warning("Failed to publish optimistic agent tool event.", exc_info=True)


async def _maybe_await(
    value: Awaitable[ToolResult] | ToolResult,
) -> ToolResult:
    if hasattr(value, "__await__"):
        return await value
    return value


class _NoopToolScope:
    async def __aenter__(self) -> _NoopToolScope:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False


async def _open_tool_scope(repository: Any) -> Any:
    begin_nested = getattr(repository, "begin_nested", None)
    scope = begin_nested() if callable(begin_nested) else _NoopToolScope()
    await scope.__aenter__()
    return scope


async def _commit_tool_scope(scope: Any | None) -> None:
    if scope is not None:
        await scope.__aexit__(None, None, None)


async def _rollback_tool_scope(scope: Any | None, *, exc: BaseException) -> None:
    if scope is not None:
        await scope.__aexit__(type(exc), exc, exc.__traceback__)


def _tool_live_dedupe_key(*, run_id: UUID, event_type: str, payload: dict[str, Any]) -> str:
    if event_type not in {"tool.started", "tool.completed", "tool.failed"}:
        return ""
    tool_call_id = str(payload.get("tool_call_id") or payload.get("call_id") or "").strip()
    return f"{run_id}:{event_type}:{tool_call_id}" if tool_call_id else ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
