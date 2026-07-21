import asyncio
import pytest
from app.core.errors import ApiError
from app.core.settings import Settings
from app.agent_runtime.tools.result import ToolResult, ToolTextOutput
from app.agent_runtime.providers import (
    OpenAIResponsesRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    ScriptedSdkBackend,
    create_agent_model_runner,
    scripted_sdk_response,
    scripted_tool_invocation,
    sdk_tool_name,
)


def test_scripted_sdk_backend_invokes_application_tool_contracts() -> None:
    invoked_args: list[str] = []

    async def invoke_json(args_json: str) -> ToolResult:
        invoked_args.append(args_json)
        return ToolResult.json({"profile": {"display_name": "Mai"}})

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
                invoke=invoke_json,
            ),
        ),
    )

    result = asyncio.run(OpenAIResponsesRunner(backend=backend).run_reasoning(request))

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


def test_scripted_sdk_backend_keeps_private_model_output_out_of_observed_trace() -> None:
    async def invoke_json(_args_json: str) -> ToolResult:
        return ToolResult(
            output=(
                ToolTextOutput(text='{"entry":{"content":"private diary content"},"status":"entry_read"}'),
            ),
            audit_output={"entry_date": "2026-07-12", "status": "entry_read"},
        )

    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="I found the entry.",
                tool_invocations=(scripted_tool_invocation("pregnancy_diary.query", {}),),
                expected_available_tools=("pregnancy_diary.query",),
            )
        ]
    )
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Use tools.",
        model_input=[{"role": "user", "content": "read my diary"}],
        tool_names=("pregnancy_diary.query",),
        tools=(
            SdkToolDefinition(
                contract_name="pregnancy_diary.query",
                sdk_name=sdk_tool_name("pregnancy_diary.query"),
                description="Read diary.",
                params_json_schema={"type": "object", "properties": {}},
                invoke=invoke_json,
            ),
        ),
    )

    result = asyncio.run(OpenAIResponsesRunner(backend=backend).run_reasoning(request))

    assert result.tool_calls[0]["safe_output"] == {
        "entry_date": "2026-07-12",
        "status": "entry_read",
    }


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
        asyncio.run(OpenAIResponsesRunner(backend=backend).run_reasoning(request))

    assert exc_info.value.code == "sdk_mock_contract_mismatch"
    assert exc_info.value.details == {"tool_name": "profile.read"}


def test_responses_runner_preserves_sanitized_provider_error_details() -> None:
    class ProviderBadRequest(Exception):
        status_code = 400

    class FailingBackend:
        async def run(self, _request: SdkNodeRequest) -> object:
            raise ProviderBadRequest("provider rejected request with api_key=secret-token")

    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Use tools.",
        model_input=[{"role": "user", "content": "hello"}],
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(OpenAIResponsesRunner(backend=FailingBackend()).run_reasoning(request))

    assert exc_info.value.code == "sdk_bad_request"
    assert exc_info.value.details["exception_type"] == "ProviderBadRequest"
    assert exc_info.value.details["provider_status_code"] == 400
    assert "secret-token" not in exc_info.value.details["message_excerpt"]
    assert "[redacted]" in exc_info.value.details["message_excerpt"]


def test_agent_model_runner_factory_selects_responses_contract_for_openai() -> None:
    runner = create_agent_model_runner(
        settings=Settings(
            app_env="test",
            openai_api_key="openai-key",
            openai_model="gpt-5.6-terra",
            openai_reasoning_effort="low",
            openai_responses_store=False,
        )
    )

    assert runner.provider == "openai"
    assert isinstance(runner, OpenAIResponsesRunner)
    assert runner.model == "gpt-5.6-terra"
    assert runner.reasoning_effort == "low"
    assert runner.text_verbosity == "low"
    assert runner.store_responses is False


def test_agent_model_runner_factory_allows_low_latency_reasoning_override() -> None:
    runner = create_agent_model_runner(
        settings=Settings(
            app_env="test",
            openai_api_key="openai-key",
            openai_reasoning_effort="low",
        ),
        model="gpt-5.4-nano",
        reasoning_effort="none",
    )

    assert isinstance(runner, OpenAIResponsesRunner)
    assert runner.model == "gpt-5.4-nano"
    assert runner.reasoning_effort == "none"
