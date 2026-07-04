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
    milk_plan_proposal = registry.get("plans.milk_plan.propose")
    pregnancy_plan_proposal = registry.get("pregnancy.plan_create.propose")
    task_create_proposal = registry.get("plans.task_create.propose")
    task_complete_proposal = registry.get("plans.task_complete.propose")
    milk_reminder_proposal = registry.get("notifications.milk_reminder.propose")
    diary_entry_proposal = registry.get("diary.entry_upsert.propose")
    memory_create_proposal = registry.get("memory.create.propose")
    assert feeding_proposal.read_or_write == "write"
    assert feeding_proposal.requires_confirmation is True
    assert feeding_proposal.side_effect_level == "low"
    assert feeding_proposal.required_permission == "records:write:self"
    assert pumping_proposal.read_or_write == "write"
    assert pumping_proposal.requires_confirmation is True
    assert pumping_proposal.required_permission == "records:write:self"
    assert milk_plan_proposal.read_or_write == "write"
    assert milk_plan_proposal.requires_confirmation is True
    assert milk_plan_proposal.side_effect_level == "medium"
    assert milk_plan_proposal.required_permission == "plans:write:self"
    assert pregnancy_plan_proposal.read_or_write == "write"
    assert pregnancy_plan_proposal.requires_confirmation is True
    assert pregnancy_plan_proposal.required_permission == "plans:write:self"
    assert task_create_proposal.read_or_write == "write"
    assert task_create_proposal.requires_confirmation is True
    assert task_create_proposal.required_permission == "plans:write:self"
    assert task_complete_proposal.read_or_write == "write"
    assert task_complete_proposal.requires_confirmation is True
    assert task_complete_proposal.required_permission == "plans:write:self"
    assert milk_reminder_proposal.read_or_write == "write"
    assert milk_reminder_proposal.requires_confirmation is True
    assert milk_reminder_proposal.side_effect_level == "medium"
    assert milk_reminder_proposal.required_permission == "notifications:create:self"
    assert diary_entry_proposal.read_or_write == "write"
    assert diary_entry_proposal.requires_confirmation is True
    assert diary_entry_proposal.side_effect_level == "medium"
    assert diary_entry_proposal.required_permission == "diary:write:self"
    assert memory_create_proposal.read_or_write == "write"
    assert memory_create_proposal.owner_scope == "actor"
    assert memory_create_proposal.requires_confirmation is True
    assert memory_create_proposal.side_effect_level == "medium"
    assert memory_create_proposal.required_permission == "memory:write:self"
    assert "profile.read" in registry.names_for_sdk()
    assert "business.context.read" in registry.names_for_sdk()
    plans_current = registry.get("plans.current.read")
    diary_recent = registry.get("diary.recent.read")
    device_status = registry.get("devices.pump_status.read")
    device_guidance_assets = registry.get("devices.guidance_assets.read")
    file_vision = registry.get("files.vision_summary.read")
    assert plans_current.read_or_write == "read"
    assert plans_current.owner_scope == "actor"
    assert plans_current.requires_confirmation is False
    assert diary_recent.read_or_write == "read"
    assert diary_recent.owner_scope == "actor"
    assert diary_recent.requires_confirmation is False
    assert device_status.read_or_write == "read"
    assert device_status.owner_scope == "actor"
    assert device_status.requires_confirmation is False
    assert device_guidance_assets.read_or_write == "read"
    assert device_guidance_assets.owner_scope == "actor"
    assert device_guidance_assets.requires_confirmation is False
    assert file_vision.read_or_write == "read"
    assert file_vision.owner_scope == "actor"
    assert file_vision.required_permission == "files:read:self"
    assert file_vision.requires_confirmation is False
    assert "records.milk_summary.read" in registry.names_for_sdk()
    assert "plans.current.read" in registry.names_for_sdk()
    assert "diary.recent.read" in registry.names_for_sdk()
    assert "diary.entry_upsert.propose" in registry.names_for_sdk()
    assert "memory.create.propose" in registry.names_for_sdk()
    assert "devices.guidance_assets.read" in registry.names_for_sdk()
    assert "pregnancy.plan_create.propose" in registry.names_for_sdk()
    assert "plans.task_create.propose" in registry.names_for_sdk()
    assert "plans.task_complete.propose" in registry.names_for_sdk()
    assert "devices.pump_status.read" in registry.names_for_sdk()
    assert "files.vision_summary.read" in registry.names_for_sdk()


