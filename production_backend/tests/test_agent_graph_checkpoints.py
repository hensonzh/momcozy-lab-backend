import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.agent_runtime.graphs import AgentGraphCheckpointStore, GraphCheckpointRef, checkpoint_namespace
from production_backend.app.modules.agent_runtime.models import AgentContextCheckpoint, AgentRun


def test_graph_checkpoint_store_saves_run_checkpoint_with_thread_namespace() -> None:
    run = _run()
    nested_id = uuid4()
    repository = FakeCheckpointRepository()

    checkpoint = asyncio.run(
        AgentGraphCheckpointStore(repository=repository).save_run_checkpoint(
            run=run,
            checkpoint_id="checkpoint-1",
            state_ref="oss://agent-state/checkpoint-1.json",
            state_summary={
                "run_id": run.id,
                "pending_action_id": nested_id,
                "visited_nodes": ("load_context", "sdk_reasoning"),
                "created_for": date(2026, 7, 2),
                "observed_at": datetime(2026, 7, 2, 10, 15, tzinfo=timezone.utc),
            },
        )
    )

    assert checkpoint.checkpoint_namespace == checkpoint_namespace(thread_id=run.thread_id, graph_version=run.graph_version)
    assert checkpoint.checkpoint_id == "checkpoint-1"
    assert checkpoint.state_ref == "oss://agent-state/checkpoint-1.json"
    assert checkpoint.state_summary["run_id"] == str(run.id)
    assert checkpoint.state_summary["pending_action_id"] == str(nested_id)
    assert checkpoint.state_summary["visited_nodes"] == ["load_context", "sdk_reasoning"]
    assert checkpoint.state_summary["created_for"] == "2026-07-02"
    assert checkpoint.state_summary["observed_at"].startswith("2026-07-02T10:15:00")


def test_graph_checkpoint_store_generates_unique_checkpoint_id() -> None:
    run = _run()
    checkpoint = asyncio.run(AgentGraphCheckpointStore(repository=FakeCheckpointRepository()).save_run_checkpoint(run=run, state_summary={}))

    assert checkpoint.checkpoint_id.startswith(f"{run.id}:")
    assert len(checkpoint.checkpoint_id) > len(str(run.id))


def test_graph_checkpoint_store_loads_by_ref_thread_and_run() -> None:
    run = _run()
    repository = FakeCheckpointRepository()
    store = AgentGraphCheckpointStore(repository=repository)
    saved = asyncio.run(store.save_run_checkpoint(run=run, checkpoint_id="checkpoint-1", state_summary={}))

    by_ref = asyncio.run(
        store.get_checkpoint(
            ref=GraphCheckpointRef(
                checkpoint_namespace=saved.checkpoint_namespace,
                checkpoint_id=saved.checkpoint_id,
            )
        )
    )
    by_thread = asyncio.run(store.latest_for_thread(thread_id=run.thread_id, graph_version=run.graph_version))
    by_run = asyncio.run(store.latest_for_run(run_id=run.id))

    assert by_ref is saved
    assert by_thread is saved
    assert by_run is saved


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


class FakeCheckpointRepository:
    def __init__(self) -> None:
        self.checkpoints: list[AgentContextCheckpoint] = []

    async def create_context_checkpoint(self, **kwargs) -> AgentContextCheckpoint:
        checkpoint = AgentContextCheckpoint(
            id=uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            checkpoint_namespace=kwargs["checkpoint_namespace"],
            checkpoint_id=kwargs["checkpoint_id"],
            graph_version=kwargs["graph_version"],
            state_ref=kwargs["state_ref"],
            state_summary=kwargs["state_summary"],
        )
        self.checkpoints.append(checkpoint)
        return checkpoint

    async def get_context_checkpoint(self, *, checkpoint_namespace: str, checkpoint_id: str) -> AgentContextCheckpoint | None:
        return next(
            (
                checkpoint
                for checkpoint in self.checkpoints
                if checkpoint.checkpoint_namespace == checkpoint_namespace and checkpoint.checkpoint_id == checkpoint_id
            ),
            None,
        )

    async def get_latest_context_checkpoint_for_thread(
        self,
        *,
        thread_id: UUID,
        checkpoint_namespace: str,
    ) -> AgentContextCheckpoint | None:
        matching = [
            checkpoint
            for checkpoint in self.checkpoints
            if checkpoint.thread_id == thread_id and checkpoint.checkpoint_namespace == checkpoint_namespace
        ]
        return matching[-1] if matching else None

    async def get_latest_context_checkpoint_for_run(self, *, run_id: UUID) -> AgentContextCheckpoint | None:
        matching = [checkpoint for checkpoint in self.checkpoints if checkpoint.run_id == run_id]
        return matching[-1] if matching else None
