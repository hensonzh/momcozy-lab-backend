from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from ....core.errors import ApiError
from ..models import AgentRun
from ..repository import AgentRuntimeRepository
from ..run_lifecycle.execution import AgentRunExecutionResult, AgentRunHandler
from .checkpoints import AgentGraphCheckpointStore
from .factory import AgentGraphRegistry, default_graph_registry
from .state import AgentGraphState


GraphNode = Callable[[AgentGraphState], Awaitable[dict[str, Any]]]


class AgentRuntimeGraphRunner:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        node_handler: AgentRunHandler,
        graph_registry: AgentGraphRegistry | None = None,
        checkpoint_store: AgentGraphCheckpointStore | None = None,
    ) -> None:
        self.repository = repository
        self.node_handler = node_handler
        self.graph_registry = graph_registry or default_graph_registry()
        self.checkpoint_store = checkpoint_store

    async def __call__(self, run: AgentRun) -> AgentRunExecutionResult:
        return await self.execute(run=run)

    async def execute(self, *, run: AgentRun) -> AgentRunExecutionResult:
        definition = self.graph_registry.get(run.graph_version)
        if definition.runtime_pattern != run.runtime_pattern:
            raise ApiError(code="runtime_graph_mismatch", message="Run runtime pattern does not match graph version.", status=409)

        resumed = await self._resume_waiting_from_checkpoint(run=run)
        if resumed is not None:
            return resumed

        graph = self._build_graph(run=run)
        final_state = await graph.ainvoke(_initial_state(run))
        status = final_state.get("outcome_status")
        if status == "completed":
            return AgentRunExecutionResult(
                status="completed",
                final_text=str(final_state.get("final_text") or ""),
                assistant_message_id=_uuid_or_none(final_state.get("assistant_message_id")),
                quick_replies=_list_of_dicts(final_state.get("quick_replies")),
            )
        if status == "waiting_for_confirmation":
            pending_action_id = _uuid_or_none(final_state.get("pending_action_id"))
            return AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=pending_action_id)
        raise ApiError(code="missing_runtime_outcome", message="Agent graph did not produce a terminal or waiting outcome.", status=502)

    def _build_graph(self, *, run: AgentRun) -> Any:
        graph = StateGraph(AgentGraphState)
        graph.add_node("sdk_reasoning", cast(Any, self._sdk_reasoning_node(run=run)))
        graph.add_node("finish", cast(Any, self._checkpointing_node(run=run, node_name="finish")))
        graph.add_edge(START, "sdk_reasoning")
        graph.add_edge("sdk_reasoning", "finish")
        graph.add_edge("finish", END)
        return graph.compile()

    def _sdk_reasoning_node(self, *, run: AgentRun) -> GraphNode:
        async def sdk_reasoning(state: AgentGraphState) -> dict[str, Any]:
            result = await self.node_handler(run)
            update = _node_update(
                state,
                node_name="sdk_reasoning",
                outcome_status=result.status,
                final_text=result.final_text,
                pending_action_id=str(result.pending_action_id) if result.pending_action_id else None,
                assistant_message_id=str(result.assistant_message_id) if result.assistant_message_id else None,
                quick_replies=result.quick_replies,
            )
            await self._save_checkpoint(run=run, node_name="sdk_reasoning", state=cast(AgentGraphState, state | update))
            return update

        return sdk_reasoning

    def _checkpointing_node(self, *, run: AgentRun, node_name: str) -> GraphNode:
        async def checkpointing_node(state: AgentGraphState) -> dict[str, Any]:
            update = _node_update(state, node_name=node_name)
            await self._save_checkpoint(run=run, node_name=node_name, state=cast(AgentGraphState, state | update))
            return update

        return checkpointing_node

    async def _save_checkpoint(self, *, run: AgentRun, node_name: str, state: AgentGraphState) -> None:
        if self.checkpoint_store is None:
            return
        await self.checkpoint_store.save_run_checkpoint(
            run=run,
            state_summary={
                "node_name": node_name,
                "run_id": state["run_id"],
                "thread_id": state["thread_id"],
                "current_step": state.get("current_step", node_name),
                "visited_nodes": state.get("visited_nodes", []),
                "pending_action_id": state.get("pending_action_id"),
                "final_message_id": state.get("final_message_id"),
                "assistant_message_id": state.get("assistant_message_id"),
                "quick_reply_count": len(state.get("quick_replies", [])),
                "outcome_status": state.get("outcome_status", ""),
            },
        )

    async def _resume_waiting_from_checkpoint(self, *, run: AgentRun) -> AgentRunExecutionResult | None:
        if self.checkpoint_store is None:
            return None
        latest = await self.checkpoint_store.latest_for_run(run_id=run.id)
        if latest is None:
            return None
        summary = latest.state_summary if isinstance(latest.state_summary, dict) else {}
        pending_action_id = _uuid_or_none(summary.get("pending_action_id"))
        if pending_action_id is None:
            return None
        if summary.get("outcome_status") == "waiting_for_confirmation" or summary.get("node_name") == "confirmation_interrupt":
            return AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=pending_action_id)
        return None


def _initial_state(run: AgentRun) -> AgentGraphState:
    return {
        "run_id": str(run.id),
        "thread_id": str(run.thread_id),
        "actor_user_id": str(run.actor_user_id),
        "current_user_message_id": "",
        "context_refs": [],
        "pending_action_id": None,
        "final_message_id": None,
        "current_step": "start",
        "visited_nodes": [],
    }


def _node_update(state: AgentGraphState, *, node_name: str, **values: Any) -> dict[str, Any]:
    return {
        **values,
        "current_step": node_name,
        "visited_nodes": [*state.get("visited_nodes", []), node_name],
    }


def _uuid_or_none(value: object) -> UUID | None:
    normalized = str(value or "").strip()
    return UUID(normalized) if normalized else None


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
