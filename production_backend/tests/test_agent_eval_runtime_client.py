import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from production_backend.app.modules.agent_runtime.evals import AgentEvalRuntimeClient, load_product_agent_eval_seed_cases
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentEvent, AgentMessage, AgentRun, AgentSafetyEvent, AgentToolCall
from production_backend.app.modules.agent_runtime.runtime import AgentRuntimeExecutor
from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, ScriptedSdkBackend, scripted_sdk_response


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_AGENT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_agent_eval_runtime_client_executes_run_and_evaluates_seed_case() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(
        thread_id=thread_id,
        run_id=run.id,
        role="user",
        text="Please remember that I prefer concise evening reminders.",
        sequence=1,
    )
    repository = FakeEvalRuntimeRepository(
        run=run,
        messages=[current_user],
        current_message=current_user,
        tool_calls=[
            AgentToolCall(
                id=uuid4(),
                run_id=run.id,
                tool_name="memory.create.propose",
                call_id="call-memory",
                status="completed",
                safe_args={},
                error_code="",
            )
        ],
    )
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                action_proposals=(
                    {
                        "action_type": "agent.memory.create",
                        "target_type": "agent_memory",
                        "side_effect_level": "medium",
                        "preview_payload": {"summary": "Prefers concise evening reminders"},
                        "apply_payload": {
                            "memory_type": "communication_preference",
                            "content": {"summary": "Prefers concise evening reminders"},
                        },
                        "idempotency_key": "idem-memory",
                    },
                )
            )
        ]
    )
    executor = AgentRuntimeExecutor(repository=repository, sdk_runner=OpenAIAgentsSdkRunner(backend=backend))
    case = _case("memory_preference_capture")

    result = asyncio.run(AgentEvalRuntimeClient(executor=executor, repository=repository).execute_case(run=run, case=case))

    assert result.execution_result.status == "waiting_for_confirmation"
    assert result.eval_result.passed is True
    assert result.trace.tool_calls[0]["tool_name"] == "memory.create.propose"
    assert result.trace.events[0]["type"] == "action.confirmation_required"
    assert result.trace.actions[0]["action_type"] == "agent.memory.create"


class FakeEvalRuntimeRepository:
    def __init__(
        self,
        *,
        run: AgentRun,
        messages: list[AgentMessage],
        current_message: AgentMessage,
        tool_calls: list[AgentToolCall],
    ) -> None:
        self.run = run
        self.messages = messages
        self.current_message = current_message
        self.tool_calls = tool_calls
        self.actions: list[AgentAction] = []
        self.events: list[AgentEvent] = []
        self.safety_events: list[AgentSafetyEvent] = []

    async def get_latest_user_message_for_run(self, *, run_id: UUID):
        return self.current_message if self.current_message.run_id == run_id else None

    async def list_messages_for_thread(self, *, thread_id: UUID, limit: int = 40):
        return [message for message in self.messages if message.thread_id == thread_id][:limit]

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

    async def list_actions_for_run(self, *, run_id: UUID):
        return [action for action in self.actions if action.run_id == run_id]

    async def list_events_for_run(self, *, run_id: UUID):
        return [event for event in self.events if event.run_id == run_id]

    async def list_tool_calls_for_run(self, *, run_id: UUID):
        return [tool_call for tool_call in self.tool_calls if tool_call.run_id == run_id]

    async def list_safety_events_for_run(self, *, run_id: UUID):
        return [event for event in self.safety_events if event.run_id == run_id]


def _case(suite: str) -> dict:
    return next(case for case in load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED) if case["suite"] == suite)


def _run(*, thread_id: UUID) -> AgentRun:
    return AgentRun(
        id=uuid4(),
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


def _message(*, thread_id: UUID, run_id: UUID, role: str, text: str, sequence: int) -> AgentMessage:
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
