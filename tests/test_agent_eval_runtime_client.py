import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from app.modules.agent_runtime.evals.service import (
    AgentEvalRuntimeClient,
    AgentEvalRuntimeTraceCollector,
    load_product_agent_eval_seed_cases,
)
from app.modules.agent_runtime.models import (
    AgentAction,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
)
from app.modules.agent_runtime.run_lifecycle.executor import AgentRuntimeExecutor
from app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, ScriptedSdkBackend, scripted_sdk_response


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_AGENT_EVAL_SEED = ROOT / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_agent_eval_runtime_client_executes_run_and_evaluates_seed_case() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(
        thread_id=thread_id,
        run_id=run.id,
        role="user",
        text="Submit this device issue to support.",
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
                tool_name="support.ticket.propose",
                call_id="call-support",
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
                        "action_type": "support.ticket.create",
                        "target_type": "support_ticket",
                        "side_effect_level": "medium",
                        "preview_payload": {"issue_summary": "Device will not start"},
                        "apply_payload": {"issue_summary": "Device will not start"},
                        "idempotency_key": "idem-support-ticket",
                    },
                )
            )
        ]
    )
    executor = AgentRuntimeExecutor(repository=repository, sdk_runner=OpenAIAgentsSdkRunner(backend=backend))
    case = {
        "suite": "runtime_trace_collection",
        "name": "supported action trace",
        "expected_tool_calls": [{"contract": "support.ticket.propose"}],
        "forbidden_tool_calls": [],
        "expected_behavior": {
            "service_skill_id": "cozymate_service_agent",
            "requires_confirmation_before_write": True,
            "must_not": [],
        },
    }

    result = asyncio.run(AgentEvalRuntimeClient(executor=executor, repository=repository).execute_case(run=run, case=case))

    assert result.execution_result.status == "waiting_for_confirmation"
    assert result.eval_result.passed is True
    assert result.trace.service_skill_id == "cozymate_service_agent"
    assert result.trace.tool_calls[0]["tool_name"] == "support.ticket.propose"
    assert any(event["type"] == "action.confirmation_required" for event in result.trace.events)
    assert result.trace.actions[0]["action_type"] == "support.ticket.create"


def test_agent_eval_trace_uses_latest_loaded_service_skill_event() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Continue setup.", sequence=1)
    repository = FakeEvalRuntimeRepository(run=run, messages=[current_user], current_message=current_user, tool_calls=[])
    repository.events.append(
        AgentEvent(
            event_id=uuid4(),
            thread_id=thread_id,
            run_id=run.id,
            sequence=1,
            event_type="skill.loaded",
            payload={"service_skill_id": "device-guidance"},
        )
    )

    trace = asyncio.run(AgentEvalRuntimeTraceCollector(repository=repository).collect(run_id=run.id))

    assert trace.service_skill_id == "device-guidance"


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

    async def get_latest_user_message_for_run(self, *, run_id: UUID):
        return self.current_message if self.current_message.run_id == run_id else None

    async def list_messages_for_thread(self, *, thread_id: UUID, limit: int = 40):
        return [message for message in self.messages if message.thread_id == thread_id][:limit]

    async def list_client_events_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        limit: int = 10,
    ):
        assert owner_user_id == self.run.actor_user_id
        return [
            event
            for event in self.events
            if event.thread_id == thread_id and event.event_type == "client.event"
        ][-limit:]

    async def list_active_workflow_states_for_thread(self, **_kwargs):
        return []

    async def get_run(self, *, run_id: UUID):
        return self.run if self.run.id == run_id else None

    async def record_routing_decision(self, **kwargs):
        if self.run.id == kwargs["run_id"]:
            self.run.service_skill_id = kwargs["selected_skill_id"]
            self.run.routing_source = kwargs["routing_source"]
            self.run.routing_confidence_score = int(float(kwargs["confidence"]) * 100)
            self.run.routing_summary = {
                "execution_mode": kwargs["execution_mode"],
                "intents": kwargs["intents"],
                "reason_codes": kwargs["reason_codes"],
                "safety_flags": kwargs["safety_flags"],
                "needs_clarification": kwargs["needs_clarification"],
                "tool_scope_version": kwargs["tool_scope_version"],
            }
        return kwargs

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

    async def list_recent_run_summaries(self, **kwargs):
        return []


def _case(suite: str) -> dict:
    return next(case for case in load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED) if case["suite"] == suite)


def _run(*, thread_id: UUID) -> AgentRun:
    return AgentRun(
        id=uuid4(),
        thread_id=thread_id,
        actor_user_id=uuid4(),
        status="running",
        runtime_pattern="sdk_only",
        runtime_version="momcozy-agent-v1",
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
