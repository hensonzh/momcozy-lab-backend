from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from ....core.errors import ApiError
from ....infrastructure.object_storage.base import ObjectStorage
from ...auth import CurrentUser
from ..actions.policy import AgentActionPolicy, AgentActionPolicyDecision
from ..event_stream.sink import AgentEventSink
from ..event_stream.transient import AgentTransientStream
from ..graphs import AgentGraphCheckpointStore, AgentGraphRegistry, default_graph_registry
from ..memory.service import AgentMemoryService
from ..models import AgentAction, AgentEvent, AgentMessage, AgentRun
from ..payloads import DEFAULT_MAX_INLINE_PAYLOAD_BYTES, maybe_externalize_json_payload
from ..prompts import (
    ContextProjection,
    DEFAULT_STABLE_DEVELOPER_PROMPT,
    DEFAULT_STABLE_SYSTEM_PROMPT,
    ModelInputBuilder,
)
from ..repository import AgentRuntimeRepository
from ..routing import RoutingContext, RoutingPlan, ServiceSkillId, SkillRoutingService
from ..sdk import (
    OpenAIAgentsSdkRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    sdk_tool_name,
)
from ..skills import AgentServiceSkill, AgentServiceSkillRegistry, default_service_skill_registry
from ..tools import ToolContractRegistry, ToolExecutor, ToolGroupRegistry, default_tool_group_registry, default_tool_registry
from ..tools.schemas import tool_input_schema
from .execution import AgentRunExecutionResult
from .state_store import AgentRuntimeStateStore


@dataclass(frozen=True)
class AgentRuntimeExecutorConfig:
    stable_system_prompt: str = DEFAULT_STABLE_SYSTEM_PROMPT
    stable_developer_prompt: str = DEFAULT_STABLE_DEVELOPER_PROMPT
    history_limit: int = 40
    memory_limit: int = 5


