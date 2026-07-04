import asyncio
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentArtifact, AgentEvent, AgentMessage, AgentRun, AgentToolCall
from production_backend.app.modules.agent_runtime.runtime import AgentRuntimeExecutor
from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, SdkNodeRequest, SdkNodeResult
from production_backend.app.modules.agent_runtime.tools import ToolExecutor, ToolHandlerContext, default_tool_registry


def test_agent_runtime_executor_uses_internal_ledger_context_and_sdk_result() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    prior_user = _message(thread_id=thread_id, run_id=uuid4(), role="user", text="What did we discuss?", sequence=1)
    prior_assistant = _message(thread_id=thread_id, run_id=uuid4(), role="assistant", text="Your care plan.", sequence=2)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Summarize it.", sequence=3)
    repository = FakeRuntimeRepository(messages=[prior_user, prior_assistant, current_user], current_message=current_user)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="Here is the summary."))
    checkpoint_store = FakeCheckpointStore()
    state_store = FakeStateStore()

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            checkpoint_store=checkpoint_store,
            state_store=state_store,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == "Here is the summary."
    request = backend.requests[0]
    assert request.run_id == str(run.id)
    assert request.thread_id == str(thread_id)
    assert request.tool_names == (
        "business.context.read",
        "devices.pump_status.read",
        "diary.entry_upsert.propose",
        "diary.recent.read",
        "files.vision_summary.read",
        "hospital_bag.cart_update.propose",
        "notifications.milk_reminder.propose",
        "plans.current.read",
        "plans.milk_plan.propose",
        "plans.task_complete.propose",
        "plans.task_create.propose",
        "pregnancy.plan_create.propose",
        "profile.read",
        "records.feeding_record.propose",
        "records.milk_summary.read",
        "records.pumping_record.propose",
        "support.ticket.propose",
    )
    assert [item["role"] for item in request.model_input] == ["system", "developer", "user", "assistant", "developer", "developer", "user"]
    assert request.model_input[0]["content"].startswith("You are the MomCozy product assistant.")
    assert request.model_input[4]["content"]["state"]["run_id"] == str(run.id)
    assert request.model_input[-1] == {"role": "user", "content": "Summarize it."}
    assert [checkpoint["state_summary"]["node_name"] for checkpoint in checkpoint_store.checkpoints] == ["sdk_reasoning", "finish"]
    assert checkpoint_store.checkpoints[0]["state_summary"]["current_user_message_id"] == str(current_user.id)
    assert state_store.projections[0]["selected_message_ids"] == [prior_user.id, prior_assistant.id, current_user.id]
    assert state_store.projections[0]["projection_summary"]["history_message_count"] == 2
    assert state_store.projections[0]["projection_summary"]["state_keys"] == [
        "actor_user_id",
        "graph_version",
        "run_id",
        "runtime_pattern",
        "thread_id",
    ]


def test_agent_runtime_executor_requires_current_user_message() -> None:
    run = _run(thread_id=uuid4())
    repository = FakeRuntimeRepository(messages=[], current_message=None)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=CapturingSdkBackend(result=SdkNodeResult(final_text="hello"))),
            ).execute(run=run)
        )

    assert exc_info.value.code == "missing_user_message"


def test_agent_runtime_executor_rejects_empty_sdk_response() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Hello", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=CapturingSdkBackend(result=SdkNodeResult(final_text="  "))),
            ).execute(run=run)
        )

    assert exc_info.value.code == "empty_agent_response"


