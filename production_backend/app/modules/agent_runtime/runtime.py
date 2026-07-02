from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...core.errors import ApiError
from .execution import AgentRunExecutionResult
from .graphs import AgentGraphCheckpointStore, AgentGraphRegistry, default_graph_registry
from .models import AgentAction, AgentMessage, AgentRun
from .prompts import ContextProjection, ModelInputBuilder
from .repository import AgentRuntimeRepository
from .sdk import OpenAIAgentsSdkRunner, SdkNodeRequest
from .tools import ToolContractRegistry, default_tool_registry


@dataclass(frozen=True)
class AgentRuntimeExecutorConfig:
    stable_system_prompt: str = (
        "You are the MomCozy product assistant. Follow product safety policy, use tools only through the application "
        "runtime, and return concise, helpful responses."
    )
    stable_developer_prompt: str = (
        "Use the provided conversation ledger, current state projection, and fresh business facts. Do not rely on "
        "provider session state. Propose confirmable actions instead of directly applying medium or high risk changes."
    )
    history_limit: int = 40


class AgentRuntimeExecutor:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        sdk_runner: OpenAIAgentsSdkRunner,
        graph_registry: AgentGraphRegistry | None = None,
        checkpoint_store: AgentGraphCheckpointStore | None = None,
        tool_registry: ToolContractRegistry | None = None,
        input_builder: ModelInputBuilder | None = None,
        config: AgentRuntimeExecutorConfig | None = None,
    ) -> None:
        self.repository = repository
        self.sdk_runner = sdk_runner
        self.graph_registry = graph_registry or default_graph_registry()
        self.checkpoint_store = checkpoint_store
        self.tool_registry = tool_registry or default_tool_registry()
        self.input_builder = input_builder or ModelInputBuilder()
        self.config = config or AgentRuntimeExecutorConfig()

    async def __call__(self, run: AgentRun) -> AgentRunExecutionResult:
        return await self.execute(run=run)

    async def execute(self, *, run: AgentRun) -> AgentRunExecutionResult:
        graph = self.graph_registry.get(run.graph_version)
        if graph.runtime_pattern != run.runtime_pattern:
            raise ApiError(code="runtime_graph_mismatch", message="Run runtime pattern does not match graph version.", status=409)

        current_message = await self.repository.get_latest_user_message_for_run(run_id=run.id)
        if current_message is None:
            raise ApiError(code="missing_user_message", message="Agent run has no user message.", status=409)

        messages = await self.repository.list_messages_for_thread(thread_id=run.thread_id, limit=self.config.history_limit)
        projection = ContextProjection(
            stable_system_prompt=self.config.stable_system_prompt,
            stable_developer_prompt=self.config.stable_developer_prompt,
            selected_conversation_history=_history_before(messages=messages, before_sequence=current_message.sequence),
            current_state_projection={
                "run_id": str(run.id),
                "thread_id": str(run.thread_id),
                "actor_user_id": str(run.actor_user_id),
                "graph_version": run.graph_version,
                "runtime_pattern": run.runtime_pattern,
            },
            fresh_business_facts={},
        )
        model_input = self.input_builder.build(
            projection=projection,
            current_user_message=_to_model_message(current_message),
        )
        await self._save_checkpoint(
            run=run,
            node_name="sdk_reasoning",
            current_user_message_id=str(current_message.id),
            state_summary={
                "context_refs": [],
                "pending_action_id": None,
                "final_message_id": None,
                "tool_names": list(self.tool_registry.names_for_sdk()),
            },
        )
        result = await self.sdk_runner.run_reasoning(
            SdkNodeRequest(
                run_id=str(run.id),
                thread_id=str(run.thread_id),
                actor_user_id=str(run.actor_user_id),
                instructions=projection.stable_developer_prompt,
                model_input=model_input,
                tool_names=self.tool_registry.names_for_sdk(),
                trace_id=run.trace_id,
            )
        )

        if result.action_proposals:
            action = await self._create_action_from_proposal(run=run, proposal=result.action_proposals[0])
            await self.repository.append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="action.confirmation_required",
                payload={"action_id": str(action.id), "action_type": action.action_type},
            )
            await self._save_checkpoint(
                run=run,
                node_name="confirmation_interrupt",
                current_user_message_id=str(current_message.id),
                state_summary={
                    "context_refs": [],
                    "pending_action_id": str(action.id),
                    "final_message_id": None,
                },
            )
            return AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=action.id)
        final_text = result.final_text.strip()
        if not final_text:
            raise ApiError(code="empty_agent_response", message="Agent runtime returned an empty response.", status=502)
        await self._save_checkpoint(
            run=run,
            node_name="finish",
            current_user_message_id=str(current_message.id),
            state_summary={
                "context_refs": [],
                "pending_action_id": None,
                "final_message_id": None,
                "final_response_ready": True,
            },
        )
        return AgentRunExecutionResult(status="completed", final_text=final_text)

    async def _create_action_from_proposal(self, *, run: AgentRun, proposal: dict[str, Any]) -> AgentAction:
        action_type = _required_text(proposal, "action_type")
        return await self.repository.create_action(
            run_id=run.id,
            actor_user_id=run.actor_user_id,
            action_type=action_type,
            target_type=_text(proposal, "target_type"),
            target_id=_text(proposal, "target_id"),
            status="confirmation_required",
            side_effect_level=_text(proposal, "side_effect_level") or "medium",
            preview_payload=_dict(proposal, "preview_payload"),
            apply_payload=_dict(proposal, "apply_payload"),
            idempotency_key=_text(proposal, "idempotency_key"),
            expires_at=None,
        )

    async def _save_checkpoint(
        self,
        *,
        run: AgentRun,
        node_name: str,
        current_user_message_id: str,
        state_summary: dict[str, Any],
    ) -> None:
        if self.checkpoint_store is None:
            return
        await self.checkpoint_store.save_run_checkpoint(
            run=run,
            state_summary={
                "node_name": node_name,
                "run_id": str(run.id),
                "thread_id": str(run.thread_id),
                "actor_user_id": str(run.actor_user_id),
                "current_user_message_id": current_user_message_id,
                **state_summary,
            },
        )


def _history_before(*, messages: list[AgentMessage], before_sequence: int) -> list[dict[str, Any]]:
    return [_to_model_message(message) for message in messages if message.sequence < before_sequence and message.role in {"user", "assistant"}]


def _to_model_message(message: AgentMessage) -> dict[str, Any]:
    role = message.role if message.role in {"user", "assistant"} else "user"
    return {"role": role, "content": _message_text(message)}


def _message_text(message: AgentMessage) -> str:
    text = message.content.get("text") if isinstance(message.content, dict) else None
    if isinstance(text, str):
        return text
    return ""


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = _text(payload, key)
    if not value:
        raise ApiError(code="invalid_action_proposal", message=f"{key} is required.", status=422)
    return value


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}
