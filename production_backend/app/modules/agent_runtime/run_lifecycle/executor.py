from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ....core.errors import ApiError
from ....core.logging import log_agent_runtime_event
from ....infrastructure.object_storage.base import ObjectStorage
from ...auth import CurrentUser
from ..actions.policy import AgentActionPolicy, AgentActionPolicyDecision
from ..agents.cozymate_service_agent.context import BusinessFactsProjector
from ..agents.cozymate_service_agent.prompts import (
    ContextProjection,
    DEFAULT_STABLE_SYSTEM_PROMPT,
    ModelInputBuilder,
)
from ..agents.cozymate_service_agent.skill_registry import (
    AgentServiceSkill,
    AgentServiceSkillRegistry,
    default_service_skill_registry,
)
from ..agents.cozymate_service_agent.service_skills import ServiceSkillId
from ..agents.cozymate_service_agent.tools import (
    ToolContractRegistry,
    ToolExecutor,
    ToolNamespace,
    ToolNamespaceRegistry,
    default_tool_namespace_registry,
    default_tool_registry,
)
from ..agents.cozymate_service_agent.tools.schemas import tool_input_schema
from ..agents.main_coordinator_agent import (
    AgentId,
    RoutingPlan,
    plan_current_request,
)
from ..event_stream.sink import AgentEventSink
from ..event_stream.transient import AgentTransientStream
from ..event_semantics import (
    action_event_payload_semantic,
    artifact_event_payload_semantic,
    progress_live_dedupe_key,
    run_progress_payload,
    with_tool_event_semantic,
)
from ..graphs import AgentGraphCheckpointStore, AgentGraphRegistry, default_graph_registry
from ..memory.service import AgentMemoryService
from ..models import AgentAction, AgentArtifact, AgentEvent, AgentMessage, AgentRun
from ..payloads import DEFAULT_MAX_INLINE_PAYLOAD_BYTES, maybe_externalize_json_payload
from ..repository import AgentRuntimeRepository
from ..response_text import sanitize_agent_response_text
from ..sdk import (
    OpenAIAgentsSdkRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    SdkToolNamespace,
    sdk_tool_name,
)
from .execution import AgentRunExecutionResult
from .quick_replies import QuickReplyFinalizer
from .state_store import AgentRuntimeStateStore


LOAD_SERVICE_SKILL_TOOL_NAME = "load_service_skill"
LOGGER = logging.getLogger("production_backend.agent_runtime.executor")
QUICK_REPLIES_TOOL_NAME = "ui_quick_replies_create"
MAIN_MODEL_EXCLUDED_TOOL_NAMES = frozenset({QUICK_REPLIES_TOOL_NAME})
LOAD_SERVICE_SKILL_INPUT_SCHEMA: dict[str, Any] = {
    "title": "LoadServiceSkillInput",
    "type": "object",
    "additionalProperties": False,
    "required": ["service_skill_id"],
    "properties": {
        "service_skill_id": {
            "type": "string",
            "enum": [
                ServiceSkillId.BIRTH_PREP.value,
                ServiceSkillId.MILK_MANAGEMENT.value,
                ServiceSkillId.HEALTH_CONSULTATION.value,
                ServiceSkillId.EMOTION_SUPPORT.value,
                ServiceSkillId.DEVICE_GUIDANCE.value,
            ],
            "description": "要加载的具体服务技能 id。",
        }
    },
}
DEFAULT_RESIDENT_SERVICE_SKILL_TTL_TURNS = 3


@dataclass(frozen=True)
class AgentRuntimeExecutorConfig:
    stable_system_prompt: str = DEFAULT_STABLE_SYSTEM_PROMPT
    history_limit: int = 40
    memory_limit: int = 5
    recent_run_fact_limit: int = 5
    resident_service_skill_ttl_turns: int = DEFAULT_RESIDENT_SERVICE_SKILL_TTL_TURNS


@dataclass
class _AgentTurnContext:
    current_message: AgentMessage
    messages: list[AgentMessage]
    memory_projection: list[dict[str, Any]]
    service_skills: tuple[AgentServiceSkill, ...]
    routing_plan: RoutingPlan
    recent_run_facts: list[dict[str, Any]]
    recent_loaded_service_skills: list[dict[str, Any]]
    resident_loaded_service_skill: dict[str, Any] | None
    expired_loaded_service_skills: list[dict[str, Any]]
    timings_ms: dict[str, float]


