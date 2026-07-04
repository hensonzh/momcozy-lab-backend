import asyncio
import sys
import types
from importlib.machinery import ModuleSpec

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.modules.agent_runtime.graphs import default_graph_registry
from production_backend.app.modules.agent_runtime.prompts import ContextProjection, ModelInputBuilder
from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, SdkNodeRequest, SdkNodeResult, SdkToolDefinition, sdk_tool_name
from production_backend.app.modules.agent_runtime.tools import default_tool_registry, tool_input_schema


def test_default_graph_registry_uses_langgraph_sdk_pattern() -> None:
    graph = default_graph_registry().get("momcozy-agent-v1")

    assert graph.runtime_pattern == "langgraph_sdk"
    assert "sdk_reasoning" in graph.node_names
    assert "confirmation_interrupt" in graph.node_names


def test_tool_contract_registry_declares_permission_confirmation_and_blocking_policy() -> None:
    registry = default_tool_registry()
    support_ticket = registry.get("support.ticket.propose")
    business_context = registry.get("business.context.read")

    assert support_ticket.read_or_write == "write"
    assert support_ticket.owner_scope == "actor"
    assert support_ticket.requires_confirmation is True
    assert support_ticket.blocking_policy == "wait_for_confirmation"
    assert business_context.read_or_write == "read"
    assert business_context.requires_confirmation is False
    assert business_context.audit_required is False
    milk_summary = registry.get("records.milk_summary.read")
    assert milk_summary.read_or_write == "read"
    assert milk_summary.owner_scope == "actor"
    assert milk_summary.requires_confirmation is False
    feeding_proposal = registry.get("records.feeding_record.propose")
    pumping_proposal = registry.get("records.pumping_record.propose")
    assert feeding_proposal.read_or_write == "write"
    assert feeding_proposal.requires_confirmation is True
    assert feeding_proposal.side_effect_level == "low"
    assert feeding_proposal.required_permission == "records:write:self"
    assert pumping_proposal.read_or_write == "write"
    assert pumping_proposal.requires_confirmation is True
    assert pumping_proposal.required_permission == "records:write:self"
    assert "profile.read" in registry.names_for_sdk()
    assert "business.context.read" in registry.names_for_sdk()
    plans_current = registry.get("plans.current.read")
    diary_recent = registry.get("diary.recent.read")
    assert plans_current.read_or_write == "read"
    assert plans_current.owner_scope == "actor"
    assert plans_current.requires_confirmation is False
    assert diary_recent.read_or_write == "read"
    assert diary_recent.owner_scope == "actor"
    assert diary_recent.requires_confirmation is False
    assert "records.milk_summary.read" in registry.names_for_sdk()
    assert "plans.current.read" in registry.names_for_sdk()
    assert "diary.recent.read" in registry.names_for_sdk()


def test_tool_input_schemas_are_explicit_and_registered_by_contract_ref() -> None:
    registry = default_tool_registry()
    profile_schema = tool_input_schema(registry.get("profile.read").input_schema_ref)
    business_schema = tool_input_schema(registry.get("business.context.read").input_schema_ref)
    support_schema = tool_input_schema(registry.get("support.ticket.propose").input_schema_ref)
    milk_schema = tool_input_schema(registry.get("records.milk_summary.read").input_schema_ref)
    plans_schema = tool_input_schema(registry.get("plans.current.read").input_schema_ref)
    diary_schema = tool_input_schema(registry.get("diary.recent.read").input_schema_ref)
    feeding_schema = tool_input_schema(registry.get("records.feeding_record.propose").input_schema_ref)
    pumping_schema = tool_input_schema(registry.get("records.pumping_record.propose").input_schema_ref)

    assert profile_schema == {
        "title": "ProfileContextQuery",
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    assert business_schema["additionalProperties"] is False
    assert business_schema["properties"]["limit"]["maximum"] == 20
    assert support_schema["required"] == ["issue_summary"]
    assert support_schema["additionalProperties"] is False
    assert "issue_summary" in support_schema["properties"]
    assert milk_schema["additionalProperties"] is False
    assert milk_schema["properties"]["days"]["maximum"] == 30
    assert milk_schema["properties"]["limit"]["maximum"] == 20
    assert plans_schema["additionalProperties"] is False
    assert plans_schema["properties"]["limit"]["maximum"] == 20
    assert diary_schema["additionalProperties"] is False
    assert diary_schema["properties"]["limit"]["maximum"] == 20
    assert feeding_schema["required"] == ["feed_time", "feed_type"]
    assert feeding_schema["properties"]["volume_ml"]["type"] == "number"
    assert pumping_schema["required"] == ["pump_start_time"]
    assert pumping_schema["properties"]["milk_volume_ml"]["type"] == "number"


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
    fake_agents.FunctionTool = FakeAgentsSdkFunctionTool
    fake_agents.Runner = FakeAgentsSdkRunner
    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[{"role": "user", "content": "hello"}],
    )

    result = asyncio.run(OpenAIAgentsSdkRunner(model="gpt-test", max_turns=3).run_reasoning(request))

    assert result.final_text == "sdk final"
    assert FakeAgentsSdkAgent.created["model"] == "gpt-test"
    assert FakeAgentsSdkAgent.created["instructions"] == "Be concise."
    assert FakeAgentsSdkRunner.last_input == "user: hello"
    assert FakeAgentsSdkRunner.last_max_turns == 3