class AgentRuntimeExecutor:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        sdk_runner: OpenAIAgentsSdkRunner,
        graph_registry: AgentGraphRegistry | None = None,
        checkpoint_store: AgentGraphCheckpointStore | None = None,
        state_store: AgentRuntimeStateStore | None = None,
        tool_registry: ToolContractRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        event_sink: AgentEventSink | None = None,
        action_policy: AgentActionPolicy | None = None,
        memory_service: AgentMemoryService | None = None,
        service_skill_registry: AgentServiceSkillRegistry | None = None,
        routing_service: SkillRoutingService | None = None,
        tool_group_registry: ToolGroupRegistry | None = None,
        transient_stream: AgentTransientStream | None = None,
        input_builder: ModelInputBuilder | None = None,
        config: AgentRuntimeExecutorConfig | None = None,
        object_storage: ObjectStorage | None = None,
        max_inline_artifact_payload_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
    ) -> None:
        self.repository = repository
        self.sdk_runner = sdk_runner
        self.graph_registry = graph_registry or default_graph_registry()
        self.checkpoint_store = checkpoint_store
        self.state_store = state_store
        self.tool_registry = tool_registry or default_tool_registry()
        self.tool_group_registry = tool_group_registry or default_tool_group_registry()
        self.tool_executor = tool_executor
        self.event_sink = event_sink
        self.action_policy = action_policy or AgentActionPolicy()
        self.memory_service = memory_service
        self.service_skill_registry = service_skill_registry or default_service_skill_registry()
        self.routing_service = routing_service or SkillRoutingService()
        self.transient_stream = transient_stream
        self.input_builder = input_builder or ModelInputBuilder()
        self.config = config or AgentRuntimeExecutorConfig()
        self.object_storage = object_storage
        self.max_inline_artifact_payload_bytes = max_inline_artifact_payload_bytes

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
        memory_projection = await self._memory_projection(run=run)
        routing_plan = await self.routing_service.route(_routing_context(run=run, current_message=current_message))
        service_skill = self.service_skill_registry.get(routing_plan.selected_skill_id.value)
        skill_summary = service_skill.state_summary()
        await self._record_routing_decision(run=run, current_message=current_message, routing_plan=routing_plan)
        tool_names = self.tool_group_registry.tool_names_for_plan(routing_plan)
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
                "prompt_version": run.prompt_version,
                "service_skill_key": service_skill.service_skill_id,
                "service_skill_id": str(skill_summary.get("id") or ""),
                "service_skill_version": str(skill_summary.get("version") or ""),
                "service_skill_scope": skill_summary.get("scope") or [],
                "service_skill_deliverables": skill_summary.get("deliverables") or [],
                "selected_tool_group_ids": list(routing_plan.tool_group_ids),
                "routing_source": routing_plan.source.value,
                "routing_confidence": routing_plan.confidence,
                "routing_reason_codes": routing_plan.reason_codes,
                "routing_execution_mode": routing_plan.execution_mode,
                "routing_safety_flags": routing_plan.safety_flags,
            },
            memory_projection=memory_projection,
            fresh_business_facts={},
        )
        model_input = self.input_builder.build(
            projection=projection,
            current_user_message=_to_model_message(current_message),
        )
        await self._record_context_projection(run=run, messages=messages, current_message=current_message, projection=projection)
        await self._save_checkpoint(
            run=run,
            node_name="sdk_reasoning",
            current_user_message_id=str(current_message.id),
            state_summary={
                "context_refs": [],
                "pending_action_id": None,
                "final_message_id": None,
                "service_skill_key": service_skill.service_skill_id,
                "service_skill_id": str(skill_summary.get("id") or ""),
                "routing_source": routing_plan.source.value,
                "routing_confidence": routing_plan.confidence,
                "routing_reason_codes": routing_plan.reason_codes,
                "tool_names": list(tool_names),
            },
        )
        result = await self.sdk_runner.run_reasoning(
            SdkNodeRequest(
                run_id=str(run.id),
                thread_id=str(run.thread_id),
                actor_user_id=str(run.actor_user_id),
                instructions=_sdk_instructions(projection=projection, service_skill=service_skill),
                model_input=model_input,
                tool_names=tool_names,
                tools=self._sdk_tools(run=run, tool_names=tool_names),
                prompt_version=run.prompt_version,
                trace_id=run.trace_id,
                service_skill_id=service_skill.service_skill_id,
                on_text_delta=self._text_delta_handler(run=run),
            )
        )

        action_proposal = _single_action_proposal(result.action_proposals)
        action_decision = self._action_decision_from_proposal(action_proposal) if action_proposal is not None else None

        await self._persist_artifacts_from_result(run=run, artifacts=result.artifacts)

        if action_proposal is not None and action_decision is not None:
            if not action_decision.requires_confirmation:
                raise ApiError(
                    code="direct_agent_action_requires_tool",
                    message="Direct-apply agent actions must be executed through a tool contract.",
                    status=422,
                )
            action = await self._create_action_from_proposal(run=run, proposal=action_proposal, decision=action_decision)
            await self._append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="action.confirmation_required",
                payload=_action_confirmation_event_payload(action),
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
        pending_action = await self._pending_confirmation_action_from_tool(run=run, current_user_message_id=str(current_message.id))
        if pending_action is not None:
            return AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=pending_action.id)
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

    def _action_decision_from_proposal(self, proposal: dict[str, Any]) -> AgentActionPolicyDecision:
        return self.action_policy.validate(
            action_type=_required_text(proposal, "action_type"),
            target_type=_text(proposal, "target_type"),
            side_effect_level=_text(proposal, "side_effect_level"),
        )

    async def _create_action_from_proposal(
        self,
        *,
        run: AgentRun,
        proposal: dict[str, Any],
        decision: AgentActionPolicyDecision,
    ) -> AgentAction:
        return await self.repository.create_action(
            run_id=run.id,
            actor_user_id=run.actor_user_id,
            action_type=decision.action_type,
            target_type=decision.target_type,
            target_id=_text(proposal, "target_id"),
            status="confirmation_required",
            side_effect_level=decision.side_effect_level,
            preview_payload=_dict(proposal, "preview_payload"),
            apply_payload=_dict(proposal, "apply_payload"),
            idempotency_key=_text(proposal, "idempotency_key"),
            expires_at=None,
        )

    async def _persist_artifacts_from_result(self, *, run: AgentRun, artifacts: list[dict[str, Any]]) -> None:
        for artifact_payload in artifacts:
            raw_payload_ref = _text(artifact_payload, "raw_payload_ref")
            externalized_payload = await maybe_externalize_json_payload(
                payload=_dict(artifact_payload, "payload"),
                object_storage=None if raw_payload_ref else self.object_storage,
                run_id=run.id,
                payload_kind="artifacts",
                max_inline_bytes=self.max_inline_artifact_payload_bytes,
            )
            artifact = await self.repository.create_artifact(
                run_id=run.id,
                owner_user_id=run.actor_user_id,
                artifact_type=_required_text(artifact_payload, "artifact_type"),
                schema_version=_text(artifact_payload, "schema_version") or "v1",
                status=_text(artifact_payload, "status") or "created",
                payload=externalized_payload.inline_payload,
                raw_payload_ref=raw_payload_ref or externalized_payload.raw_payload_ref,
            )
            await self._append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="artifact.created",
                payload={"artifact_id": str(artifact.id), "artifact_type": artifact.artifact_type},
            )

    def _text_delta_handler(self, *, run: AgentRun) -> Callable[[str], Awaitable[None]] | None:
        transient_stream = self.transient_stream
        if transient_stream is None:
            return None

        async def publish(delta: str) -> None:
            await transient_stream.publish_message_delta(thread_id=run.thread_id, run_id=run.id, delta=delta)

        return publish

    def _sdk_tools(self, *, run: AgentRun, tool_names: tuple[str, ...]) -> tuple[SdkToolDefinition, ...]:
        if self.tool_executor is None:
            return ()
        return tuple(self._sdk_tool_definition(run=run, tool_name=tool_name) for tool_name in tool_names)

    def _sdk_tool_definition(self, *, run: AgentRun, tool_name: str) -> SdkToolDefinition:
        contract = self.tool_registry.get(tool_name)
        sdk_name = sdk_tool_name(contract.name)

        async def invoke_json(args_json: str) -> str:
            return await self._invoke_sdk_tool(run=run, contract_name=contract.name, sdk_name=sdk_name, args_json=args_json)

        return SdkToolDefinition(
            contract_name=contract.name,
            sdk_name=sdk_name,
            description=contract.description,
            params_json_schema=tool_input_schema(contract.input_schema_ref),
            invoke_json=invoke_json,
        )

    async def _invoke_sdk_tool(self, *, run: AgentRun, contract_name: str, sdk_name: str, args_json: str) -> str:
        if self.tool_executor is None:
            raise ApiError(code="unsupported_operation", message="Tool executor is not configured.", status=501)
        args = _json_object(args_json)
        result = await self.tool_executor.execute(
            actor=_run_actor(run),
            run_id=run.id,
            tool_name=contract_name,
            call_id=f"sdk-{sdk_name}-{uuid4().hex}",
            args=args,
        )
        return json.dumps(result.safe_output, sort_keys=True)

    async def _pending_confirmation_action_from_tool(self, *, run: AgentRun, current_user_message_id: str) -> AgentAction | None:
        if self.tool_executor is None:
            return None
        actions = await self.repository.list_actions_for_run(run_id=run.id)
        pending_action = next((action for action in reversed(actions) if action.status == "confirmation_required"), None)
        if pending_action is None:
            return None
        await self._save_checkpoint(
            run=run,
            node_name="confirmation_interrupt",
            current_user_message_id=current_user_message_id,
            state_summary={
                "context_refs": [],
                "pending_action_id": str(pending_action.id),
                "final_message_id": None,
            },
        )
        return pending_action

    async def _append_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict[str, Any]) -> AgentEvent:
        if self.event_sink is not None:
            return await self.event_sink.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
        return await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)

    async def _record_routing_decision(self, *, run: AgentRun, current_message: AgentMessage, routing_plan: RoutingPlan) -> None:
        recorder = getattr(self.repository, "record_routing_decision", None)
        if recorder is None:
            return
        await recorder(
            run_id=run.id,
            thread_id=run.thread_id,
            actor_user_id=run.actor_user_id,
            message_id=current_message.id,
            selected_skill_id=routing_plan.selected_skill_id.value,
            routing_source=routing_plan.source.value,
            confidence=routing_plan.confidence,
            execution_mode=routing_plan.execution_mode,
            intents=[intent.model_dump(mode="json") for intent in routing_plan.intents],
            reason_codes=list(routing_plan.reason_codes),
            safety_flags=list(routing_plan.safety_flags),
            needs_clarification=routing_plan.needs_clarification,
            tool_scope_version=",".join(routing_plan.tool_group_ids) or "none",
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

    async def _record_context_projection(
        self,
        *,
        run: AgentRun,
        messages: list[AgentMessage],
        current_message: AgentMessage,
        projection: ContextProjection,
    ) -> None:
        if self.state_store is None:
            return
        selected_history = [
            message for message in messages if message.sequence < current_message.sequence and message.role in {"user", "assistant"}
        ]
        await self.state_store.record_context_projection(
            run=run,
            selected_message_ids=[message.id for message in [*selected_history, current_message]],
            source_refs={
                "thread_id": str(run.thread_id),
                "run_id": str(run.id),
                "current_message_id": str(current_message.id),
            },
            projection_summary={
                "history_message_count": len(selected_history),
                "state_keys": sorted(projection.current_state_projection),
                "service_skill_id": _text(projection.current_state_projection, "service_skill_id"),
                "memory_count": len(projection.memory_projection),
                "fresh_business_fact_keys": sorted(projection.fresh_business_facts),
            },
            tool_schema_version="default",
        )

    async def _memory_projection(self, *, run: AgentRun) -> list[dict[str, Any]]:
        if self.memory_service is None:
            return []
        memories = await self.memory_service.list_active_memories(
            owner_user_id=run.actor_user_id,
            limit=self.config.memory_limit,
        )
        return [_memory_projection_item(memory) for memory in memories]


def _history_before(*, messages: list[AgentMessage], before_sequence: int) -> list[dict[str, Any]]:
    return [
        _to_model_message(message) for message in messages if message.sequence < before_sequence and message.role in {"user", "assistant"}
    ]


def _routing_context(*, run: AgentRun, current_message: AgentMessage) -> RoutingContext:
    content = current_message.content if isinstance(current_message.content, dict) else {}
    app_surface = _text(content, "app_surface") or _text(content, "surface")
    pending_action_id = _text(content, "pending_action_id")
    active_workflow = _text(content, "active_workflow")
    active_service_skill_id = _text(content, "active_service_skill_id")
    attachment_types = content.get("attachment_types")
    return RoutingContext(
        run_id=run.id,
        thread_id=run.thread_id,
        actor_user_id=run.actor_user_id,
        message=_message_text(current_message),
        app_surface=app_surface or None,
        active_service_skill_id=_service_skill_id(active_service_skill_id),
        active_workflow=active_workflow or None,
        pending_action_id=_uuid(pending_action_id),
        attachment_types=[item for item in attachment_types if isinstance(item, str)] if isinstance(attachment_types, list) else [],
    )


def _service_skill_id(value: str) -> ServiceSkillId | None:
    if not value:
        return None
    try:
        return ServiceSkillId(value)
    except ValueError:
        return None


def _uuid(value: str) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _sdk_instructions(*, projection: ContextProjection, service_skill: AgentServiceSkill) -> str:
    blocks = [
        projection.stable_system_prompt,
        projection.stable_developer_prompt,
        f"已选择服务技能：{service_skill.service_skill_id}（{service_skill.name}）。",
        "你始终是同一个 CozyMate，不要表现成多个专家或把内部服务技能名称暴露给用户。",
        "本轮只使用运行时提供的工具；如果当前工具不足以完成写入或产物创建，先自然澄清或说明下一步，不要编造已执行。",
        service_skill.prompt_block(),
    ]
    return "\n\n".join(blocks)


def _to_model_message(message: AgentMessage) -> dict[str, Any]:
    role = message.role if message.role in {"user", "assistant"} else "user"
    return {"role": role, "content": _message_text(message)}


def _memory_projection_item(memory: Any) -> dict[str, Any]:
    content = memory.content if isinstance(memory.content, dict) else {}
    return {
        "memory_id": str(memory.id),
        "memory_type": memory.memory_type,
        "summary": _text(content, "summary"),
        "confidence_score": int(memory.confidence_score or 0),
        "updated_at": _iso_or_empty(getattr(memory, "updated_at", None)),
    }


def _iso_or_empty(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else ""


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


def _single_action_proposal(action_proposals: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(action_proposals) > 1:
        raise ApiError(code="too_many_agent_action_proposals", message="Only one agent action proposal is supported per run.", status=422)
    return action_proposals[0] if action_proposals else None


def _action_confirmation_event_payload(action: AgentAction) -> dict[str, Any]:
    return {
        "action_id": str(action.id),
        "action_type": action.action_type,
        "action_status": action.status,
        "target_type": action.target_type,
        "target_id": action.target_id,
        "side_effect_level": action.side_effect_level,
        "preview_payload": action.preview_payload,
    }


def _json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ApiError(code="validation_failed", message="Tool arguments must be valid JSON.", status=422) from exc
    if not isinstance(parsed, dict):
        raise ApiError(code="validation_failed", message="Tool arguments must be a JSON object.", status=422)
    return parsed


def _run_actor(run: AgentRun) -> CurrentUser:
    return CurrentUser(
        user_id=run.actor_user_id,
        subject=str(run.actor_user_id),
        session_id="",
        token_id="",
        roles=frozenset({"user"}),
        permissions=frozenset(),
    )