@dataclass(frozen=True)
class _AgentTurnToolScope:
    tool_namespaces: tuple[ToolNamespace, ...]
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class _PreparedModelTurn:
    projection: ContextProjection
    model_input: list[dict[str, Any]]


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
        tool_namespace_registry: ToolNamespaceRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        event_sink: AgentEventSink | None = None,
        action_policy: AgentActionPolicy | None = None,
        memory_service: AgentMemoryService | None = None,
        service_skill_registry: AgentServiceSkillRegistry | None = None,
        business_facts_projector: BusinessFactsProjector | None = None,
        transient_stream: AgentTransientStream | None = None,
        quick_reply_finalizer: QuickReplyFinalizer | None = None,
        input_builder: ModelInputBuilder | None = None,
        config: AgentRuntimeExecutorConfig | None = None,
        object_storage: ObjectStorage | None = None,
        max_inline_artifact_payload_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.sdk_runner = sdk_runner
        self.graph_registry = graph_registry or default_graph_registry()
        self.checkpoint_store = checkpoint_store
        self.state_store = state_store
        self.tool_registry = tool_registry or default_tool_registry()
        self.tool_namespace_registry = tool_namespace_registry or default_tool_namespace_registry(self.tool_registry)
        self.tool_executor = tool_executor
        self.event_sink = event_sink
        self.action_policy = action_policy or AgentActionPolicy()
        self.memory_service = memory_service
        self.service_skill_registry = service_skill_registry or default_service_skill_registry()
        self.business_facts_projector = business_facts_projector
        self.transient_stream = transient_stream
        self.quick_reply_finalizer = quick_reply_finalizer
        self.input_builder = input_builder or ModelInputBuilder()
        self.config = config or AgentRuntimeExecutorConfig()
        self.object_storage = object_storage
        self.max_inline_artifact_payload_bytes = max_inline_artifact_payload_bytes
        self.clock = clock or _utcnow
        self._run_loaded_service_skill_ids: dict[UUID, set[str]] = {}
        self._run_assistant_message_ids: dict[UUID, UUID] = {}
        self._run_text_stream_buffers: dict[UUID, str] = {}
        self._run_text_stream_emitted: dict[UUID, str] = {}

    async def __call__(self, run: AgentRun) -> AgentRunExecutionResult:
        return await self.execute(run=run)

    async def execute(self, *, run: AgentRun) -> AgentRunExecutionResult:
        run_started_at = perf_counter()
        graph = self.graph_registry.get(run.graph_version)
        if graph.runtime_pattern != run.runtime_pattern:
            raise ApiError(code="runtime_graph_mismatch", message="Run runtime pattern does not match graph version.", status=409)
        self._run_assistant_message_ids[run.id] = uuid4()
        self._run_loaded_service_skill_ids[run.id] = set()
        self._run_text_stream_buffers[run.id] = ""
        self._run_text_stream_emitted[run.id] = ""
        try:
            turn_context = await self._load_turn_context(run=run)
            if turn_context.resident_loaded_service_skill is not None:
                self._run_loaded_service_skill_ids[run.id].add(_text(turn_context.resident_loaded_service_skill, "service_skill_id"))
            tool_scope = self._tool_scope_for_turn()
            await self._append_progress(run=run, phase="context_ready", label="我先理解一下你的需求～")
            prepared_turn = await self._prepare_model_turn(run=run, turn_context=turn_context, tool_scope=tool_scope)
            result = await self._run_model_turn(run=run, turn_context=turn_context, tool_scope=tool_scope, prepared_turn=prepared_turn)
            return await self._finalize_turn_result(
                run=run,
                turn_context=turn_context,
                result=result,
                run_started_at=run_started_at,
            )
        except Exception as exc:
            self._log_executor_timing(
                run=run,
                status="failed",
                timings_ms={"total_executor_ms": _elapsed_ms(run_started_at)},
                error_type=type(exc).__name__,
            )
            raise
        finally:
            self._run_assistant_message_ids.pop(run.id, None)
            self._run_loaded_service_skill_ids.pop(run.id, None)
            self._run_text_stream_buffers.pop(run.id, None)
            self._run_text_stream_emitted.pop(run.id, None)

    async def _load_turn_context(self, *, run: AgentRun) -> _AgentTurnContext:
        timings_ms: dict[str, float] = {}
        await self._append_progress(run=run, phase="context_loading", label="我已经收到你的消息啦～")
        context_started_at = perf_counter()
        current_message = await self.repository.get_latest_user_message_for_run(run_id=run.id)
        if current_message is None:
            raise ApiError(code="missing_user_message", message="Agent run has no user message.", status=409)
        messages = await self.repository.list_messages_for_thread(thread_id=run.thread_id, limit=self.config.history_limit)
        memory_projection = await self._memory_projection(run=run)
        timings_ms["context_base"] = _elapsed_ms(context_started_at)

        service_skills = self.service_skill_registry.list()
        routing_started_at = perf_counter()
        routing_plan = plan_current_request(user_message_text=_message_text(current_message))
        timings_ms["routing"] = _elapsed_ms(routing_started_at)

        facts_started_at = perf_counter()
        recent_summaries = await self._recent_run_summaries(run=run)
        selected_history_messages = _history_messages_before(messages=messages, before_sequence=current_message.sequence)
        selected_history_message_ids = {str(message.id) for message in selected_history_messages}
        selected_history_assistant_run_ids = {
            str(message.run_id) for message in selected_history_messages if message.role == "assistant" and message.run_id is not None
        }
        recent_run_facts = _recent_run_fact_projection_items(
            summaries=recent_summaries,
            selected_history_message_ids=selected_history_message_ids,
            selected_history_assistant_run_ids=selected_history_assistant_run_ids,
        )
        recent_loaded_service_skills = _recent_loaded_service_skills(recent_summaries)
        resident_loaded_service_skill, expired_loaded_service_skills = _resident_loaded_service_skill_context(
            summaries=recent_summaries,
            service_skill_registry=self.service_skill_registry,
            tool_namespace_registry=self.tool_namespace_registry,
            ttl_turns=self.config.resident_service_skill_ttl_turns,
        )
        timings_ms["facts_projection"] = _elapsed_ms(facts_started_at)

        await self._record_routing_decision(run=run, current_message=current_message, routing_plan=routing_plan)
        return _AgentTurnContext(
            current_message=current_message,
            messages=messages,
            memory_projection=memory_projection,
            service_skills=service_skills,
            routing_plan=routing_plan,
            recent_run_facts=recent_run_facts,
            recent_loaded_service_skills=recent_loaded_service_skills,
            resident_loaded_service_skill=resident_loaded_service_skill,
            expired_loaded_service_skills=expired_loaded_service_skills,
            timings_ms=timings_ms,
        )

    def _tool_scope_for_turn(self) -> _AgentTurnToolScope:
        tool_namespaces: tuple[ToolNamespace, ...] = (
            self.tool_namespace_registry.list()
            if self.tool_executor is not None and self.sdk_runner.supports_tool_namespaces()
            else ()
        )
        if self.tool_executor is None:
            business_tool_names: tuple[str, ...] = ()
        else:
            business_tool_names = tuple(
                tool_name
                for tool_name in self.tool_registry.names_for_sdk()
                if tool_name not in MAIN_MODEL_EXCLUDED_TOOL_NAMES
            )
        tool_names = (LOAD_SERVICE_SKILL_TOOL_NAME, *business_tool_names)
        return _AgentTurnToolScope(tool_namespaces=tool_namespaces, tool_names=tool_names)

    async def _prepare_model_turn(
        self,
        *,
        run: AgentRun,
        turn_context: _AgentTurnContext,
        tool_scope: _AgentTurnToolScope,
    ) -> _PreparedModelTurn:
        model_visible_state = _model_visible_state_projection(
            recent_loaded_service_skills=turn_context.recent_loaded_service_skills,
            resident_loaded_service_skill=turn_context.resident_loaded_service_skill,
            expired_loaded_service_skills=turn_context.expired_loaded_service_skills,
            routing_plan=turn_context.routing_plan,
        )
        projection = ContextProjection(
            stable_system_prompt=self.config.stable_system_prompt,
            selected_conversation_history=_history_before(
                messages=turn_context.messages,
                before_sequence=turn_context.current_message.sequence,
            ),
            current_state_projection=model_visible_state,
            user_context=_user_context(current_message=turn_context.current_message, now=self.clock()),
            recent_run_facts=turn_context.recent_run_facts,
            memory_projection=turn_context.memory_projection,
            fresh_business_facts={},
        )
        model_input = self.input_builder.build(
            projection=projection,
            current_user_message=_to_model_message(turn_context.current_message),
        )
        return _PreparedModelTurn(projection=projection, model_input=model_input)

    async def _run_model_turn(
        self,
        *,
        run: AgentRun,
        turn_context: _AgentTurnContext,
        tool_scope: _AgentTurnToolScope,
        prepared_turn: _PreparedModelTurn,
    ) -> Any:
        await self._append_progress(run=run, phase="model_reasoning", label="我想一下")
        model_started_at = perf_counter()
        result = await self.sdk_runner.run_reasoning(
            SdkNodeRequest(
                run_id=str(run.id),
                thread_id=str(run.thread_id),
                actor_user_id=str(run.actor_user_id),
                instructions=_sdk_instructions(projection=prepared_turn.projection),
                model_input=prepared_turn.model_input,
                tool_names=tool_scope.tool_names,
                tool_namespaces=_sdk_tool_namespaces(tool_scope.tool_namespaces),
                tool_search_enabled=_tool_search_enabled(tool_scope.tool_namespaces),
                tools=self._sdk_tools(run=run, tool_names=tool_scope.tool_names, tool_namespaces=tool_scope.tool_namespaces),
                prompt_version=run.prompt_version,
                trace_id=run.trace_id,
                service_skill_id=_routing_target_agent_id(turn_context.routing_plan),
                on_text_delta=self._text_delta_handler(run=run),
            )
        )
        turn_context.timings_ms["model_reasoning"] = _elapsed_ms(model_started_at)
        return result

    async def _finalize_turn_result(
        self,
        *,
        run: AgentRun,
        turn_context: _AgentTurnContext,
        result: Any,
        run_started_at: float,
    ) -> AgentRunExecutionResult:
        await self._append_progress(run=run, phase="response_finalizing", label="我在组织回复～")

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
            timings_ms = _timings_with_total(turn_context.timings_ms, run_started_at)
            await self._append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="action.confirmation_required",
                payload=_action_confirmation_event_payload(action),
            )
            await self._save_checkpoint(
                run=run,
                node_name="confirmation_interrupt",
                current_user_message_id=str(turn_context.current_message.id),
                state_summary={
                    "context_refs": [],
                    "pending_action_id": str(action.id),
                    "final_message_id": None,
                    "timings_ms": timings_ms,
                },
            )
            self._log_executor_timing(run=run, status="waiting_for_confirmation", timings_ms=timings_ms)
            return AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=action.id)
        pending_timings_ms = _timings_with_total(turn_context.timings_ms, run_started_at)
        pending_action = await self._pending_confirmation_action_from_tool(
            run=run,
            current_user_message_id=str(turn_context.current_message.id),
            timings_ms=pending_timings_ms,
        )
        if pending_action is not None:
            self._log_executor_timing(run=run, status="waiting_for_confirmation", timings_ms=pending_timings_ms)
            return AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=pending_action.id)
        sanitized_response = sanitize_agent_response_text(str(result.final_text or ""))
        final_text = sanitized_response.text.strip()
        if not final_text and str(result.final_text or "").strip():
            final_text = "我已经整理好了。"
        if not final_text:
            raise ApiError(code="empty_agent_response", message="Agent runtime returned an empty response.", status=502)
        finish_timings_ms = _timings_with_total(turn_context.timings_ms, run_started_at)
        await self._save_checkpoint(
            run=run,
            node_name="finish",
            current_user_message_id=str(turn_context.current_message.id),
            state_summary={
                "context_refs": [],
                "pending_action_id": None,
                "final_message_id": None,
                "final_response_ready": True,
                "timings_ms": finish_timings_ms,
            },
        )
        await self._upsert_run_summary(
            run=run,
            current_message=turn_context.current_message,
            result=result,
            turn_context=turn_context,
            final_text=final_text,
        )
        quick_reply_started_at = perf_counter()
        quick_replies = await self._generate_quick_replies(
            run=run,
            turn_context=turn_context,
            final_text=final_text,
            artifacts=list(result.artifacts or []),
        )
        finish_timings_ms["quick_reply_finalizer"] = _elapsed_ms(quick_reply_started_at)
        self._log_executor_timing(
            run=run,
            status="completed",
            timings_ms=finish_timings_ms,
            final_text_length=len(final_text),
            quick_reply_count=len(quick_replies),
        )
        return AgentRunExecutionResult(
            status="completed",
            final_text=final_text,
            assistant_message_id=self._run_assistant_message_ids.get(run.id),
            quick_replies=quick_replies,
        )

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
                payload=_artifact_event_payload(artifact),
            )

    def _text_delta_handler(self, *, run: AgentRun) -> Callable[[str], Awaitable[None]] | None:
        event_publisher = self.event_sink
        transient_stream = self.transient_stream
        if event_publisher is None and transient_stream is None:
            return None

        async def publish(delta: str) -> None:
            raw_text = f"{self._run_text_stream_buffers.get(run.id, '')}{delta or ''}"
            self._run_text_stream_buffers[run.id] = raw_text
            sanitized_text = sanitize_agent_response_text(raw_text).text
            emitted_text = self._run_text_stream_emitted.get(run.id, "")
            if sanitized_text.startswith(emitted_text):
                sanitized_delta = sanitized_text[len(emitted_text) :]
            else:
                sanitized_delta = sanitized_text
            if not sanitized_delta:
                return
            self._run_text_stream_emitted[run.id] = sanitized_text
            message_stream_id = str(self._run_assistant_message_ids.get(run.id) or "assistant")
            if event_publisher is not None:
                await event_publisher.publish_message_delta(
                    thread_id=run.thread_id,
                    run_id=run.id,
                    delta=sanitized_delta,
                    message_stream_id=message_stream_id,
                )
                return
            if transient_stream is not None:
                await transient_stream.publish_message_delta(
                    thread_id=run.thread_id,
                    run_id=run.id,
                    delta=sanitized_delta,
                    message_stream_id=message_stream_id,
                )

        return publish

    def _sdk_tools(
        self,
        *,
        run: AgentRun,
        tool_names: tuple[str, ...],
        tool_namespaces: tuple[ToolNamespace, ...],
    ) -> tuple[SdkToolDefinition, ...]:
        if self.tool_executor is None:
            return (self._load_service_skill_tool_definition(run=run),)
        namespace_by_tool = _namespace_by_tool(tool_namespaces)
        business_tools = tuple(
            self._sdk_tool_definition(
                run=run,
                tool_name=tool_name,
                namespace=namespace_by_tool.get(tool_name),
            )
            for tool_name in tool_names
            if tool_name != LOAD_SERVICE_SKILL_TOOL_NAME
        )
        return (self._load_service_skill_tool_definition(run=run), *business_tools)

    def _load_service_skill_tool_definition(self, *, run: AgentRun) -> SdkToolDefinition:
        async def invoke_json(args_json: str) -> str:
            args = _json_object(args_json)
            output = await self._invoke_load_service_skill_tool(run=run, args=args)
            await self._append_progress(run=run, phase="model_reasoning_after_tool", label="我接着处理下一步")
            return json.dumps(output, ensure_ascii=False, sort_keys=True)

        return SdkToolDefinition(
            contract_name=LOAD_SERVICE_SKILL_TOOL_NAME,
            sdk_name=LOAD_SERVICE_SKILL_TOOL_NAME,
            description=(
                "按 service_skill_id 加载一个 MomCozy 服务技能。"
                "需要进入奶量、产前准备、健康咨询、情绪支持或设备指导流程前先调用；"
                "返回该技能说明、可用工具范围和小型业务事实包。"
            ),
            params_json_schema=LOAD_SERVICE_SKILL_INPUT_SCHEMA,
            invoke_json=invoke_json,
        )

    def _sdk_tool_definition(self, *, run: AgentRun, tool_name: str, namespace: ToolNamespace | None = None) -> SdkToolDefinition:
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
            namespace_name=namespace.name if namespace is not None else "",
            defer_loading=contract.name in set(namespace.deferred_tool_contracts) if namespace is not None else False,
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
        await self._append_progress(run=run, phase="model_reasoning_after_tool", label="我接着处理下一步")
        return json.dumps(result.safe_output, sort_keys=True)

    async def _generate_quick_replies(
        self,
        *,
        run: AgentRun,
        turn_context: _AgentTurnContext,
        final_text: str,
        artifacts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self.quick_reply_finalizer is None:
            return []
        try:
            return await self.quick_reply_finalizer.generate(
                run=run,
                messages=turn_context.messages,
                current_message=turn_context.current_message,
                final_text=final_text,
                artifacts=artifacts,
            )
        except Exception:
            LOGGER.warning("Failed to generate quick replies.", exc_info=True)
            return []

    async def _invoke_load_service_skill_tool(self, *, run: AgentRun, args: dict[str, Any]) -> dict[str, Any]:
        raw_skill_id = _text(args, "service_skill_id") or _text(args, "skill_id")
        try:
            skill_id = ServiceSkillId(raw_skill_id)
        except ValueError as exc:
            raise ApiError(code="invalid_service_skill", message="Unsupported service_skill_id.", status=422) from exc

        skill = self.service_skill_registry.get(skill_id.value)
        call_id = f"sdk-{LOAD_SERVICE_SKILL_TOOL_NAME}-{uuid4().hex}"
        tool_call = await self.repository.start_tool_call(
            run_id=run.id,
            tool_name=LOAD_SERVICE_SKILL_TOOL_NAME,
            call_id=call_id,
            safe_args={"service_skill_id": skill.service_skill_id},
            started_at=_utcnow(),
        )
        started_payload = {
            "tool_call_id": str(tool_call.id),
            "tool_name": LOAD_SERVICE_SKILL_TOOL_NAME,
            "call_id": call_id,
            "label": "加载服务技能",
            "safe_args": {"service_skill_id": skill.service_skill_id},
        }
        started_payload = with_tool_event_semantic(
            started_payload,
            event_type="tool.started",
            tool_name=LOAD_SERVICE_SKILL_TOOL_NAME,
        )
        await self._publish_optimistic_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.started",
            payload=started_payload,
        )
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.started",
            payload=started_payload,
        )
        try:
            facts = await self._fresh_business_facts_for_skill(run=run, skill_id=skill_id)
            output = _load_service_skill_output(
                skill=skill,
                tool_namespaces=_tool_namespaces_for_service_skill(
                    tool_namespace_registry=self.tool_namespace_registry,
                    skill_id=skill_id,
                ),
                business_facts=facts,
                loaded_at=self.clock(),
            )
            completed = await self.repository.complete_tool_call(tool_call=tool_call, completed_at=_utcnow())
            tool_output = await self.repository.create_tool_output(tool_call_id=completed.id, safe_output=output, raw_output_ref="")
            self._run_loaded_service_skill_ids[run.id] = {skill.service_skill_id}
            completed_payload = {
                "tool_call_id": str(completed.id),
                "tool_output_id": str(tool_output.id),
                "tool_name": LOAD_SERVICE_SKILL_TOOL_NAME,
                "call_id": completed.call_id,
                "label": "加载服务技能",
                "safe_output": {
                    "status": "service_skill_loaded",
                    "service_skill_id": skill.service_skill_id,
                    "skill_version": skill.version,
                },
            }
            completed_payload = with_tool_event_semantic(
                completed_payload,
                event_type="tool.completed",
                tool_name=LOAD_SERVICE_SKILL_TOOL_NAME,
                safe_output=completed_payload["safe_output"],
            )
            await self._publish_optimistic_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.completed",
                payload=completed_payload,
            )
            await self._append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.completed",
                payload=completed_payload,
            )
            await self._append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="skill.loaded",
                payload={
                    "tool_call_id": str(completed.id),
                    "tool_output_id": str(tool_output.id),
                    "service_skill_id": skill.service_skill_id,
                    "skill_version": skill.version,
                    "loaded_at": output["loaded_at"],
                    "tool_names": output["tool_scope"]["tool_names"],
                },
            )
            return output
        except ApiError as exc:
            await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code=exc.code)
            failed_payload = {
                "tool_call_id": str(tool_call.id),
                "tool_name": LOAD_SERVICE_SKILL_TOOL_NAME,
                "call_id": call_id,
                "error_code": exc.code,
                "label": "加载服务技能",
            }
            failed_payload = with_tool_event_semantic(
                failed_payload,
                event_type="tool.failed",
                tool_name=LOAD_SERVICE_SKILL_TOOL_NAME,
            )
            await self._publish_optimistic_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.failed",
                payload=failed_payload,
            )
            await self._append_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.failed",
                payload=failed_payload,
            )
            raise

    async def _pending_confirmation_action_from_tool(
        self,
        *,
        run: AgentRun,
        current_user_message_id: str,
        timings_ms: dict[str, float],
    ) -> AgentAction | None:
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
                "timings_ms": dict(timings_ms),
            },
        )
        return pending_action

    async def _append_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict[str, Any]) -> AgentEvent:
        if self.event_sink is not None:
            return await self.event_sink.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
        return await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)

    async def _publish_optimistic_tool_event(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        dedupe_key = _tool_live_dedupe_key(run_id=run_id, event_type=event_type, payload=payload)
        if not dedupe_key:
            return
        if self.event_sink is not None:
            await self.event_sink.publish_application_event(
                thread_id=thread_id,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                dedupe_key=dedupe_key,
                optimistic=True,
                durable=False,
            )
            return
        if self.transient_stream is None:
            return
        try:
            await self.transient_stream.publish_application_event(
                thread_id=thread_id,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                dedupe_key=dedupe_key,
                optimistic=True,
                durable=False,
            )
        except Exception:
            LOGGER.warning("Failed to publish optimistic load_service_skill event.", exc_info=True)

    async def _append_progress(self, *, run: AgentRun, phase: str, label: str) -> None:
        payload = run_progress_payload(phase=phase, label=label)
        semantic = payload.get("semantic")
        dedupe_key = progress_live_dedupe_key(run_id=run.id, semantic=semantic) if isinstance(semantic, dict) else ""
        published_live = False
        if self.event_sink is not None:
            await self.event_sink.publish_progress(
                thread_id=run.thread_id,
                run_id=run.id,
                phase=phase,
                label=label,
                semantic=semantic if isinstance(semantic, dict) else None,
                dedupe_key=dedupe_key,
                optimistic=True,
                durable=False,
            )
            published_live = True
        elif self.transient_stream is not None:
            try:
                await self.transient_stream.publish_progress(
                    thread_id=run.thread_id,
                    run_id=run.id,
                    phase=phase,
                    label=label,
                    semantic=semantic if isinstance(semantic, dict) else None,
                    dedupe_key=dedupe_key,
                    optimistic=True,
                    durable=False,
                )
                published_live = True
            except Exception:
                LOGGER.warning("Failed to publish live run.progress event.", exc_info=True)
        if published_live:
            return
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="run.progress",
            payload=payload,
        )

    def _log_executor_timing(
        self,
        *,
        run: AgentRun,
        status: str,
        timings_ms: dict[str, float],
        final_text_length: int = 0,
        quick_reply_count: int = 0,
        error_type: str = "",
    ) -> None:
        log_agent_runtime_event(
            "agent.run.executor_turn",
            run_id=str(run.id),
            thread_id=str(run.thread_id),
            trace_id=run.trace_id,
            status=status,
            service_skill_id=run.service_skill_id,
            routing_source=run.routing_source,
            timings_ms=timings_ms,
            final_text_length=final_text_length,
            quick_reply_count=quick_reply_count,
            error_type=error_type,
        )

    async def _record_routing_decision(self, *, run: AgentRun, current_message: AgentMessage, routing_plan: RoutingPlan) -> None:
        recorder = getattr(self.repository, "record_routing_decision", None)
        if recorder is None:
            return
        await recorder(
            run_id=run.id,
            thread_id=run.thread_id,
            actor_user_id=run.actor_user_id,
            message_id=current_message.id,
            selected_skill_id=_routing_target_agent_id(routing_plan),
            routing_source=routing_plan.source.value,
            confidence=routing_plan.confidence,
            execution_mode=routing_plan.execution_mode,
            intents=[intent.model_dump(mode="json") for intent in routing_plan.intents],
            reason_codes=list(routing_plan.reason_codes),
            safety_flags=list(routing_plan.safety_flags),
            needs_clarification=routing_plan.needs_clarification,
            tool_scope_version=_tool_scope_version_for_ledger(
                tool_executor_configured=self.tool_executor is not None,
            ),
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

    async def _memory_projection(self, *, run: AgentRun) -> list[dict[str, Any]]:
        if self.memory_service is None:
            return []
        memories = await self.memory_service.list_active_memories(
            owner_user_id=run.actor_user_id,
            limit=self.config.memory_limit,
        )
        return [_memory_projection_item(memory) for memory in memories]

    async def _recent_run_summaries(self, *, run: AgentRun) -> list[Any]:
        return await self.repository.list_recent_run_summaries(
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            limit=max(1, self.config.recent_run_fact_limit, self.config.resident_service_skill_ttl_turns + 1),
            summary_type="run_fact",
            exclude_run_id=run.id,
        )

    async def _fresh_business_facts_for_skill(self, *, run: AgentRun, skill_id: ServiceSkillId) -> dict[str, Any]:
        if self.business_facts_projector is None:
            return {}
        return await self.business_facts_projector.project(
            actor=_run_actor(run),
            run_id=run.id,
            service_skill_id=skill_id,
        )

    async def _upsert_run_summary(
        self,
        *,
        run: AgentRun,
        current_message: AgentMessage,
        result: Any,
        turn_context: _AgentTurnContext,
        final_text: str,
    ) -> None:
        tool_outputs = await self.repository.list_tool_outputs_for_run(run_id=run.id)
        actions = await self.repository.list_actions_for_run(run_id=run.id)
        artifacts = await self.repository.list_artifacts_for_run(run_id=run.id)
        loaded_service_skills = _loaded_service_skill_summaries(tool_outputs=tool_outputs, result_tool_calls=getattr(result, "tool_calls", []))
        service_skill_id = loaded_service_skills[-1]["service_skill_id"] if loaded_service_skills else AgentId.COZYMATE_SERVICE_AGENT.value
        payload = {
            "user_goal": _compact_text(_message_text(current_message), max_chars=500),
            "assistant_conclusion": _compact_text(final_text, max_chars=1000),
            "tools_used": _tool_names_from_summary_sources(tool_outputs=tool_outputs, result_tool_calls=getattr(result, "tool_calls", [])),
            "tool_facts": _tool_fact_projection(tool_outputs),
            "loaded_service_skills": loaded_service_skills,
            "resident_loaded_service_skill": _resident_loaded_service_skill_summary(turn_context.resident_loaded_service_skill),
            "expired_loaded_service_skills": turn_context.expired_loaded_service_skills,
            "actions": [
                {
                    "action_id": str(action.id),
                    "action_type": action.action_type,
                    "status": action.status,
                    "target_type": action.target_type,
                }
                for action in actions[:8]
            ],
            "artifacts": [
                {
                    "artifact_id": str(artifact.id),
                    "artifact_type": artifact.artifact_type,
                    "status": artifact.status,
                }
                for artifact in artifacts[:8]
            ],
        }
        await self.repository.upsert_run_summary(
            run_id=run.id,
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            service_skill_id=service_skill_id,
            summary_type="run_fact",
            schema_version="v1",
            payload=payload,
            source_message_ids=[str(current_message.id)],
            source_tool_call_ids=[str(tool_call.id) for tool_call, _output in tool_outputs],
        )


def _history_before(*, messages: list[AgentMessage], before_sequence: int) -> list[dict[str, Any]]:
    return [_to_model_message(message) for message in _history_messages_before(messages=messages, before_sequence=before_sequence)]


def _history_messages_before(*, messages: list[AgentMessage], before_sequence: int) -> list[AgentMessage]:
    return [
        message for message in messages if message.sequence < before_sequence and message.role in {"user", "assistant"}
    ]


def _routing_target_agent_id(routing_plan: RoutingPlan) -> str:
    return routing_plan.selected_agent_id.value


def _load_service_skill_output(
    *,
    skill: AgentServiceSkill,
    tool_namespaces: tuple[ToolNamespace, ...],
    business_facts: dict[str, Any],
    loaded_at: datetime,
) -> dict[str, Any]:
    tool_names = _tool_names_for_namespaces(tool_namespaces)
    return {
        "schema_version": "service_skill_load.v1",
        "service_skill_id": skill.service_skill_id,
        "skill_version": skill.version,
        "loaded_at": _aware_datetime(loaded_at).astimezone(timezone.utc).isoformat(),
        "skill": {
            "service_skill_id": skill.service_skill_id,
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.prompt_block(),
        },
        "tool_scope": {
            "namespace_names": [namespace.name for namespace in tool_namespaces],
            "tool_names": list(tool_names),
        },
        "business_facts": business_facts,
    }


def _model_visible_state_projection(
    *,
    recent_loaded_service_skills: list[dict[str, Any]],
    resident_loaded_service_skill: dict[str, Any] | None,
    expired_loaded_service_skills: list[dict[str, Any]],
    routing_plan: RoutingPlan,
) -> dict[str, Any]:
    return {
        "agent_mode": "single_downstream_agent",
        "agent_id": routing_plan.selected_agent_id.value,
        "coordinator": {
            "target_kind": routing_plan.target_kind,
            "selected_agent_id": routing_plan.selected_agent_id.value,
            "execution_mode": routing_plan.execution_mode,
            "source": routing_plan.source.value,
            "reason_codes": list(routing_plan.reason_codes),
        },
        "service_skill_context": {
            "recent_loaded_service_skills": recent_loaded_service_skills,
            "resident_loaded_service_skill": resident_loaded_service_skill,
            "expired_loaded_service_skills": expired_loaded_service_skills,
        },
        "execution_mode": "single",
        "needs_clarification": routing_plan.needs_clarification,
        "safety_flags": list(routing_plan.safety_flags),
    }


def _artifact_event_payload(artifact: AgentArtifact) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_id": str(artifact.id),
        "artifact_type": artifact.artifact_type,
        "schema_version": artifact.schema_version,
        "status": artifact.status,
        "artifact": {
            "id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            "status": artifact.status,
            "payload": artifact.payload,
            "raw_payload_ref": artifact.raw_payload_ref,
        },
    }
    if isinstance(artifact.payload, dict):
        payload.update({key: value for key, value in artifact.payload.items() if key in {"form", "card", "card_json", "cart_update", "summary"}})
    payload["semantic"] = artifact_event_payload_semantic(artifact_type=artifact.artifact_type)
    return payload


