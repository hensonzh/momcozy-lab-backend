import asyncio

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.sdk import (
    OpenAIAgentsSdkRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    ScriptedSdkBackend,
    scripted_sdk_response,
    scripted_tool_invocation,
    sdk_tool_name,
)


def test_scripted_sdk_backend_invokes_application_tool_contracts() -> None:
    invoked_args: list[str] = []

    async def invoke_json(args_json: str) -> str:
        invoked_args.append(args_json)
        return '{"profile":{"display_name":"Mai"}}'

    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="I read your profile.",
                tool_invocations=(scripted_tool_invocation("profile.read", {"limit": 1}),),
                expected_available_tools=("profile.read",),
            )
        ]
    )
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Use tools.",
        model_input=[{"role": "user", "content": "read profile"}],
        tool_names=("profile.read",),
        tools=(
            SdkToolDefinition(
                contract_name="profile.read",
                sdk_name=sdk_tool_name("profile.read"),
                description="Read profile.",
                params_json_schema={"type": "object", "additionalProperties": False, "properties": {}},
                invoke_json=invoke_json,
            ),
        ),
    )

    result = asyncio.run(OpenAIAgentsSdkRunner(backend=backend).run_reasoning(request))

    assert result.final_text == "I read your profile."
    assert invoked_args == ['{"limit": 1}']
    assert result.tool_calls == [
        {
            "tool_name": "profile.read",
            "status": "completed",
            "args": {"limit": 1},
            "safe_output": {"profile": {"display_name": "Mai"}},
        }
    ]
    assert backend.requests[0].run_id == "run_1"


def test_scripted_sdk_backend_fails_on_missing_tool_contract() -> None:
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="",
                tool_invocations=(scripted_tool_invocation("profile.read"),),
            )
        ]
    )
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Use tools.",
        model_input=[{"role": "user", "content": "read profile"}],
        tool_names=(),
        tools=(),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(OpenAIAgentsSdkRunner(backend=backend).run_reasoning(request))

    assert exc_info.value.code == "sdk_mock_contract_mismatch"
    assert exc_info.value.details == {"tool_name": "profile.read"}
