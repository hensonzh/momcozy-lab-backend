from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from ..models import AgentContextCheckpoint, AgentRun
from ..repository import AgentRuntimeRepository


CHECKPOINT_NAMESPACE_PREFIX = "agent-runtime"


@dataclass(frozen=True)
class GraphCheckpointRef:
    checkpoint_namespace: str
    checkpoint_id: str


class AgentGraphCheckpointStore:
    def __init__(self, *, repository: AgentRuntimeRepository) -> None:
        self.repository = repository

    async def save_run_checkpoint(
        self,
        *,
        run: AgentRun,
        state_summary: dict[str, Any],
        checkpoint_id: str | None = None,
        state_ref: str = "",
    ) -> AgentContextCheckpoint:
        namespace = checkpoint_namespace(thread_id=run.thread_id, graph_version=run.graph_version)
        return await self.repository.create_context_checkpoint(
            thread_id=run.thread_id,
            run_id=run.id,
            checkpoint_namespace=namespace,
            checkpoint_id=_checkpoint_id(run=run, checkpoint_id=checkpoint_id),
            graph_version=run.graph_version,
            state_ref=state_ref,
            state_summary=_json_compatible(state_summary),
        )

    async def get_checkpoint(self, *, ref: GraphCheckpointRef) -> AgentContextCheckpoint | None:
        return await self.repository.get_context_checkpoint(
            checkpoint_namespace=ref.checkpoint_namespace,
            checkpoint_id=ref.checkpoint_id,
        )

    async def latest_for_thread(self, *, thread_id: UUID, graph_version: str) -> AgentContextCheckpoint | None:
        return await self.repository.get_latest_context_checkpoint_for_thread(
            thread_id=thread_id,
            checkpoint_namespace=checkpoint_namespace(thread_id=thread_id, graph_version=graph_version),
        )

    async def latest_for_run(self, *, run_id: UUID) -> AgentContextCheckpoint | None:
        return await self.repository.get_latest_context_checkpoint_for_run(run_id=run_id)


def checkpoint_namespace(*, thread_id: UUID, graph_version: str) -> str:
    return f"{CHECKPOINT_NAMESPACE_PREFIX}:{graph_version}:{thread_id}"


def _checkpoint_id(*, run: AgentRun, checkpoint_id: str | None) -> str:
    normalized = str(checkpoint_id or "").strip()
    if normalized:
        return normalized
    return f"{run.id}:{uuid4().hex}"


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