def _tool_scope_version_for_ledger(*, tool_executor_configured: bool) -> str:
    if not tool_executor_configured:
        return "agent_load_service_skill:v1"
    return "agent_load_service_skill+namespaced_tool_registry:v1"


def _sdk_tool_namespaces(tool_namespaces: tuple[ToolNamespace, ...]) -> tuple[SdkToolNamespace, ...]:
    return tuple(
        SdkToolNamespace(
            name=namespace.name,
            description=namespace.description,
            tool_names=namespace.tool_contracts,
            deferred_tool_names=namespace.deferred_tool_contracts,
        )
        for namespace in tool_namespaces
    )


def _tool_search_enabled(tool_namespaces: tuple[ToolNamespace, ...]) -> bool:
    return any(namespace.deferred_tool_contracts for namespace in tool_namespaces)


def _namespace_by_tool(tool_namespaces: tuple[ToolNamespace, ...]) -> dict[str, ToolNamespace]:
    return {tool_name: namespace for namespace in tool_namespaces for tool_name in namespace.tool_contracts}


def _tool_names_for_namespaces(tool_namespaces: tuple[ToolNamespace, ...]) -> tuple[str, ...]:
    return tuple(sorted({tool_name for namespace in tool_namespaces for tool_name in namespace.tool_contracts}))


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)


