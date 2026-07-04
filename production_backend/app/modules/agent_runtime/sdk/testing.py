from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ....core.errors import ApiError
from .runner import SdkNodeRequest, SdkNodeResult, SdkRunnerBackend


@dataclass(frozen=True)
class ScriptedToolInvocation:
    contract_name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScriptedSdkResponse:
    final_text: str = ""
    tool_invocations: tuple[ScriptedToolInvocation, ...] = ()
    tool_calls: tuple[dict[str, Any], ...] = ()
    action_proposals: tuple[dict[str, Any], ...] = ()
    artifacts: tuple[dict[str, Any], ...] = ()
    safety_decision: dict[str, Any] | None = None
    expected_available_tools: tuple[str, ...] = ()


class ScriptedSdkBackend(SdkRunnerBackend):
    def __init__(self, responses: list[ScriptedSdkResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[SdkNodeRequest] = []

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.requests.append(request)
        if not self._responses:
            raise ApiError(code="sdk_mock_exhausted", message="Scripted SDK backend has no response left.", status=500)
        response = self._responses.pop(0)
        _assert_expected_tools(response=response, request=request)
        invoked_tool_calls = await _invoke_scripted_tools(response=response, request=request)
        return SdkNodeResult(
            final_text=response.final_text,
            tool_calls=[*response.tool_calls, *invoked_tool_calls],
            action_proposals=[*response.action_proposals],
            artifacts=[*response.artifacts],
            safety_decision=response.safety_decision,
        )


def scripted_tool_invocation(contract_name: str, args: dict[str, Any] | None = None) -> ScriptedToolInvocation:
    return ScriptedToolInvocation(contract_name=contract_name, args=args or {})


def scripted_sdk_response(
    *,
    final_text: str = "",
    tool_invocations: tuple[ScriptedToolInvocation, ...] = (),
    tool_calls: tuple[dict[str, Any], ...] = (),
    action_proposals: tuple[dict[str, Any], ...] = (),
    artifacts: tuple[dict[str, Any], ...] = (),
    safety_decision: dict[str, Any] | None = None,
    expected_available_tools: tuple[str, ...] = (),
) -> ScriptedSdkResponse:
    return ScriptedSdkResponse(
        final_text=final_text,
        tool_invocations=tool_invocations,
        tool_calls=tool_calls,
        action_proposals=action_proposals,
        artifacts=artifacts,
        safety_decision=safety_decision,
        expected_available_tools=expected_available_tools,
    )


def _assert_expected_tools(*, response: ScriptedSdkResponse, request: SdkNodeRequest) -> None:
    missing = [tool_name for tool_name in response.expected_available_tools if tool_name not in request.tool_names]
    if missing:
        raise ApiError(
            code="sdk_mock_contract_mismatch",
            message="Scripted SDK expected unavailable tool contracts.",
            status=500,
            details={"missing_tool_names": missing},
        )


async def _invoke_scripted_tools(*, response: ScriptedSdkResponse, request: SdkNodeRequest) -> list[dict[str, Any]]:
    tools_by_contract = {tool.contract_name: tool for tool in request.tools}
    tool_calls: list[dict[str, Any]] = []
    for invocation in response.tool_invocations:
        tool = tools_by_contract.get(invocation.contract_name)
        if tool is None:
            raise ApiError(
                code="sdk_mock_contract_mismatch",
                message="Scripted SDK attempted to invoke an unavailable tool contract.",
                status=500,
                details={"tool_name": invocation.contract_name},
            )
        args_json = json.dumps(invocation.args, sort_keys=True)
        output_json = await tool.invoke_json(args_json)
        tool_calls.append(
            {
                "tool_name": invocation.contract_name,
                "status": "completed",
                "args": invocation.args,
                "safe_output": _json_object_or_raw(output_json),
            }
        )
    return tool_calls


def _json_object_or_raw(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