def test_tool_input_schemas_are_explicit_and_registered_by_contract_ref() -> None:
    registry = default_tool_registry()
    profile_schema = tool_input_schema(registry.get("profile.read").input_schema_ref)
    business_schema = tool_input_schema(registry.get("business.context.read").input_schema_ref)
    support_schema = tool_input_schema(registry.get("support.ticket.propose").input_schema_ref)
    milk_schema = tool_input_schema(registry.get("records.milk_summary.read").input_schema_ref)
    plans_schema = tool_input_schema(registry.get("plans.current.read").input_schema_ref)
    diary_schema = tool_input_schema(registry.get("diary.recent.read").input_schema_ref)
    diary_entry_schema = tool_input_schema(registry.get("diary.entry_upsert.propose").input_schema_ref)
    devices_schema = tool_input_schema(registry.get("devices.pump_status.read").input_schema_ref)
    device_guidance_schema = tool_input_schema(registry.get("devices.guidance_assets.read").input_schema_ref)
    file_vision_schema = tool_input_schema(registry.get("files.vision_summary.read").input_schema_ref)
    milk_plan_schema = tool_input_schema(registry.get("plans.milk_plan.propose").input_schema_ref)
    pregnancy_plan_schema = tool_input_schema(registry.get("pregnancy.plan_create.propose").input_schema_ref)
    task_create_schema = tool_input_schema(registry.get("plans.task_create.propose").input_schema_ref)
    task_complete_schema = tool_input_schema(registry.get("plans.task_complete.propose").input_schema_ref)
    milk_reminder_schema = tool_input_schema(registry.get("notifications.milk_reminder.propose").input_schema_ref)
    feeding_schema = tool_input_schema(registry.get("records.feeding_record.propose").input_schema_ref)
    pumping_schema = tool_input_schema(registry.get("records.pumping_record.propose").input_schema_ref)
    memory_create_schema = tool_input_schema(registry.get("memory.create.propose").input_schema_ref)

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
    assert diary_entry_schema["additionalProperties"] is False
    assert diary_entry_schema["required"] == ["entry_date"]
    assert diary_entry_schema["properties"]["content"]["maxLength"] == 5000
    assert diary_entry_schema["properties"]["symptom_tags"]["type"] == "array"
    assert memory_create_schema["additionalProperties"] is False
    assert memory_create_schema["required"] == ["memory_type", "content"]
    assert memory_create_schema["properties"]["memory_type"]["enum"] == [
        "user_preference",
        "stable_care_preference",
        "communication_preference",
        "recurring_constraint",
    ]
    assert memory_create_schema["properties"]["content"]["required"] == ["summary"]
    assert memory_create_schema["properties"]["confidence_score"]["maximum"] == 100
    assert devices_schema["additionalProperties"] is False
    assert devices_schema["properties"]["limit"]["maximum"] == 20
    assert device_guidance_schema["additionalProperties"] is False
    assert device_guidance_schema["properties"]["limit"]["maximum"] == 20
    assert device_guidance_schema["properties"]["content_type"]["type"] == "string"
    assert file_vision_schema["additionalProperties"] is False
    assert file_vision_schema["required"] == ["file_id"]
    assert file_vision_schema["properties"]["file_id"]["type"] == "string"
    assert milk_plan_schema["additionalProperties"] is False
    assert milk_plan_schema["required"] == ["title"]
    assert milk_plan_schema["properties"]["payload"]["type"] == "object"
    assert pregnancy_plan_schema["additionalProperties"] is False
    assert pregnancy_plan_schema["required"] == ["title"]
    assert task_create_schema["additionalProperties"] is False
    assert task_create_schema["required"] == ["title"]
    assert task_create_schema["properties"]["task_date"]["type"] == "string"
    assert task_complete_schema["additionalProperties"] is False
    assert task_complete_schema["required"] == ["task_id"]
    assert task_complete_schema["properties"]["completed"]["type"] == "boolean"
    assert milk_reminder_schema["additionalProperties"] is False
    assert milk_reminder_schema["required"] == ["title"]
    assert milk_reminder_schema["properties"]["remind_at"]["type"] == "string"
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

    assert [item["role"] for item in model_input] == ["system", "developer", "user", "developer", "developer", "developer", "user"]
    assert model_input[0]["content"] == "system-v1"
    assert model_input[-3]["content"] == {"memory": []}
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
    fake_agents.RunConfig = FakeAgentsSdkRunConfig
    fake_agents.Runner = FakeAgentsSdkRunner
    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[{"role": "user", "content": "hello"}],
        tool_names=("profile.read",),
        prompt_version="prompt-v2",
        trace_id="trace_1",
    )

    result = asyncio.run(OpenAIAgentsSdkRunner(model="gpt-test", max_turns=3, trace_enabled=True).run_reasoning(request))

    assert result.final_text == "sdk final"
    assert FakeAgentsSdkAgent.created["model"] == "gpt-test"
    assert FakeAgentsSdkAgent.created["instructions"] == "Be concise."
    assert FakeAgentsSdkRunner.last_input == "user: hello"
    assert FakeAgentsSdkRunner.last_max_turns == 3
    assert FakeAgentsSdkRunner.last_run_config.tracing_disabled is False
    assert FakeAgentsSdkRunner.last_run_config.trace_id == "trace_1"
    assert FakeAgentsSdkRunner.last_run_config.group_id == "thread_1"
    assert FakeAgentsSdkRunner.last_run_config.trace_metadata["run_id"] == "run_1"
    assert FakeAgentsSdkRunner.last_run_config.trace_metadata["prompt_version"] == "prompt-v2"
    assert FakeAgentsSdkRunner.last_run_config.trace_metadata["tool_names"] == ["profile.read"]
    assert FakeAgentsSdkRunner.last_previous_response_id is None
    assert FakeAgentsSdkRunner.last_auto_previous_response_id is False
    assert FakeAgentsSdkRunner.last_conversation_id is None
    assert FakeAgentsSdkRunner.last_session is None