def test_agent_runtime_executor_routes_sdk_tool_calls_through_tool_executor() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Read my profile", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    tool_executor = FakeToolExecutor(safe_output={"profile": {"display_name": "Mai"}})
    backend = InvokingSdkBackend()

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            tool_executor=tool_executor,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == '{"profile": {"display_name": "Mai"}}'
    assert backend.tool_names == (
        "business_context_read",
        "devices_pump_status_read",
        "diary_entry_upsert_propose",
        "diary_recent_read",
        "files_vision_summary_read",
        "hospital_bag_cart_update_propose",
        "notifications_milk_reminder_propose",
        "plans_current_read",
        "plans_milk_plan_propose",
        "plans_task_complete_propose",
        "plans_task_create_propose",
        "pregnancy_plan_create_propose",
        "profile_read",
        "records_feeding_record_propose",
        "records_milk_summary_read",
        "records_pumping_record_propose",
        "support_ticket_propose",
    )
    assert backend.tool_schemas["business_context_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["devices_pump_status_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["diary_entry_upsert_propose"]["required"] == ["entry_date"]
    assert backend.tool_schemas["diary_entry_upsert_propose"]["properties"]["content"]["maxLength"] == 5000
    assert backend.tool_schemas["diary_recent_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["files_vision_summary_read"]["required"] == ["file_id"]
    assert backend.tool_schemas["hospital_bag_cart_update_propose"]["required"] == ["cart_update"]
    assert backend.tool_schemas["hospital_bag_cart_update_propose"]["additionalProperties"] is False
    assert backend.tool_schemas["notifications_milk_reminder_propose"]["required"] == ["title"]
    assert backend.tool_schemas["plans_current_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["plans_milk_plan_propose"]["required"] == ["title"]
    assert backend.tool_schemas["plans_task_complete_propose"]["required"] == ["task_id"]
    assert backend.tool_schemas["plans_task_complete_propose"]["properties"]["completed"]["type"] == "boolean"
    assert backend.tool_schemas["plans_task_create_propose"]["required"] == ["title"]
    assert backend.tool_schemas["pregnancy_plan_create_propose"]["required"] == ["title"]
    assert backend.tool_schemas["profile_read"]["additionalProperties"] is False
    assert backend.tool_schemas["profile_read"]["properties"] == {}
    assert backend.tool_schemas["records_feeding_record_propose"]["required"] == ["feed_time", "feed_type"]
    assert backend.tool_schemas["records_milk_summary_read"]["properties"]["days"]["maximum"] == 30
    assert backend.tool_schemas["records_pumping_record_propose"]["required"] == ["pump_start_time"]
    assert backend.tool_schemas["support_ticket_propose"]["required"] == ["issue_summary"]
    assert backend.tool_schemas["support_ticket_propose"]["additionalProperties"] is False
    assert tool_executor.calls[0]["actor"].user_id == run.actor_user_id
    assert tool_executor.calls[0]["run_id"] == run.id
    assert tool_executor.calls[0]["tool_name"] == "profile.read"
    assert tool_executor.calls[0]["args"] == {}


def test_agent_runtime_executor_real_tool_executor_uses_run_actor_role_permissions() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Read my profile", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user, run=run)
    tool_executor = ToolExecutor(
        registry=default_tool_registry(),
        repository=repository,
        handlers={"profile.read": profile_read_handler},
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=InvokingSdkBackend()),
            tool_executor=tool_executor,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert repository.tool_call is not None
    assert repository.tool_call.status == "completed"
    assert repository.tool_call.safe_args == {}
    assert repository.events[0].event_type == "tool.started"
    assert repository.events[1].event_type == "tool.completed"
    assert result.final_text == '{"profile": {"actor_user_id": "' + str(run.actor_user_id) + '"}}'


def test_agent_runtime_executor_persists_sdk_action_proposal_and_waits_for_confirmation() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Create a support ticket", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    checkpoint_store = FakeCheckpointStore()
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            action_proposals=[
                {
                    "action_type": "support.ticket.create",
                    "target_type": "support_ticket",
                    "side_effect_level": "medium",
                    "preview_payload": {"summary": "Pump does not turn on"},
                    "apply_payload": {"issue_summary": "Pump does not turn on"},
                    "idempotency_key": "idem-action",
                }
            ]
        )
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            checkpoint_store=checkpoint_store,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "waiting_for_confirmation"
    assert result.pending_action_id == repository.actions[0].id
    assert repository.actions[0].status == "confirmation_required"
    assert repository.actions[0].apply_payload == {"issue_summary": "Pump does not turn on"}
    assert repository.events[-1].event_type == "action.confirmation_required"
    assert repository.events[-1].payload["action_id"] == str(repository.actions[0].id)
    assert repository.events[-1].payload["action_status"] == "confirmation_required"
    assert repository.events[-1].payload["action_type"] == "support.ticket.create"
    assert repository.events[-1].payload["target_type"] == "support_ticket"
    assert repository.events[-1].payload["side_effect_level"] == "medium"
    assert repository.events[-1].payload["preview_payload"] == {"summary": "Pump does not turn on"}
    assert "apply_payload" not in repository.events[-1].payload
    assert [checkpoint["state_summary"]["node_name"] for checkpoint in checkpoint_store.checkpoints] == [
        "sdk_reasoning",
        "confirmation_interrupt",
    ]
    assert checkpoint_store.checkpoints[-1]["state_summary"]["pending_action_id"] == str(repository.actions[0].id)


def test_agent_runtime_executor_rejects_unsupported_sdk_action_proposal_before_persisting() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Delete my device", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            action_proposals=[
                {
                    "action_type": "device.delete",
                    "target_type": "device",
                    "side_effect_level": "high",
                    "preview_payload": {"summary": "Delete device"},
                    "apply_payload": {"device_id": "device_1"},
                }
            ]
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            ).execute(run=run)
        )

    assert exc_info.value.code == "unsupported_agent_action"
    assert repository.actions == []
    assert repository.events == []