def _timings_with_total(timings_ms: dict[str, float], run_started_at: float) -> dict[str, float]:
    return {**timings_ms, "total_before_finish_checkpoint": _elapsed_ms(run_started_at)}


SERVICE_TOOL_NAMESPACE_NAMES: dict[ServiceSkillId, frozenset[str]] = {
    ServiceSkillId.BIRTH_PREP: frozenset({"birth_prep", "hospital_bag_cart", "pump_recommendation"}),
    ServiceSkillId.MILK_MANAGEMENT: frozenset({"milk_management"}),
    ServiceSkillId.HEALTH_CONSULTATION: frozenset({"health_consultation", "milk_management"}),
    ServiceSkillId.EMOTION_SUPPORT: frozenset({"emotion_support", "health_consultation"}),
    ServiceSkillId.DEVICE_GUIDANCE: frozenset({"device_support"}),
}


def _tool_namespaces_for_service_skill(
    *,
    tool_namespace_registry: ToolNamespaceRegistry,
    skill_id: ServiceSkillId,
) -> tuple[ToolNamespace, ...]:
    namespaces = tool_namespace_registry.list()
    allowed_namespace_names = SERVICE_TOOL_NAMESPACE_NAMES.get(skill_id)
    if allowed_namespace_names is None:
        return ()
    return tuple(namespace for namespace in namespaces if namespace.name in allowed_namespace_names)