def test_sdk_runner_disables_provider_tracing_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_agents = types.ModuleType("agents")
    fake_agents.__spec__ = ModuleSpec("agents", loader=None)
    fake_agents.Agent = FakeAgentsSdkAgent
    fake_agents.FunctionTool = FakeAgentsSdkFunctionTool
    fake_agents.RunConfig = FakeAgentsSdkRunConfig
    fake_agents.Runner = FakeAgentsSdkRunner
    monkeypatch.setitem(sys.modules, "agents", fake_agents)

    asyncio.run(
        OpenAIAgentsSdkRunner(model="gpt-test").run_reasoning(
            SdkNodeRequest(
                run_id="run_1",
                thread_id="thread_1",
                actor_user_id="user_1",
                instructions="Be concise.",
                model_input=[{"role": "user", "content": "hello"}],
            )
        )
    )

    assert FakeAgentsSdkRunner.last_run_config.tracing_disabled is True


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


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (429, "sdk_rate_limited"),
        (401, "sdk_auth_failed"),
        (503, "sdk_provider_unavailable"),
        (400, "sdk_bad_request"),
        (None, "sdk_run_failed"),
    ],
)
def test_sdk_runner_maps_provider_errors_to_stable_codes(status_code: int | None, expected_code: str) -> None:
    metrics = RequestMetrics()
    exc = FakeProviderError(status_code=status_code) if status_code is not None else RuntimeError("provider exploded")
    request = SdkNodeRequest(
        run_id="run_1",
        thread_id="thread_1",
        actor_user_id="user_1",
        instructions="Be concise.",
        model_input=[{"role": "user", "content": "hello"}],
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(OpenAIAgentsSdkRunner(backend=FailingSdkBackend(exc), metrics=metrics).run_reasoning(request))

    assert exc_info.value.code == expected_code
    sdk_metrics = metrics.snapshot()["agent_sdk"][0]
    assert sdk_metrics["error_code_counts"][expected_code] == 1


class FakeSdkBackend:
    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        return SdkNodeResult(final_text="hello", tool_calls=[{"tool_name": request.tool_names[0]}])


class FailingSdkBackend:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        raise self.exc


class FakeProviderError(Exception):
    def __init__(self, *, status_code: int) -> None:
        super().__init__(f"provider status {status_code}")
        self.status_code = status_code


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


class FakeAgentsSdkRunConfig:
    def __init__(
        self,
        *,
        tracing_disabled: bool,
        trace_id: str | None,
        group_id: str | None,
        workflow_name: str,
        trace_metadata: dict,
    ) -> None:
        self.tracing_disabled = tracing_disabled
        self.trace_id = trace_id
        self.group_id = group_id
        self.workflow_name = workflow_name
        self.trace_metadata = trace_metadata


class FakeAgentsSdkRunner:
    last_input = ""
    last_max_turns = None
    last_run_config = None
    last_previous_response_id = None
    last_auto_previous_response_id = None
    last_conversation_id = None
    last_session = None

    @staticmethod
    async def run(
        agent: FakeAgentsSdkAgent,
        input: str,
        *,
        max_turns=None,
        run_config=None,
        previous_response_id=None,
        auto_previous_response_id=False,
        conversation_id=None,
        session=None,
    ):
        FakeAgentsSdkRunner.last_input = input
        FakeAgentsSdkRunner.last_max_turns = max_turns
        FakeAgentsSdkRunner.last_run_config = run_config
        FakeAgentsSdkRunner.last_previous_response_id = previous_response_id
        FakeAgentsSdkRunner.last_auto_previous_response_id = auto_previous_response_id
        FakeAgentsSdkRunner.last_conversation_id = conversation_id
        FakeAgentsSdkRunner.last_session = session
        return FakeAgentsSdkResult(final_output="sdk final")


class ToolCallingAgentsSdkRunner:
    @staticmethod
    async def run(agent: FakeAgentsSdkAgent, input: str, *, max_turns=None, run_config=None):
        output = await agent.tools[0].on_invoke_tool(None, '{"owner_user_id": "user_1"}')
        return FakeAgentsSdkResult(final_output=output)


class FakeAgentsSdkResult:
    def __init__(self, *, final_output: str) -> None:
        self.final_output = final_output
