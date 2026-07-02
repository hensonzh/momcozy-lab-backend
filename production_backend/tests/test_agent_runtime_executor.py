import asyncio
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import AgentMessage, AgentRun
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

    async def get_latest_user_message_for_run(self, *, run_id):
        if self.current_message is not None and self.current_message.run_id == run_id:
            return self.current_message
        return None

    async def list_messages_for_thread(self, *, thread_id, limit=40):
        return [message for message in self.messages if message.thread_id == thread_id][:limit]


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
