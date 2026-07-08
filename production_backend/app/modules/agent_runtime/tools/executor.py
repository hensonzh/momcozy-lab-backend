from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID

from ....core.errors import ApiError
from ....core.metrics import RequestMetrics
from ....infrastructure.object_storage.base import ObjectStorage
from ...auth import CurrentUser, PermissionPolicy
from ..event_stream.sink import AgentEventSink
from ..models import AgentToolCall
from ..payloads import DEFAULT_MAX_INLINE_PAYLOAD_BYTES, maybe_externalize_json_payload
from ..repository import AgentRuntimeRepository
from .contracts import ToolContract
from .output_policy import strip_instructional_tool_output_keys
from .registry import ToolContractRegistry
from .schemas import validate_tool_input


@dataclass(frozen=True)
class ToolHandlerContext:
    actor: CurrentUser
    run_id: UUID
    tool_name: str
    call_id: str
    args: dict[str, Any]


ToolHandler = Callable[[ToolHandlerContext], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_call: AgentToolCall
    safe_output: dict[str, Any]


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolContractRegistry,
        repository: AgentRuntimeRepository,
        permission_policy: PermissionPolicy | None = None,
        handlers: dict[str, ToolHandler] | None = None,
        event_sink: AgentEventSink | None = None,
        metrics: RequestMetrics | None = None,
        object_storage: ObjectStorage | None = None,
        max_inline_output_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.permission_policy = permission_policy or PermissionPolicy()
        self.handlers = handlers or {}
        self.event_sink = event_sink
        self.metrics = metrics
        self.object_storage = object_storage
        self.max_inline_output_bytes = max_inline_output_bytes

    async def execute(
        self,
        *,
        actor: CurrentUser,
        run_id: UUID,
        tool_name: str,
        call_id: str,
        args: dict[str, Any],
    ) -> ToolExecutionResult:
        started_at = perf_counter()
        tool_call: AgentToolCall | None = None
        try:
            contract = self.registry.get(tool_name)
            self._authorize(actor=actor, contract=contract, args=args)
            validate_tool_input(schema_ref=contract.input_schema_ref, value=args)
            handler = self.handlers.get(tool_name)
            if handler is None:
                raise ApiError(code="unsupported_operation", message="Tool handler is not configured.", status=501)
            run = await self.repository.get_run(run_id=run_id)
            if run is None:
                raise ApiError(code="not_found", message="Agent run not found.", status=404)

            tool_call = await self.repository.start_tool_call(
                run_id=run_id,
                tool_name=tool_name,
                call_id=call_id,
                safe_args=_safe_payload(args),
                started_at=_utcnow(),
            )
            await self._append_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.started",
                payload={"tool_call_id": str(tool_call.id), "tool_name": tool_name, "call_id": call_id},
            )
            result = await asyncio.wait_for(
                _maybe_await(
                    handler(
                        ToolHandlerContext(
                            actor=actor,
                            run_id=run_id,
                            tool_name=tool_name,
                            call_id=call_id,
                            args=args,
                        )
                    )
                ),
                timeout=contract.timeout_seconds,
            )
        except ApiError as exc:
            if tool_call is not None:
                await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code=exc.code)
                await self._append_tool_failed_event(tool_call=tool_call, error_code=exc.code)
            self._record(tool_name=tool_name, outcome="failed", error_code=exc.code, started_at=started_at)
            raise
        except TimeoutError as exc:
            if tool_call is not None:
                await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code="timeout")
                await self._append_tool_failed_event(tool_call=tool_call, error_code="timeout")
            self._record(tool_name=tool_name, outcome="failed", error_code="timeout", started_at=started_at)
            raise ApiError(code="timeout", message="Tool execution timed out.", status=504) from exc
        except Exception as exc:
            if tool_call is not None:
                await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code="tool_failed")
                await self._append_tool_failed_event(tool_call=tool_call, error_code="tool_failed")
            self._record(tool_name=tool_name, outcome="failed", error_code="tool_failed", started_at=started_at)
            raise ApiError(code="tool_failed", message="Tool execution failed.", status=500) from exc

        safe_output = strip_instructional_tool_output_keys(_safe_payload(result))
        externalized_output = await maybe_externalize_json_payload(
            payload=safe_output,
            object_storage=self.object_storage,
            run_id=run.id,
            payload_kind="tool-outputs",
            key_suffix=str(tool_call.id),
            max_inline_bytes=self.max_inline_output_bytes,
        )
        completed = await self.repository.complete_tool_call(tool_call=tool_call, completed_at=_utcnow())
        output = await self.repository.create_tool_output(
            tool_call_id=completed.id,
            safe_output=externalized_output.inline_payload,
            raw_output_ref=externalized_output.raw_payload_ref,
        )
        completed_payload = {
            "tool_call_id": str(completed.id),
            "tool_output_id": str(output.id),
            "tool_name": completed.tool_name,
            "call_id": completed.call_id,
        }
        await self._append_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.completed",
            payload=completed_payload,
        )
        self._record(tool_name=tool_name, outcome="completed", error_code="", started_at=started_at)
        return ToolExecutionResult(tool_call=completed, safe_output=externalized_output.inline_payload)

    def _authorize(self, *, actor: CurrentUser, contract: ToolContract, args: dict[str, Any]) -> None:
        self.permission_policy.require_permission(actor, contract.required_permission)
        if contract.owner_scope == "actor" and args.get("owner_user_id") not in {None, "", str(actor.user_id), actor.user_id}:
            raise ApiError(code="owner_scope_violation", message="Tool arguments are outside the current user scope.", status=403)

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
        await self._append_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.failed",
            payload={
                "tool_call_id": str(tool_call.id),
                "tool_name": tool_call.tool_name,
                "call_id": tool_call.call_id,
                "error_code": error_code,
            },
        )

    async def _append_tool_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            await self.event_sink.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
            return
        await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)


async def _maybe_await(value: Awaitable[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    if hasattr(value, "__await__"):
        return await value
    return value


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redacted_value(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    return value


def _redacted_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("authorization", "password", "secret", "token", "api_key")):
        return "[redacted]"
    return _safe_payload(value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
