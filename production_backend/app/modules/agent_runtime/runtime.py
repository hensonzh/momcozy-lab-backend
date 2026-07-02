from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...core.errors import ApiError
from .execution import AgentRunExecutionResult
from .graphs import AgentGraphRegistry, default_graph_registry
from .models import AgentMessage, AgentRun
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
        tool_registry: ToolContractRegistry | None = None,
        input_builder: ModelInputBuilder | None = None,
        config: AgentRuntimeExecutorConfig | None = None,
    ) -> None:
        self.repository = repository
        self.sdk_runner = sdk_runner
        self.graph_registry = graph_registry or default_graph_registry()
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
            raise ApiError(code="action_proposal_not_wired", message="SDK action proposal handling is not wired yet.", status=503)
        final_text = result.final_text.strip()
        if not final_text:
            raise ApiError(code="empty_agent_response", message="Agent runtime returned an empty response.", status=502)
        return AgentRunExecutionResult(status="completed", final_text=final_text)


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