def test_sdk_runner_flattens_structured_context_as_stable_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_agents = types.ModuleType("agents")
    fake_agents.__spec__ = ModuleSpec("agents", loader=None)
    fake_agents.Agent = FakeAgentsSdkAgent
    fake_agents.FunctionTool = FakeAgentsSdkFunctionTool
    fake_agents.Runner = FakeAgentsSdkRunner
    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[
            {"role": "developer", "content": {"state": {"z": 2, "a": 1}}},
            {"role": "user", "content": "hello"},
        ],
    )

    asyncio.run(OpenAIAgentsSdkRunner(model="gpt-test").run_reasoning(request))

    assert FakeAgentsSdkRunner.last_input == 'developer: {"state":{"a":1,"z":2}}\nuser: hello'


def test_sdk_runner_wraps_application_tool_executor_for_agents_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_agents = types.ModuleType("agents")
    fake_agents.__spec__ = ModuleSpec("agents", loader=None)
    fake_agents.Agent = FakeAgentsSdkAgent
    fake_agents.FunctionTool = FakeAgentsSdkFunctionTool
    fake_agents.Runner = ToolCallingAgentsSdkRunner
    monkeypatch.setitem(sys.modules, "agents", fake_agents)

    async def invoke_json(args_json: str) -> str:
        return f"tool-output:{args_json}"

    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Use tools.",
        model_input=[{"role": "user", "content": "read profile"}],
        tools=(
            SdkToolDefinition(
                contract_name="profile.read",
                sdk_name=sdk_tool_name("profile.read"),
                description="Read profile.",
                params_json_schema={"type": "object", "properties": {}},
                invoke_json=invoke_json,
            ),
        ),
    )

    result = asyncio.run(OpenAIAgentsSdkRunner(model="gpt-test").run_reasoning(request))

    assert result.final_text == 'tool-output:{"owner_user_id": "user_1"}'
    assert FakeAgentsSdkAgent.created["tools"][0].name == "profile_read"
    assert FakeAgentsSdkAgent.created["tools"][0].params_json_schema == {"type": "object", "properties": {}}


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


def test_sdk_runner_times_out_slow_backend() -> None:
    metrics = RequestMetrics()
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[{"role": "user", "content": "hello"}],
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(OpenAIAgentsSdkRunner(backend=SlowSdkBackend(), metrics=metrics, timeout_seconds=0.001).run_reasoning(request))

    assert exc_info.value.code == "sdk_run_timed_out"
    sdk_metrics = metrics.snapshot()["agent_sdk"][0]
    assert sdk_metrics["error_code_counts"]["sdk_run_timed_out"] == 1


class FakeSdkBackend:
    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        return SdkNodeResult(final_text="hello", tool_calls=[{"tool_name": request.tool_names[0]}])


class SlowSdkBackend:
    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        await asyncio.sleep(1)
        return SdkNodeResult(final_text="too late")


class FakeAgentsSdkAgent:
    created = {}

    def __init__(self, *, name: str, instructions: str, model: str, tools=()) -> None:
        self.name = name
        self.instructions = instructions
        self.model = model
        self.tools = tools
        FakeAgentsSdkAgent.created = {"name": name, "instructions": instructions, "model": model, "tools": tools}


class FakeAgentsSdkFunctionTool:
    def __init__(self, *, name: str, description: str, params_json_schema: dict, on_invoke_tool) -> None:
        self.name = name
        self.description = description
        self.params_json_schema = params_json_schema
        self.on_invoke_tool = on_invoke_tool


class FakeAgentsSdkRunner:
    last_input = ""
    last_max_turns = None

    @staticmethod
    async def run(agent: FakeAgentsSdkAgent, input: str, *, max_turns=None):
        FakeAgentsSdkRunner.last_input = input
        FakeAgentsSdkRunner.last_max_turns = max_turns
        return FakeAgentsSdkResult(final_output="sdk final")


class ToolCallingAgentsSdkRunner:
    @staticmethod
    async def run(agent: FakeAgentsSdkAgent, input: str, *, max_turns=None):
        output = await agent.tools[0].on_invoke_tool(None, '{"owner_user_id": "user_1"}')
        return FakeAgentsSdkResult(final_output=output)


class FakeAgentsSdkResult:
    def __init__(self, *, final_output: str) -> None:
        self.final_output = final_output