def _sdk_instructions(*, projection: ContextProjection) -> str:
    return projection.stable_system_prompt


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


RECENT_RUN_FACT_KEYS_FOR_VISIBLE_HISTORY = ("assistant_conclusion", "tools_used", "tool_facts", "actions", "artifacts")


def _recent_run_fact_projection_items(
    *,
    summaries: list[Any],
    selected_history_message_ids: set[str],
    selected_history_assistant_run_ids: set[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for summary in summaries:
        item = _run_fact_projection_item(
            summary,
            selected_history_message_ids=selected_history_message_ids,
            selected_history_assistant_run_ids=selected_history_assistant_run_ids,
        )
        if item is not None:
            items.append(item)
    return items


def _run_fact_projection_item(
    summary: Any,
    *,
    selected_history_message_ids: set[str],
    selected_history_assistant_run_ids: set[str],
) -> dict[str, Any] | None:
    payload = summary.payload if isinstance(summary.payload, dict) else {}
    if _summary_source_is_visible_in_history(summary=summary, selected_history_message_ids=selected_history_message_ids):
        payload = _deduplicated_recent_fact_payload(
            payload,
            assistant_conclusion_visible=str(summary.run_id) in selected_history_assistant_run_ids,
        )
        if not payload:
            return None
    return {
        "run_id": str(summary.run_id),
        "service_skill_id": summary.service_skill_id,
        "schema_version": summary.schema_version,
        "created_at": _iso_or_empty(getattr(summary, "created_at", None)),
        "facts": _compact_mapping(payload, max_items=8, max_chars=2400),
    }


def _summary_source_is_visible_in_history(*, summary: Any, selected_history_message_ids: set[str]) -> bool:
    source_message_ids = getattr(summary, "source_message_ids", None)
    if not isinstance(source_message_ids, (list, tuple, set)):
        return False
    return any(str(message_id) in selected_history_message_ids for message_id in source_message_ids)


def _deduplicated_recent_fact_payload(payload: dict[str, Any], *, assistant_conclusion_visible: bool) -> dict[str, Any]:
    compact_payload: dict[str, Any] = {}
    for key in RECENT_RUN_FACT_KEYS_FOR_VISIBLE_HISTORY:
        if key == "assistant_conclusion" and assistant_conclusion_visible:
            continue
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        compact_payload[key] = value
    return compact_payload


def _resident_loaded_service_skill_context(
    *,
    summaries: list[Any],
    service_skill_registry: AgentServiceSkillRegistry,
    tool_namespace_registry: ToolNamespaceRegistry,
    ttl_turns: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    latest = _latest_loaded_service_skill_record(summaries)
    if latest is None:
        return None, []
    service_skill_id = _text(latest["item"], "service_skill_id")
    if not service_skill_id:
        return None, []
    turns_since_loaded = int(latest["turns_since_loaded"])
    loaded_at = _text(latest["item"], "loaded_at")
    skill_version = _text(latest["item"], "skill_version")
    last_loaded_run_id = str(latest["summary"].run_id)
    expired = {
        "service_skill_id": service_skill_id,
        "skill_version": skill_version,
        "last_loaded_run_id": last_loaded_run_id,
        "loaded_at": loaded_at,
        "expired_reason": "turn_ttl_exceeded",
        "instruction": (
            f"{service_skill_id} was loaded before, but its SKILL.md has been removed from context. "
            "Call load_service_skill if this turn still needs that skill."
        ),
    }
    if ttl_turns <= 0 or turns_since_loaded >= ttl_turns:
        return None, [expired]
    try:
        service_skill = service_skill_registry.get(service_skill_id)
        skill_id = ServiceSkillId(service_skill_id)
    except (KeyError, ValueError):
        return None, [expired]
    tool_namespaces = _tool_namespaces_for_service_skill(
        tool_namespace_registry=tool_namespace_registry,
        skill_id=skill_id,
    )
    return (
        {
            "service_skill_id": service_skill.service_skill_id,
            "skill_version": skill_version or service_skill.version,
            "last_loaded_run_id": last_loaded_run_id,
            "loaded_at": loaded_at,
            "turns_since_loaded": turns_since_loaded,
            "remaining_turns": ttl_turns - turns_since_loaded,
            "instruction": (
                "This SKILL.md is currently available in context. "
                "Use it only if the current user message still belongs to this service flow."
            ),
            "skill": {
                "service_skill_id": service_skill.service_skill_id,
                "name": service_skill.name,
                "description": service_skill.description,
                "instructions": service_skill.prompt_block(),
            },
            "tool_scope": {
                "namespace_names": [namespace.name for namespace in tool_namespaces],
                "tool_names": list(_tool_names_for_namespaces(tool_namespaces)),
            },
        },
        [],
    )


def _latest_loaded_service_skill_record(summaries: list[Any]) -> dict[str, Any] | None:
    for index in range(len(summaries) - 1, -1, -1):
        summary = summaries[index]
        payload = summary.payload if isinstance(summary.payload, dict) else {}
        loaded_skills = payload.get("loaded_service_skills")
        if not isinstance(loaded_skills, list):
            continue
        for item in reversed(loaded_skills):
            if not isinstance(item, dict) or not _text(item, "service_skill_id"):
                continue
            return {
                "summary": summary,
                "item": item,
                "turns_since_loaded": len(summaries) - 1 - index,
            }
    return None


def _recent_loaded_service_skills(summaries: list[Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    recent: list[dict[str, Any]] = []
    for summary in reversed(summaries):
        payload = summary.payload if isinstance(summary.payload, dict) else {}
        loaded_skills = payload.get("loaded_service_skills")
        if not isinstance(loaded_skills, list):
            continue
        for item in reversed(loaded_skills):
            if not isinstance(item, dict):
                continue
            service_skill_id = _text(item, "service_skill_id")
            if not service_skill_id or service_skill_id in seen:
                continue
            seen.add(service_skill_id)
            recent.append(
                {
                    "service_skill_id": service_skill_id,
                    "skill_version": _text(item, "skill_version"),
                    "last_run_id": str(summary.run_id),
                    "loaded_at": _text(item, "loaded_at"),
                    "user_goal": _compact_text(_text(payload, "user_goal"), max_chars=160),
                    "assistant_conclusion": _compact_text(_text(payload, "assistant_conclusion"), max_chars=240),
                }
            )
            if len(recent) >= 3:
                return recent
    return recent


def _loaded_service_skill_summaries(*, tool_outputs: list[tuple[Any, Any]], result_tool_calls: Any) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for tool_call, output in tool_outputs:
        if str(getattr(tool_call, "tool_name", "") or "") != LOAD_SERVICE_SKILL_TOOL_NAME:
            continue
        safe_output = output.safe_output if isinstance(output.safe_output, dict) else {}
        item = _loaded_service_skill_summary_from_output(safe_output)
        if item:
            loaded.append(item)
    if isinstance(result_tool_calls, list):
        for call in result_tool_calls:
            if not isinstance(call, dict) or _text(call, "tool_name") != LOAD_SERVICE_SKILL_TOOL_NAME:
                continue
            safe_output = call.get("safe_output")
            item = _loaded_service_skill_summary_from_output(safe_output if isinstance(safe_output, dict) else {})
            if item and item not in loaded:
                loaded.append(item)
    return loaded


def _loaded_service_skill_summary_from_output(output: dict[str, Any]) -> dict[str, Any]:
    service_skill_id = _text(output, "service_skill_id")
    if not service_skill_id:
        return {}
    tool_scope = output.get("tool_scope")
    tool_names = tool_scope.get("tool_names") if isinstance(tool_scope, dict) else []
    return {
        "service_skill_id": service_skill_id,
        "skill_version": _text(output, "skill_version"),
        "loaded_at": _text(output, "loaded_at"),
        "tool_names": [str(tool_name) for tool_name in tool_names] if isinstance(tool_names, list) else [],
    }


def _resident_loaded_service_skill_summary(resident_loaded_service_skill: dict[str, Any] | None) -> dict[str, Any]:
    if not resident_loaded_service_skill:
        return {}
    tool_scope = resident_loaded_service_skill.get("tool_scope")
    tool_names = tool_scope.get("tool_names") if isinstance(tool_scope, dict) else []
    return {
        "service_skill_id": _text(resident_loaded_service_skill, "service_skill_id"),
        "skill_version": _text(resident_loaded_service_skill, "skill_version"),
        "last_loaded_run_id": _text(resident_loaded_service_skill, "last_loaded_run_id"),
        "loaded_at": _text(resident_loaded_service_skill, "loaded_at"),
        "turns_since_loaded": int(resident_loaded_service_skill.get("turns_since_loaded") or 0),
        "remaining_turns": int(resident_loaded_service_skill.get("remaining_turns") or 0),
        "tool_names": [str(tool_name) for tool_name in tool_names] if isinstance(tool_names, list) else [],
    }


def _tool_names_from_summary_sources(
    *,
    tool_outputs: list[tuple[Any, Any]],
    result_tool_calls: Any,
) -> list[str]:
    names = {str(tool_call.tool_name) for tool_call, _output in tool_outputs if str(getattr(tool_call, "tool_name", "") or "")}
    if isinstance(result_tool_calls, list):
        for item in result_tool_calls:
            if isinstance(item, dict):
                tool_name = _text(item, "tool_name")
                if tool_name:
                    names.add(tool_name)
    return sorted(names)


def _tool_fact_projection(tool_outputs: list[tuple[Any, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for tool_call, output in tool_outputs[:6]:
        if str(getattr(tool_call, "tool_name", "") or "") == LOAD_SERVICE_SKILL_TOOL_NAME:
            continue
        safe_output = output.safe_output if isinstance(output.safe_output, dict) else {}
        facts.append(
            {
                "tool_call_id": str(tool_call.id),
                "tool_name": str(tool_call.tool_name),
                "status": str(tool_call.status),
                "safe_output": _compact_mapping(safe_output, max_items=8, max_chars=1200),
            }
        )
    return facts


def _compact_mapping(payload: dict[str, Any], *, max_items: int, max_chars: int) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in list(payload.items())[:max_items]:
        compacted[str(key)] = _compact_value(value=value, max_chars=max_chars)
    return compacted


def _compact_value(*, value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return _compact_text(value, max_chars=max_chars)
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, dict):
        return _compact_mapping(value, max_items=8, max_chars=max_chars)
    if isinstance(value, list):
        return [_compact_value(value=item, max_chars=max_chars) for item in value[:8]]
    return _compact_text(str(value), max_chars=max_chars)


def _compact_text(value: str, *, max_chars: int) -> str:
    stripped = value.strip()
    return stripped if len(stripped) <= max_chars else f"{stripped[:max_chars]}..."


def _user_context(*, current_message: AgentMessage, now: datetime) -> dict[str, Any]:
    content = current_message.content if isinstance(current_message.content, dict) else {}
    location = _location_context(content.get("location"))
    timezone_name = _text(content, "timezone") or _text(location, "timezone") or "UTC"
    timezone_info, normalized_timezone = _timezone_info(timezone_name)
    current_time = _aware_datetime(now).astimezone(timezone_info).isoformat()
    user_context: dict[str, Any] = {
        "current_time": current_time,
        "timezone": normalized_timezone,
        "locale": _text(content, "locale") or "zh-CN",
    }
    if location:
        user_context["location"] = location
    return user_context


def _location_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    location: dict[str, Any] = {}
    for key in ("country", "region", "city", "timezone"):
        text_value = _text(value, key)
        if text_value:
            location[key] = text_value
    for key in ("latitude", "longitude"):
        number = value.get(key)
        if isinstance(number, int | float):
            location[key] = number
    return location


def _timezone_info(value: str) -> tuple[ZoneInfo, str]:
    try:
        return ZoneInfo(value), value
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC"), "UTC"


def _aware_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _tool_live_dedupe_key(*, run_id: UUID, event_type: str, payload: dict[str, Any]) -> str:
    if event_type not in {"tool.started", "tool.completed", "tool.failed"}:
        return ""
    tool_call_id = str(payload.get("tool_call_id") or payload.get("call_id") or "").strip()
    return f"{run_id}:{event_type}:{tool_call_id}" if tool_call_id else ""


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
        "semantic": action_event_payload_semantic(action_status=action.status),
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
