import asyncio
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentAction, AgentEvent, AgentMessage, AgentRun
from production_backend.app.modules.agent_runtime.runtime import AgentRuntimeExecutor
from production_backend.app.modules.agent_runtime.sdk import OpenAIAgentsSdkRunner, SdkNodeRequest, SdkNodeResult


def test_agent_runtime_executor_uses_internal_ledger_context_and_sdk_result() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    prior_user = _message(thread_id=thread_id, run_id=uuid4(), role="user", text="What did we discuss?", sequence=1)
    prior_assistant = _message(thread_id=thread_id, run_id=uuid4(), role="assistant", text="Your care plan.", sequence=2)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Summarize it.", sequence=3)
    repository = FakeRuntimeRepository(messages=[prior_user, prior_assistant, current_user], current_message=current_user)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="Here is the summary."))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == "Here is the summary."
    request = backend.requests[0]
    assert request.run_id == str(run.id)
    assert request.thread_id == str(thread_id)
    assert request.tool_names == ("profile.read", "support.ticket.propose")
    assert [item["role"] for item in request.model_input] == ["system", "developer", "user", "assistant", "developer", "developer", "user"]
    assert request.model_input[0]["content"].startswith("You are the MomCozy product assistant.")
    assert request.model_input[4]["content"]["state"]["run_id"] == str(run.id)
    assert request.model_input[-1] == {"role": "user", "content": "Summarize it."}


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


def test_agent_runtime_executor_persists_sdk_action_proposal_and_waits_for_confirmation() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Create a support ticket", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
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
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "waiting_for_confirmation"
    assert result.pending_action_id == repository.actions[0].id
    assert repository.actions[0].status == "confirmation_required"
    assert repository.actions[0].apply_payload == {"issue_summary": "Pump does not turn on"}
    assert repository.events[-1].event_type == "action.confirmation_required"
    assert repository.events[-1].payload["action_id"] == str(repository.actions[0].id)


class CapturingSdkBackend:
    def __init__(self, *, result: SdkNodeResult) -> None:
        self.result = result
        self.requests = []

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.requests.append(request)
        return self.result


class FakeRuntimeRepository:
    def __init__(self, *, messages: list[AgentMessage], current_message: AgentMessage | None) -> None:
        self.messages = messages
        self.current_message = current_message
        self.actions = []
        self.events = []

    async def get_latest_user_message_for_run(self, *, run_id):
        if self.current_message is not None and self.current_message.run_id == run_id:
            return self.current_message
        return None

    async def list_messages_for_thread(self, *, thread_id, limit=40):
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


def _run(*, thread_id) -> AgentRun:
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
