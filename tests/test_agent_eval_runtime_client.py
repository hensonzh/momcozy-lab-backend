import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from app.agent_runtime.evals.service import (
    AgentEvalRuntimeClient,
    AgentEvalRuntimeTraceCollector,
)
from app.agents.cozymate.evals import (
    COZYMATE_AGENT_ID,
    create_cozymate_eval_assertion_engine,
    load_product_agent_eval_seed_cases,
)
from app.agent_runtime.context.items import ContextItemAppend, message_context_item
from app.agent_runtime.runs.models import (
    AgentAction,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentToolCall,
)
from app.agents.cozymate.executor import CozymateAgentExecutor
from app.agent_runtime.providers import OpenAIResponsesRunner, ScriptedSdkBackend, scripted_sdk_response


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_AGENT_EVAL_SEED = ROOT / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_agent_eval_runtime_client_executes_run_and_evaluates_seed_case() -> None:
    thread_id = uuid4()
    run = _run(
        thread_id=thread_id,
        service_skill_id=COZYMATE_AGENT_ID,
    )
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
                tool_name="support_ticket_create",
                call_id="call-support",
                status="completed",
                safe_args={},
                error_code="",
            )
        ],
    )
    backend = ScriptedSdkBackend([scripted_sdk_response(final_text="Please confirm the editable support form.")])
    executor = CozymateAgentExecutor(repository=repository, sdk_runner=OpenAIResponsesRunner(backend=backend))
    case = {
        "suite": "runtime_trace_collection",
        "name": "support draft tool trace",
        "expected_tool_calls": [{"contract": "support_ticket_create"}],
        "forbidden_tool_calls": [],
        "expected_behavior": {
            "service_skill_id": "cozymate_service_agent",
            "requires_confirmation_before_write": False,
            "must_not": [],
        },
    }

    result = asyncio.run(
        AgentEvalRuntimeClient(
            executor=executor,
            repository=repository,
            assertion_engine=create_cozymate_eval_assertion_engine(),
        ).execute_case(run=run, case=case)
    )

    assert result.execution_result.status == "completed"
    assert result.eval_result.passed is True
    assert result.trace.service_skill_id == COZYMATE_AGENT_ID
    assert result.trace.tool_calls[0]["tool_name"] == "support_ticket_create"
    assert result.trace.actions == []


def test_agent_eval_trace_uses_run_service_skill_id() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id, service_skill_id="device-guidance")
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Continue setup.", sequence=1)
    repository = FakeEvalRuntimeRepository(run=run, messages=[current_user], current_message=current_user, tool_calls=[])

    trace = asyncio.run(AgentEvalRuntimeTraceCollector(repository=repository).collect(run_id=run.id))

    assert trace.service_skill_id == "device-guidance"


def test_agent_eval_trace_does_not_invent_a_missing_route() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(
        thread_id=thread_id,
        run_id=run.id,
        role="user",
        text="Continue.",
        sequence=1,
    )
    repository = FakeEvalRuntimeRepository(
        run=run,
        messages=[current_user],
        current_message=current_user,
        tool_calls=[],
    )

    trace = asyncio.run(
        AgentEvalRuntimeTraceCollector(repository=repository).collect(
            run_id=run.id
        )
    )

    assert trace.service_skill_id == ""


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
        self.context_items = [
            ContextItemAppend(
                item_key=f"message:{message.id}",
                item=message_context_item(role=message.role, content=message.content),
            )
            for message in messages
            if message.role in {"user", "assistant"}
        ]

    async def get_latest_user_message_for_run(self, *, run_id: UUID):
        return self.current_message if self.current_message.run_id == run_id else None

    async def list_messages_for_thread(self, *, thread_id: UUID, limit: int = 40):
        return [message for message in self.messages if message.thread_id == thread_id][:limit]

    async def list_context_items_for_thread(
        self,
        *,
        thread_id: UUID,
        limit: int | None = None,
    ):
        items = list(self.context_items) if thread_id == self.run.thread_id else []
        return items[-limit:] if isinstance(limit, int) else items

    async def append_context_items(self, *, thread_id: UUID, run_id: UUID, items):
        assert thread_id == self.run.thread_id
        assert run_id == self.run.id
        self.context_items.extend(items)
        return list(items)

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


def _run(*, thread_id: UUID, service_skill_id: str = "") -> AgentRun:
    return AgentRun(
        id=uuid4(),
        thread_id=thread_id,
        actor_user_id=uuid4(),
        status="running",
        runtime_pattern="sdk_only",
        runtime_version="momcozy-agent-v2",
        prompt_version="",
        request_id="req",
        trace_id="trace",
        service_skill_id=service_skill_id,
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