def test_agent_runtime_executor_rejects_multiple_sdk_action_proposals_before_side_effects() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Create two tickets", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            final_text="I drafted a plan.",
            artifacts=[{"artifact_type": "care_plan", "payload": {"title": "Plan"}}],
            action_proposals=[
                {"action_type": "support.ticket.create", "apply_payload": {"issue_summary": "First"}},
                {"action_type": "support.ticket.create", "apply_payload": {"issue_summary": "Second"}},
            ],
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            ).execute(run=run)
        )

    assert exc_info.value.code == "too_many_agent_action_proposals"
    assert repository.actions == []
    assert repository.artifacts == []
    assert repository.events == []


def test_agent_runtime_executor_persists_sdk_artifacts_and_emits_events() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Create a plan", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            final_text="I drafted a plan.",
            artifacts=[
                {
                    "artifact_type": "care_plan",
                    "schema_version": "v1",
                    "payload": {"title": "Birth plan"},
                }
            ],
        )
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert repository.artifacts[0].artifact_type == "care_plan"
    assert repository.artifacts[0].payload == {"title": "Birth plan"}
    assert repository.events[0].event_type == "artifact.created"
    assert repository.events[0].payload["artifact_id"] == str(repository.artifacts[0].id)


class CapturingSdkBackend:
    def __init__(self, *, result: SdkNodeResult) -> None:
        self.result = result
        self.requests = []

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.requests.append(request)
        return self.result


