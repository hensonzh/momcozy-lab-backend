from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from ....core.errors import ApiError
from ..execution import AgentRunExecutionResult, AgentRunHandler
from ..models import AgentRun
from ..repository import AgentRuntimeRepository
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

        graph = self._build_graph(run=run)
        final_state = await graph.ainvoke(_initial_state(run))
        status = final_state.get("outcome_status")
        if status == "completed":
            return AgentRunExecutionResult(status="completed", final_text=str(final_state.get("final_text") or ""))
        if status == "waiting_for_confirmation":
            pending_action_id = _uuid_or_none(final_state.get("pending_action_id"))
            return AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=pending_action_id)
        raise ApiError(code="missing_runtime_outcome", message="Agent graph did not produce a terminal or waiting outcome.", status=502)

    def _build_graph(self, *, run: AgentRun):
        graph = StateGraph(AgentGraphState)
        graph.add_node("load_context", self._load_context_node(run=run))
        graph.add_node("safety_gate", self._checkpointing_node(run=run, node_name="safety_gate"))
        graph.add_node("sdk_reasoning", self._sdk_reasoning_node(run=run))
        graph.add_node("confirmation_interrupt", self._checkpointing_node(run=run, node_name="confirmation_interrupt"))
        graph.add_node("final_response", self._checkpointing_node(run=run, node_name="final_response"))
        graph.add_node("finish", self._checkpointing_node(run=run, node_name="finish"))
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "safety_gate")
        graph.add_edge("safety_gate", "sdk_reasoning")
        graph.add_conditional_edges(
            "sdk_reasoning",
            _route_after_reasoning,
            {
                "confirmation_interrupt": "confirmation_interrupt",
                "final_response": "final_response",
            },
        )
        graph.add_edge("confirmation_interrupt", "finish")
        graph.add_edge("final_response", "finish")
        graph.add_edge("finish", END)
        return graph.compile()

    def _load_context_node(self, *, run: AgentRun) -> GraphNode:
        async def load_context(state: AgentGraphState) -> dict[str, Any]:
            current_message = await self.repository.get_latest_user_message_for_run(run_id=run.id)
            if current_message is None:
                raise ApiError(code="missing_user_message", message="Agent run has no user message.", status=409)
            next_state = _node_update(
                state,
                node_name="load_context",
                current_user_message_id=str(current_message.id),
            )
            await self._save_checkpoint(run=run, node_name="load_context", state=state | next_state)
            return next_state

        return load_context

    def _sdk_reasoning_node(self, *, run: AgentRun) -> GraphNode:
        async def sdk_reasoning(state: AgentGraphState) -> dict[str, Any]:
            result = await self.node_handler(run)
            update = _node_update(
                state,
                node_name="sdk_reasoning",
                outcome_status=result.status,
                final_text=result.final_text,
                pending_action_id=str(result.pending_action_id) if result.pending_action_id else None,
            )
            await self._save_checkpoint(run=run, node_name="sdk_reasoning", state=state | update)
            return update

        return sdk_reasoning

    def _checkpointing_node(self, *, run: AgentRun, node_name: str) -> GraphNode:
        async def checkpointing_node(state: AgentGraphState) -> dict[str, Any]:
            update = _node_update(state, node_name=node_name)
            await self._save_checkpoint(run=run, node_name=node_name, state=state | update)
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
                "outcome_status": state.get("outcome_status", ""),
            },
        )


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


def _route_after_reasoning(state: AgentGraphState) -> str:
    status = state.get("outcome_status")
    if status == "waiting_for_confirmation":
        return "confirmation_interrupt"
    if status == "completed":
        return "final_response"
    raise ApiError(code="missing_runtime_outcome", message="Agent graph did not produce a terminal or waiting outcome.", status=502)


def _uuid_or_none(value: object) -> UUID | None:
    normalized = str(value or "").strip()
    return UUID(normalized) if normalized else None
