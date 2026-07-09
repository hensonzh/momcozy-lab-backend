import asyncio
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.run_lifecycle.execution import AgentRunExecutionResult
from production_backend.app.modules.agent_runtime.graphs import AgentRuntimeGraphRunner
from production_backend.app.modules.agent_runtime.models import AgentContextCheckpoint, AgentMessage, AgentRun


def test_agent_runtime_graph_runner_executes_completed_path_with_checkpoints() -> None:
    run = _run()
    current_message = _message(run=run)
    repository = FakeGraphRepository(current_message=current_message)
    checkpoint_store = FakeCheckpointStore()
    handler = FakeNodeHandler(result=AgentRunExecutionResult(status="completed", final_text="Done."))

    result = asyncio.run(
        AgentRuntimeGraphRunner(
            repository=repository,
            checkpoint_store=checkpoint_store,
            node_handler=handler,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == "Done."
    assert handler.runs == [run.id]
    assert _checkpoint_nodes(checkpoint_store) == [
        "load_context",
        "safety_gate",
        "sdk_reasoning",
        "finish",
    ]
    assert checkpoint_store.checkpoints[-1].state_summary["visited_nodes"] == [
        "load_context",
        "safety_gate",
        "sdk_reasoning",
        "finish",
    ]
    assert checkpoint_store.checkpoints[-1].state_summary["outcome_status"] == "completed"


def test_agent_runtime_graph_runner_executes_confirmation_interrupt_path() -> None:
    run = _run()
    action_id = uuid4()
    checkpoint_store = FakeCheckpointStore()
    handler = FakeNodeHandler(result=AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=action_id))

    result = asyncio.run(
        AgentRuntimeGraphRunner(
            repository=FakeGraphRepository(current_message=_message(run=run)),
            checkpoint_store=checkpoint_store,
            node_handler=handler,
        ).execute(run=run)
    )

    assert result.status == "waiting_for_confirmation"
    assert result.pending_action_id == action_id
    assert _checkpoint_nodes(checkpoint_store) == [
        "load_context",
        "safety_gate",
        "sdk_reasoning",
        "finish",
    ]
    assert checkpoint_store.checkpoints[-1].state_summary["pending_action_id"] == str(action_id)
    assert checkpoint_store.checkpoints[-1].state_summary["outcome_status"] == "waiting_for_confirmation"


def test_agent_runtime_graph_runner_fails_before_handler_without_user_message() -> None:
    run = _run()
    handler = FakeNodeHandler(result=AgentRunExecutionResult(status="completed", final_text="Done."))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeGraphRunner(
                repository=FakeGraphRepository(current_message=None),
                checkpoint_store=FakeCheckpointStore(),
                node_handler=handler,
            ).execute(run=run)
        )

    assert exc_info.value.code == "missing_user_message"
    assert handler.runs == []


def test_agent_runtime_graph_runner_resumes_waiting_checkpoint_without_reinvoking_handler() -> None:
    run = _run()
    action_id = uuid4()
    checkpoint_store = FakeCheckpointStore()
    checkpoint_store.checkpoints.append(
        AgentContextCheckpoint(
            id=uuid4(),
            thread_id=run.thread_id,
            run_id=run.id,
            checkpoint_namespace="test",
            checkpoint_id="checkpoint-existing",
            graph_version=run.graph_version,
            state_ref="",
            state_summary={
                "node_name": "sdk_reasoning",
                "outcome_status": "waiting_for_confirmation",
                "pending_action_id": str(action_id),
            },
        )
    )
    handler = FakeNodeHandler(result=AgentRunExecutionResult(status="completed", final_text="Should not run"))

    result = asyncio.run(
        AgentRuntimeGraphRunner(
            repository=FakeGraphRepository(current_message=_message(run=run)),
            checkpoint_store=checkpoint_store,
            node_handler=handler,
        ).execute(run=run)
    )

    assert result.status == "waiting_for_confirmation"
    assert result.pending_action_id == action_id
    assert handler.runs == []
    assert _checkpoint_nodes(checkpoint_store) == ["sdk_reasoning"]


def _checkpoint_nodes(store: "FakeCheckpointStore") -> list[str]:
    return [checkpoint.state_summary["node_name"] for checkpoint in store.checkpoints]


def _run() -> AgentRun:
    return AgentRun(
        id=uuid4(),
        thread_id=uuid4(),
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


def _message(*, run: AgentRun) -> AgentMessage:
    return AgentMessage(
        id=uuid4(),
        thread_id=run.thread_id,
        run_id=run.id,
        role="user",
        message_type="text",
        content={"text": "Hello"},
        status="completed",
        sequence=1,
    )


class FakeNodeHandler:
    def __init__(self, *, result: AgentRunExecutionResult) -> None:
        self.result = result
        self.runs: list = []

    async def __call__(self, run: AgentRun) -> AgentRunExecutionResult:
        self.runs.append(run.id)
        return self.result


class FakeGraphRepository:
    def __init__(self, *, current_message: AgentMessage | None) -> None:
        self.current_message = current_message

    async def get_latest_user_message_for_run(self, *, run_id):
        if self.current_message is not None and self.current_message.run_id == run_id:
            return self.current_message
        return None


class FakeCheckpointStore:
    def __init__(self) -> None:
        self.checkpoints: list[AgentContextCheckpoint] = []

    async def save_run_checkpoint(self, **kwargs):
        checkpoint = AgentContextCheckpoint(
            id=uuid4(),
            thread_id=kwargs["run"].thread_id,
            run_id=kwargs["run"].id,
            checkpoint_namespace="test",
            checkpoint_id=kwargs.get("checkpoint_id", f"checkpoint-{len(self.checkpoints) + 1}"),
            graph_version=kwargs["run"].graph_version,
            state_ref=kwargs.get("state_ref", ""),
            state_summary=kwargs["state_summary"],
        )
        self.checkpoints.append(checkpoint)
        return checkpoint

    async def latest_for_run(self, *, run_id):
        matching = [checkpoint for checkpoint in self.checkpoints if checkpoint.run_id == run_id]
        return matching[-1] if matching else None
