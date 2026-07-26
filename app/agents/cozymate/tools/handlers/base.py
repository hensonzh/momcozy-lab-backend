from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from app.core.errors import ApiError
from app.agent_runtime.tools.result import ToolResult
from app.agent_runtime.tools.executor import ToolHandlerContext

from app.agents.cozymate.tools.result import cozymate_tool_result_from_payload

_MAX_MEDIA_VOICE_ITEMS = 2
_DEVICE_GUIDANCE_IMAGE_SPOKEN_LABEL = "我放了一张当前步骤的对照图，你可以边看图边完成这一步。"
_MILK_ANALYSIS_PLAN_TTL = timedelta(minutes=30)
_MUTATION_TOOL_SUFFIXES = ("_mutate", "_create", "_update", "_delete")
_ToolOperationOutput = ToolResult | dict[str, Any]


class _StandardToolHandler:
    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        resolved = await self.execute(context)
        if isinstance(resolved, ToolResult):
            if resolved.audit_output is None:
                raise TypeError("ToolResult returned by a tool handler requires audit_output.")
            return resolved
        if context.tool_name.endswith(_MUTATION_TOOL_SUFFIXES):
            operation = str(context.args.get("operation") or "").strip()
            if operation:
                resolved = {"operation": operation, **resolved}
        return cozymate_tool_result_from_payload(tool_name=context.tool_name, output=resolved)

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
