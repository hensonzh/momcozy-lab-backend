from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ....core.errors import ApiError
from ...auth import CurrentUser, PermissionPolicy
from ..models import AgentToolCall
from ..repository import AgentRuntimeRepository
from .contracts import ToolContract
from .registry import ToolContractRegistry


ToolHandler = Callable[[CurrentUser, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


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
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.permission_policy = permission_policy or PermissionPolicy()
        self.handlers = handlers or {}

    async def execute(
        self,
        *,
        actor: CurrentUser,
        run_id: UUID,
        tool_name: str,
        call_id: str,
        args: dict[str, Any],
    ) -> ToolExecutionResult:
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
        try:
            result = await asyncio.wait_for(_maybe_await(handler(actor, args)), timeout=contract.timeout_seconds)
        except ApiError as exc:
            await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code=exc.code)
            raise
        except TimeoutError as exc:
            await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code="timeout")
            raise ApiError(code="timeout", message="Tool execution timed out.", status=504) from exc
        except Exception as exc:
            await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code="tool_failed")
            raise ApiError(code="tool_failed", message="Tool execution failed.", status=500) from exc

        safe_output = _safe_payload(result)
        completed = await self.repository.complete_tool_call(tool_call=tool_call, completed_at=_utcnow())
        await self.repository.create_tool_output(tool_call_id=completed.id, safe_output=safe_output)
        return ToolExecutionResult(tool_call=completed, safe_output=safe_output)

    def _authorize(self, *, actor: CurrentUser, contract: ToolContract, args: dict[str, Any]) -> None:
        self.permission_policy.require_permission(actor, contract.required_permission)
        if contract.owner_scope == "actor" and args.get("owner_user_id") not in {None, "", str(actor.user_id), actor.user_id}:
            raise ApiError(code="owner_scope_violation", message="Tool arguments are outside the current user scope.", status=403)


async def _maybe_await(value):
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
