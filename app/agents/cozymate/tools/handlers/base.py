from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from app.core.errors import ApiError
from app.agent_runtime.tools.result import ToolResult
from app.agent_runtime.tools.executor import DEFERRED_AGENT_EVENTS_KEY, ToolHandlerContext

_MAX_MEDIA_VOICE_ITEMS = 2
_DEVICE_GUIDANCE_IMAGE_SPOKEN_LABEL = "我放了一张当前步骤的对照图，你可以边看图边完成这一步。"
_MILK_ANALYSIS_PLAN_TTL = timedelta(minutes=30)
_MUTATION_TOOL_SUFFIXES = ("_mutate", "_create", "_update", "_delete")
_ToolOperationOutput = ToolResult | dict[str, Any]


class _StandardToolHandler:
    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        resolved = await self.execute(context)
        if isinstance(resolved, ToolResult):
            return resolved
        if context.tool_name.endswith(_MUTATION_TOOL_SUFFIXES):
            operation = str(context.args.get("operation") or "").strip()
            if operation:
                resolved = {"operation": operation, **resolved}
        canonical_output = dict(resolved)
        deferred_events = _deferred_events(canonical_output.pop(DEFERRED_AGENT_EVENTS_KEY, None))
        return ToolResult.json(
            canonical_output,
            deferred_events=deferred_events,
        )

    async def execute(self, context: ToolHandlerContext) -> _ToolOperationOutput:
        raise NotImplementedError


class _OperationDispatchToolHandler(_StandardToolHandler):
    def __init__(self, *, operations: Mapping[str, _StandardToolHandler]) -> None:
        self.operations = dict(operations)

    async def execute(self, context: ToolHandlerContext) -> _ToolOperationOutput:
        operation = str(context.args.get("operation") or "").strip()
        handler = self.operations.get(operation)
        if handler is None:
            raise ApiError(code="unsupported_operation", message="Tool operation is not supported.", status=422)
        return await handler.execute(context)


def _deferred_events(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    events: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        payload = item.get("payload")
        if event_type and isinstance(payload, dict):
            events.append({"event_type": event_type, "payload": dict(payload)})
    return tuple(events)
