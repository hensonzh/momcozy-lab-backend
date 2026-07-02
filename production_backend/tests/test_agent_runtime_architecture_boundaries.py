import asyncio
import sys
import types
from importlib.machinery import ModuleSpec

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.modules.agent_runtime.graphs import default_graph_registry
from production_backend.app.modules.agent_runtime.prompts import ContextProjection, ModelInputBuilder
from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, SdkNodeRequest, SdkNodeResult
from production_backend.app.modules.agent_runtime.tools import default_tool_registry


def test_default_graph_registry_uses_langgraph_sdk_pattern() -> None:
    graph = default_graph_registry().get("momcozy-agent-v1")

    assert graph.runtime_pattern == "langgraph_sdk"
    assert "sdk_reasoning" in graph.node_names
    assert "confirmation_interrupt" in graph.node_names


def test_tool_contract_registry_declares_permission_confirmation_and_blocking_policy() -> None:
    registry = default_tool_registry()
    support_ticket = registry.get("support.ticket.propose")

    assert support_ticket.read_or_write == "write"
    assert support_ticket.owner_scope == "actor"
    assert support_ticket.requires_confirmation is True
    assert support_ticket.blocking_policy == "wait_for_confirmation"
    assert "profile.read" in registry.names_for_sdk()


def test_context_builder_keeps_stable_prompts_before_dynamic_projection() -> None:
    model_input = ModelInputBuilder().build(
        projection=ContextProjection(
            stable_system_prompt="system-v1",
            stable_developer_prompt="developer-v1",
            selected_conversation_history=[{"role": "user", "content": "history"}],
            current_state_projection={"run_id": "run_1"},
            fresh_business_facts={"profile": {"name": "Mai"}},
        ),
        current_user_message={"role": "user", "content": "hello"},
    )

    assert [item["role"] for item in model_input] == ["system", "developer", "user", "developer", "developer", "user"]
    assert model_input[0]["content"] == "system-v1"
    assert model_input[-1]["content"] == "hello"


def test_sdk_runner_uses_injected_backend_and_never_legacy_loop() -> None:
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[{"role": "user", "content": "hello"}],
        tool_names=("profile.read",),
    )

    result = asyncio.run(OpenAIAgentsSdkRunner(backend=FakeSdkBackend()).run_reasoning(request))

    assert result.final_text == "hello"
    assert result.tool_calls == [{"tool_name": "profile.read"}]


def test_sdk_runner_reports_missing_backend_as_dependency_error() -> None:
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[],
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(OpenAIAgentsSdkRunner().run_reasoning(request))

    assert exc_info.value.code == "dependency_not_configured"


def test_sdk_runner_uses_real_agents_sdk_shape_when_package_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_agents = types.ModuleType("agents")
    fake_agents.__spec__ = ModuleSpec("agents", loader=None)
    fake_agents.Agent = FakeAgentsSdkAgent
    fake_agents.Runner = FakeAgentsSdkRunner
    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[{"role": "user", "content": "hello"}],
    )

    result = asyncio.run(OpenAIAgentsSdkRunner(model="gpt-test").run_reasoning(request))

    assert result.final_text == "sdk final"
    assert FakeAgentsSdkAgent.created["model"] == "gpt-test"
    assert FakeAgentsSdkAgent.created["instructions"] == "Be concise."
    assert FakeAgentsSdkRunner.last_input == "user: hello"


def test_sdk_runner_records_backend_metrics() -> None:
    metrics = RequestMetrics()
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[{"role": "user", "content": "hello"}],
        tool_names=("profile.read",),
    )

    asyncio.run(OpenAIAgentsSdkRunner(backend=FakeSdkBackend(), metrics=metrics).run_reasoning(request))
    with pytest.raises(ApiError):
        asyncio.run(OpenAIAgentsSdkRunner(metrics=metrics).run_reasoning(request))

    sdk_metrics = metrics.snapshot()["agent_sdk"][0]
    assert sdk_metrics["node_name"] == "openai_agents_sdk"
    assert sdk_metrics["outcome_counts"]["completed"] == 1
    assert sdk_metrics["outcome_counts"]["failed"] == 1
    assert sdk_metrics["error_code_counts"]["dependency_not_configured"] == 1


class FakeSdkBackend:
    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        return SdkNodeResult(final_text="hello", tool_calls=[{"tool_name": request.tool_names[0]}])


class FakeAgentsSdkAgent:
    created = {}

    def __init__(self, *, name: str, instructions: str, model: str) -> None:
        self.name = name
        self.instructions = instructions
        self.model = model
        FakeAgentsSdkAgent.created = {"name": name, "instructions": instructions, "model": model}


class FakeAgentsSdkRunner:
    last_input = ""

    @staticmethod
    async def run(agent: FakeAgentsSdkAgent, input: str):
        FakeAgentsSdkRunner.last_input = input
        return FakeAgentsSdkResult(final_output="sdk final")


class FakeAgentsSdkResult:
    def __init__(self, *, final_output: str) -> None:
        self.final_output = final_output
