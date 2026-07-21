from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.agent_runtime.tools.result import ToolResult
from app.agent_runtime.tools.executor import ToolHandlerContext

from app.agents.cozymate.tools.result import cozymate_tool_result_from_payload

_MAX_MEDIA_VOICE_ITEMS = 2
_DEVICE_GUIDANCE_IMAGE_SPOKEN_LABEL = "我放了一张当前步骤的对照图，你可以边看图边完成这一步。"
_MILK_ANALYSIS_PLAN_TTL = timedelta(minutes=30)
_ToolOperationOutput = ToolResult | dict[str, Any]


class _StandardToolHandler:
    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        resolved = await self.execute(context)
        if isinstance(resolved, ToolResult):
            if resolved.audit_output is None:
                raise TypeError("ToolResult returned by a tool handler requires audit_output.")
            return resolved
        return cozymate_tool_result_from_payload(tool_name=context.tool_name, output=resolved)

    async def execute(self, context: ToolHandlerContext) -> _ToolOperationOutput:
        raise NotImplementedError
