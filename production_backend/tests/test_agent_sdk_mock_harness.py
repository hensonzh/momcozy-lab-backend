import asyncio
import types

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.modules.agent_runtime.sdk import (
    OpenAIAgentsSdkRunner,
    OpenAIResponsesRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    SdkToolInvocationResult,
    ScriptedSdkBackend,
    create_agent_sdk_runner,
    scripted_sdk_response,
    scripted_tool_invocation,
    sdk_tool_name,
)
from production_backend.app.modules.agent_runtime.sdk.runner import _build_function_tool, _build_run_config


def test_scripted_sdk_backend_invokes_application_tool_contracts() -> None:
    invoked_args: list[str] = []

    async def invoke_json(args_json: str) -> SdkToolInvocationResult:
        invoked_args.append(args_json)
        return SdkToolInvocationResult(output_json='{"profile":{"display_name":"Mai"}}')

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


def test_scripted_sdk_backend_keeps_private_model_output_out_of_observed_trace() -> None:
    async def invoke_json(_args_json: str) -> SdkToolInvocationResult:
        return SdkToolInvocationResult(
            output_json='{"entry":{"content":"private diary content"},"status":"entry_read"}',
            safe_output_json='{"entry_date":"2026-07-12","status":"entry_read"}',
        )

    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="I found the entry.",
                tool_invocations=(scripted_tool_invocation("pregnancy_diary.manage", {"action": "read"}),),
                expected_available_tools=("pregnancy_diary.manage",),
            )
        ]
    )
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Use tools.",
        model_input=[{"role": "user", "content": "read my diary"}],
        tool_names=("pregnancy_diary.manage",),
        tools=(
            SdkToolDefinition(
                contract_name="pregnancy_diary.manage",
                sdk_name=sdk_tool_name("pregnancy_diary.manage"),
                description="Read diary.",
                params_json_schema={"type": "object", "properties": {}},
                invoke=invoke_json,
            ),
        ),
    )

    result = asyncio.run(OpenAIAgentsSdkRunner(backend=backend).run_reasoning(request))

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
        asyncio.run(OpenAIAgentsSdkRunner(backend=backend).run_reasoning(request))

    assert exc_info.value.code == "sdk_mock_contract_mismatch"
    assert exc_info.value.details == {"tool_name": "profile.read"}


def test_agents_sdk_function_tool_does_not_convert_fatal_commit_failure_to_model_output() -> None:
    class FakeFunctionTool:
        def __init__(self, **kwargs: object) -> None:
            self.on_invoke_tool = kwargs["on_invoke_tool"]

    async def invoke_json(_args_json: str) -> SdkToolInvocationResult:
        raise ApiError(
            code="tool_commit_failed",
            message="Tool result could not be committed.",
            status=503,
        )

    tool = _build_function_tool(
        agents_module=types.SimpleNamespace(FunctionTool=FakeFunctionTool),
        definition=SdkToolDefinition(
            contract_name="pregnancy_diary.manage",
            sdk_name="pregnancy_diary_manage",
            description="Create diary entry.",
            params_json_schema={"type": "object", "properties": {}},
            invoke=invoke_json,
        ),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(tool.on_invoke_tool(None, "{}"))

    assert exc_info.value.code == "tool_commit_failed"


def test_agent_sdk_runner_preserves_sanitized_provider_error_details() -> None:
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
        asyncio.run(OpenAIAgentsSdkRunner(backend=FailingBackend()).run_reasoning(request))

    assert exc_info.value.code == "sdk_bad_request"
    assert exc_info.value.details["exception_type"] == "ProviderBadRequest"
    assert exc_info.value.details["provider_status_code"] == 400
    assert "secret-token" not in exc_info.value.details["message_excerpt"]
    assert "[redacted]" in exc_info.value.details["message_excerpt"]


def test_agent_sdk_runner_factory_selects_responses_contract_for_openai() -> None:
    runner = create_agent_sdk_runner(
        settings=Settings(
            app_env="test",
            openai_api_key="openai-key",
            openai_model="gpt-5.6-terra",
            openai_reasoning_effort="low",
            openai_responses_store=False,
            openai_agent_use_responses=True,
        )
    )

    assert runner.provider == "openai"
    assert isinstance(runner, OpenAIResponsesRunner)
    assert runner.model == "gpt-5.6-terra"
    assert runner.reasoning_effort == "low"
    assert runner.text_verbosity == "low"
    assert runner.store_responses is False
    assert runner.use_responses is True


def test_agent_model_runner_factory_allows_low_latency_reasoning_override() -> None:
    runner = create_agent_sdk_runner(
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


def test_agent_sdk_runner_factory_keeps_explicit_openai_sdk_rollback_path() -> None:
    runner = create_agent_sdk_runner(
        settings=Settings(
            app_env="test",
            openai_api_key="openai-key",
            openai_model="gpt-test",
            openai_agent_use_responses=False,
        )
    )

    assert isinstance(runner, OpenAIAgentsSdkRunner)
    assert runner.provider == "openai"
    assert runner.use_responses is False


def test_agents_sdk_legacy_run_config_falls_back_to_tracing_disabled() -> None:
    class LegacyRunConfig:
        def __init__(self, *, tracing_disabled: bool) -> None:
            self.tracing_disabled = tracing_disabled

    run_config = _build_run_config(
        agents_module=types.SimpleNamespace(RunConfig=LegacyRunConfig),
        request=SdkNodeRequest(
            run_id="run_1",
            thread_id="thread_1",
            actor_user_id="user_1",
            instructions="Use tools.",
            model_input=[],
        ),
        trace_enabled=True,
        api_key="",
        base_url="",
        use_responses=False,
        buffer_streamed_tool_calls=False,
    )

    assert run_config.tracing_disabled is True


def test_agents_sdk_run_config_fails_closed_when_private_tracing_cannot_be_enforced() -> None:
    class BrokenRunConfig:
        def __init__(self, **_kwargs: object) -> None:
            raise TypeError("unsupported")

    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Use tools.",
        model_input=[],
    )
    for agents_module in (
        types.SimpleNamespace(),
        types.SimpleNamespace(RunConfig=BrokenRunConfig),
    ):
        with pytest.raises(ApiError) as exc_info:
            _build_run_config(
                agents_module=agents_module,
                request=request,
                trace_enabled=True,
                api_key="",
                base_url="",
                use_responses=False,
                buffer_streamed_tool_calls=False,
            )

        assert exc_info.value.code == "sdk_trace_privacy_not_supported"
