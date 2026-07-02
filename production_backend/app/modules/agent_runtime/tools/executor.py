from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID

from ....core.metrics import RequestMetrics
from ....core.errors import ApiError
from ...auth import CurrentUser, PermissionPolicy
from ..models import AgentToolCall
from ..repository import AgentRuntimeRepository
from .contracts import ToolContract
from .registry import ToolContractRegistry


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
        metrics: RequestMetrics | None = None,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.permission_policy = permission_policy or PermissionPolicy()
        self.handlers = handlers or {}
        self.metrics = metrics

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
            handler = self.handlers.get(tool_name)
            if handler is None:
                raise ApiError(code="unsupported_operation", message="Tool handler is not configured.", status=501)

            tool_call = await self.repository.start_tool_call(
                run_id=run_id,
                tool_name=tool_name,
                call_id=call_id,
                safe_args=_safe_payload(args),
                started_at=_utcnow(),
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
            self._record(tool_name=tool_name, outcome="failed", error_code=exc.code, started_at=started_at)
            raise
        except TimeoutError as exc:
            if tool_call is not None:
                await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code="timeout")
            self._record(tool_name=tool_name, outcome="failed", error_code="timeout", started_at=started_at)
            raise ApiError(code="timeout", message="Tool execution timed out.", status=504) from exc
        except Exception as exc:
            if tool_call is not None:
                await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code="tool_failed")
            self._record(tool_name=tool_name, outcome="failed", error_code="tool_failed", started_at=started_at)
            raise ApiError(code="tool_failed", message="Tool execution failed.", status=500) from exc

        safe_output = _safe_payload(result)
        completed = await self.repository.complete_tool_call(tool_call=tool_call, completed_at=_utcnow())
        await self.repository.create_tool_output(tool_call_id=completed.id, safe_output=safe_output)
        self._record(tool_name=tool_name, outcome="completed", error_code="", started_at=started_at)
        return ToolExecutionResult(tool_call=completed, safe_output=safe_output)

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
