import asyncio
import sys
import types

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.modules.agent_runtime.sdk import (
    OpenAIAgentsSdkBackend,
    OpenAIAgentsSdkRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    ScriptedSdkBackend,
    create_agent_sdk_runner,
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


def test_agent_sdk_runner_factory_selects_minimax_provider_config() -> None:
    runner = create_agent_sdk_runner(
        settings=Settings(
            app_env="test",
            agent_model_provider="minimax",
            minimax_api_key="minimax-key",
            minimax_base_url="https://api.minimax.io/v1",
            minimax_model="MiniMax-M3",
            openai_agent_max_turns=6,
            openai_agent_timeout_seconds=30,
        )
    )

    assert runner.provider == "minimax"
    assert runner.model == "MiniMax-M3"
    assert runner.api_key == "minimax-key"
    assert runner.base_url == "https://api.minimax.io/v1"
    assert runner.use_responses is False
    assert runner.buffer_streamed_tool_calls is True
    assert runner.max_turns == 6
    assert runner.timeout_seconds == 30


def test_minimax_backend_uses_openai_compatible_model_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOpenAIProvider:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeRunConfig:
        instances: list["FakeRunConfig"] = []

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.instances.append(self)

    class FakeModelSettings:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeRunner:
        calls: list[dict[str, object]] = []

        @staticmethod
        async def run(agent: FakeAgent, model_input: str, **kwargs: object) -> object:
            FakeRunner.calls.append({"agent": agent, "model_input": model_input, "kwargs": kwargs})
            return types.SimpleNamespace(final_output="minimax ok")

    fake_agents = types.SimpleNamespace(
        Agent=FakeAgent,
        Runner=FakeRunner,
        RunConfig=FakeRunConfig,
        OpenAIProvider=FakeOpenAIProvider,
        ModelSettings=FakeModelSettings,
    )
    monkeypatch.setitem(sys.modules, "agents", fake_agents)

    backend = OpenAIAgentsSdkBackend(
        model="MiniMax-M3",
        provider="minimax",
        api_key="minimax-key",
        base_url="https://api.minimax.io/v1",
        use_responses=False,
        buffer_streamed_tool_calls=True,
    )
    result = asyncio.run(
        backend.run(
            SdkNodeRequest(
                run_id="run_1",
                thread_id="thread_1",
                actor_user_id="user_1",
                instructions="Use tools.",
                model_input=[{"role": "user", "content": "hello"}],
                trace_id="trace_1",
            )
        )
    )

    assert result.final_text == "minimax ok"
    assert FakeRunner.calls
    run_config = FakeRunner.calls[0]["kwargs"]["run_config"]
    assert isinstance(run_config, FakeRunConfig)
    model_provider = run_config.kwargs["model_provider"]
    assert isinstance(model_provider, FakeOpenAIProvider)
    assert model_provider.kwargs["api_key"] == "minimax-key"
    assert model_provider.kwargs["base_url"] == "https://api.minimax.io/v1"
    assert model_provider.kwargs["use_responses"] is False
    assert model_provider.kwargs["buffer_streamed_tool_calls"] is True
    agent = FakeRunner.calls[0]["agent"]
    assert isinstance(agent, FakeAgent)
    model_settings = agent.kwargs["model_settings"]
    assert isinstance(model_settings, FakeModelSettings)
    assert model_settings.kwargs["extra_body"] == {
        "thinking": {"type": "disabled"},
        "service_tier": "priority",
    }
    assert run_config.kwargs["trace_metadata"]["model_provider"] == "minimax"


def test_minimax_backend_strips_thinking_blocks_from_final_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOpenAIProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakeRunConfig:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakeAgent:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakeRunner:
        @staticmethod
        async def run(_agent: FakeAgent, _model_input: str, **_kwargs: object) -> object:
            return types.SimpleNamespace(final_output="<think>private reasoning</think>\n\n用户可见回复")

    fake_agents = types.SimpleNamespace(
        Agent=FakeAgent,
        Runner=FakeRunner,
        RunConfig=FakeRunConfig,
        OpenAIProvider=FakeOpenAIProvider,
    )
    monkeypatch.setitem(sys.modules, "agents", fake_agents)

    result = asyncio.run(
        OpenAIAgentsSdkBackend(
            model="MiniMax-M3",
            provider="minimax",
            api_key="minimax-key",
            base_url="https://api.minimaxi.com/v1",
            use_responses=False,
        ).run(
            SdkNodeRequest(
                run_id="run_1",
                thread_id="thread_1",
                actor_user_id="user_1",
                instructions="Use tools.",
                model_input=[{"role": "user", "content": "hello"}],
            )
        )
    )

    assert result.final_text == "用户可见回复"


def test_minimax_backend_strips_thinking_blocks_from_streamed_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOpenAIProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakeRunConfig:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakeAgent:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakeStreamedResult:
        final_output = "<think>hidden</think>\n\n可见回复"

        async def stream_events(self) -> object:
            for delta in ("<thi", "nk>hidden", "</think>\n\n可", "见回复"):
                yield types.SimpleNamespace(
                    type="raw_response_event",
                    data=types.SimpleNamespace(type="response.output_text.delta", delta=delta),
                )

    class FakeRunner:
        @staticmethod
        def run_streamed(_agent: FakeAgent, _model_input: str, **_kwargs: object) -> FakeStreamedResult:
            return FakeStreamedResult()

    fake_agents = types.SimpleNamespace(
        Agent=FakeAgent,
        Runner=FakeRunner,
        RunConfig=FakeRunConfig,
        OpenAIProvider=FakeOpenAIProvider,
    )
    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    deltas: list[str] = []

    async def on_text_delta(delta: str) -> None:
        deltas.append(delta)

    result = asyncio.run(
        OpenAIAgentsSdkBackend(
            model="MiniMax-M3",
            provider="minimax",
            api_key="minimax-key",
            base_url="https://api.minimaxi.com/v1",
            use_responses=False,
        ).run(
            SdkNodeRequest(
                run_id="run_1",
                thread_id="thread_1",
                actor_user_id="user_1",
                instructions="Use tools.",
                model_input=[{"role": "user", "content": "hello"}],
                on_text_delta=on_text_delta,
            )
        )
    )

    assert result.final_text == "可见回复"
    assert "".join(deltas) == "可见回复"