class FakeRuntimeRepository:
    def __init__(self, *, messages: list[AgentMessage], current_message: AgentMessage | None, run: AgentRun | None = None) -> None:
        self.messages = messages
        self.current_message = current_message
        self.run = run
        self.actions = []
        self.artifacts = []
        self.events = []
        self.tool_call = None
        self.tool_output = None

    async def get_latest_user_message_for_run(self, *, run_id):
        if self.current_message is not None and self.current_message.run_id == run_id:
            return self.current_message
        return None

    async def list_messages_for_thread(self, *, thread_id, limit=40):
        return [message for message in self.messages if message.thread_id == thread_id][:limit]

    async def get_run(self, *, run_id):
        if self.run is not None and self.run.id == run_id:
            return self.run
        if self.current_message is not None and self.current_message.run_id == run_id:
            return _run(thread_id=self.current_message.thread_id, run_id=run_id)
        return None

    async def start_tool_call(self, **kwargs):
        self.tool_call = AgentToolCall(
            id=uuid4(),
            run_id=kwargs["run_id"],
            tool_name=kwargs["tool_name"],
            call_id=kwargs["call_id"],
            status="started",
            safe_args=kwargs["safe_args"],
            started_at=kwargs["started_at"],
            error_code="",
        )
        return self.tool_call

    async def complete_tool_call(self, **kwargs):
        self.tool_call.status = "completed"
        self.tool_call.completed_at = kwargs["completed_at"]
        return self.tool_call

    async def fail_tool_call(self, **kwargs):
        self.tool_call.status = "failed"
        self.tool_call.completed_at = kwargs["completed_at"]
        self.tool_call.error_code = kwargs["error_code"]
        return self.tool_call

    async def create_tool_output(self, **kwargs):
        self.tool_output = FakeToolOutput(tool_call_id=kwargs["tool_call_id"], safe_output=kwargs["safe_output"])
        return self.tool_output

    async def create_action(self, **kwargs):
        action = AgentAction(
            id=uuid4(),
            run_id=kwargs["run_id"],
            actor_user_id=kwargs["actor_user_id"],
            action_type=kwargs["action_type"],
            target_type=kwargs["target_type"],
            target_id=kwargs["target_id"],
            status=kwargs["status"],
            side_effect_level=kwargs["side_effect_level"],
            preview_payload=kwargs["preview_payload"],
            apply_payload=kwargs["apply_payload"],
            idempotency_key=kwargs["idempotency_key"],
            expires_at=kwargs["expires_at"],
            error_code="",
        )
        self.actions.append(action)
        return action

    async def create_artifact(self, **kwargs):
        artifact = AgentArtifact(
            id=uuid4(),
            run_id=kwargs["run_id"],
            owner_user_id=kwargs["owner_user_id"],
            artifact_type=kwargs["artifact_type"],
            schema_version=kwargs["schema_version"],
            status=kwargs["status"],
            payload=kwargs["payload"],
            raw_payload_ref=kwargs["raw_payload_ref"],
        )
        self.artifacts.append(artifact)
        return artifact

    async def append_event(self, **kwargs):
        event = AgentEvent(
            event_id=uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            sequence=len(self.events) + 1,
            event_type=kwargs["event_type"],
            payload=kwargs["payload"],
        )
        self.events.append(event)
        return event

    async def list_actions_for_run(self, *, run_id):
        return [action for action in self.actions if action.run_id == run_id]


class FakeCheckpointStore:
    def __init__(self) -> None:
        self.checkpoints = []

    async def save_run_checkpoint(self, **kwargs):
        self.checkpoints.append(kwargs)


class FakeStateStore:
    def __init__(self) -> None:
        self.projections = []

    async def record_context_projection(self, **kwargs):
        self.projections.append(kwargs)


class FakeToolExecutor:
    def __init__(self, *, safe_output):
        self.safe_output = safe_output
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return FakeToolExecutionResult(safe_output=self.safe_output)


class FakeToolExecutionResult:
    def __init__(self, *, safe_output):
        self.safe_output = safe_output


class FakeToolOutput:
    def __init__(self, *, tool_call_id, safe_output):
        self.id = uuid4()
        self.tool_call_id = tool_call_id
        self.safe_output = safe_output


class InvokingSdkBackend:
    def __init__(self) -> None:
        self.tool_names = ()
        self.tool_schemas = {}

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.tool_names = tuple(tool.sdk_name for tool in request.tools)
        self.tool_schemas = {tool.sdk_name: tool.params_json_schema for tool in request.tools}
        profile_tool = next(tool for tool in request.tools if tool.contract_name == "profile.read")
        output = await profile_tool.invoke_json("{}")
        return SdkNodeResult(final_text=output)


async def profile_read_handler(context: ToolHandlerContext):
    return {"profile": {"actor_user_id": str(context.actor.user_id)}}


def _run(*, thread_id, run_id=None) -> AgentRun:
    return AgentRun(
        id=run_id or uuid4(),
        thread_id=thread_id,
        actor_user_id=uuid4(),
        status="running",
        runtime_pattern="langgraph_sdk",
        graph_version="momcozy-agent-v1",
        prompt_version="",
        request_id="req",
        trace_id="trace",
        error_code="",
        error_details={},
    )


def _message(*, thread_id, run_id, role: str, text: str, sequence: int) -> AgentMessage:
    return AgentMessage(
        id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        role=role,
        message_type="text",
        content={"text": text},
        status="completed",
        sequence=sequence,
    )
