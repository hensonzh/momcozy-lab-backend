from __future__ import annotations

import json
import logging
import re
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
from ..client_context import project_agent_client_context
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
    ToolHandlerContext,
    ToolHandlerResult,
    ToolNamespace,
    ToolNamespaceRegistry,
    default_tool_namespace_registry,
    default_tool_registry,
)
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
from ..response_text import AppendOnlyAgentResponseProjector, agent_response_text_integrity
from ..sdk import (
    AgentModelRunner,
    SdkNodeRequest,
    SdkToolDefinition,
    SdkToolInvocationResult,
    SdkToolNamespace,
    sdk_tool_name,
)
from .execution import AgentRunExecutionResult
from .quick_replies import QuickReplyFinalizer
from .state_store import AgentRuntimeStateStore


LOAD_SERVICE_SKILL_TOOL_NAME = "load_service_skill"
IMAGE_INSPECT_TOOL_NAME = "images.inspect"
LOGGER = logging.getLogger("production_backend.agent_runtime.executor")
DEFAULT_RESIDENT_SERVICE_SKILL_TTL_TURNS = 3
FORM_TOOL_IDS = {
    "hospital_bag_card_create": "hospital_bag_intake",
    "labor_communication_card_create": "birth_plan_card_intake",
}
FORM_CREATION_TOOL_NAMES = {"birth_plan_form_create", "hospital_bag_form_create"}
MARKDOWN_IMAGE_URL_PATTERN = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))")
MODEL_IMAGE_DATA_URL_PATTERN = re.compile(
    r"^data:image/(?:png|jpe?g|webp|gif);base64,",
    re.IGNORECASE,
)


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
    resident_loaded_service_skill: dict[str, Any] | None
    expired_loaded_service_skills: list[dict[str, Any]]
    timings_ms: dict[str, float]


@dataclass(frozen=True)
class _AgentTurnToolCatalog:
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
        sdk_runner: AgentModelRunner,
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
        self._run_text_projectors: dict[UUID, AppendOnlyAgentResponseProjector] = {}
        self._run_text_stream_emitted: dict[UUID, str] = {}
        self._run_trusted_form_submissions: dict[UUID, dict[str, dict[str, Any]]] = {}
        self._run_business_facts: dict[UUID, dict[ServiceSkillId, dict[str, Any]]] = {}
        self._run_visible_image_urls: dict[UUID, tuple[str, ...]] = {}
        self._run_hospital_bag_cart_groups: dict[UUID, list[dict[str, Any]] | None] = {}
        self._run_text_segment_counts: dict[UUID, int] = {}
        self._unified_load_service_skill = isinstance(self.tool_executor, ToolExecutor)

    async def __call__(self, run: AgentRun) -> AgentRunExecutionResult:
        return await self.execute(run=run)

    async def execute(self, *, run: AgentRun) -> AgentRunExecutionResult:
        run_started_at = perf_counter()
        graph = self.graph_registry.get(run.graph_version)
        if graph.runtime_pattern != run.runtime_pattern:
            raise ApiError(code="runtime_graph_mismatch", message="Run runtime pattern does not match graph version.", status=409)
        self._run_assistant_message_ids[run.id] = uuid4()
        self._run_loaded_service_skill_ids[run.id] = set()
        self._run_text_projectors[run.id] = AppendOnlyAgentResponseProjector()
        self._run_text_stream_emitted[run.id] = ""
        self._run_visible_image_urls[run.id] = ()
        self._run_business_facts[run.id] = {}
        self._run_text_segment_counts[run.id] = 0
        try:
            turn_context = await self._load_turn_context(run=run)
            self._run_trusted_form_submissions[run.id] = _trusted_form_submissions(turn_context.current_message)
            self._run_hospital_bag_cart_groups[run.id] = _current_hospital_bag_cart_groups(turn_context.current_message)
            if turn_context.resident_loaded_service_skill is not None:
                self._run_loaded_service_skill_ids[run.id].add(_text(turn_context.resident_loaded_service_skill, "service_skill_id"))
            tool_catalog = self._tool_catalog_for_turn()
            await self._append_progress(run=run, phase="context_ready", label="我先理解一下你的需求～")
            prepared_turn = self._prepare_model_turn(turn_context=turn_context)
            result = await self._run_model_turn(run=run, turn_context=turn_context, tool_catalog=tool_catalog, prepared_turn=prepared_turn)
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
            self._run_text_projectors.pop(run.id, None)
            self._run_text_stream_emitted.pop(run.id, None)
            self._run_trusted_form_submissions.pop(run.id, None)
            self._run_business_facts.pop(run.id, None)
            self._run_visible_image_urls.pop(run.id, None)
            self._run_hospital_bag_cart_groups.pop(run.id, None)
            self._run_text_segment_counts.pop(run.id, None)

    async def _load_turn_context(self, *, run: AgentRun) -> _AgentTurnContext:
        timings_ms: dict[str, float] = {}
        await self._append_progress(run=run, phase="context_loading", label="我已经收到你的消息啦～")
        context_started_at = perf_counter()
        current_message = await self.repository.get_latest_user_message_for_run(run_id=run.id)
        if current_message is None:
            raise ApiError(code="missing_user_message", message="Agent run has no user message.", status=409)
        messages = await self.repository.list_messages_for_thread(thread_id=run.thread_id, limit=self.config.history_limit)
        self._run_visible_image_urls[run.id] = _visible_assistant_image_urls(
            messages=messages,
            before_sequence=current_message.sequence,
        )
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
        resident_loaded_service_skill, expired_loaded_service_skills = _resident_loaded_service_skill_context(
            summaries=recent_summaries,
            service_skill_registry=self.service_skill_registry,
            tool_registry=self.tool_registry,
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
            resident_loaded_service_skill=resident_loaded_service_skill,
            expired_loaded_service_skills=expired_loaded_service_skills,
            timings_ms=timings_ms,
        )

    def _tool_catalog_for_turn(self) -> _AgentTurnToolCatalog:
        tool_namespaces: tuple[ToolNamespace, ...] = (
            self.tool_namespace_registry.list() if self.tool_executor is not None and self.sdk_runner.supports_tool_namespaces() else ()
        )
        if self.tool_executor is None:
            business_tool_names: tuple[str, ...] = ()
        else:
            business_tool_names = tuple(
                tool_name for tool_name in self.tool_registry.names_for_sdk() if tool_name != LOAD_SERVICE_SKILL_TOOL_NAME
            )
        tool_names = (LOAD_SERVICE_SKILL_TOOL_NAME, *business_tool_names)
        return _AgentTurnToolCatalog(tool_namespaces=tool_namespaces, tool_names=tool_names)

    def _prepare_model_turn(
        self,
        *,
        turn_context: _AgentTurnContext,
    ) -> _PreparedModelTurn:
        model_visible_state = _model_visible_state_projection(
            resident_loaded_service_skill=turn_context.resident_loaded_service_skill,
            expired_loaded_service_skills=turn_context.expired_loaded_service_skills,
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
            current_user_message=_to_model_message(
                turn_context.current_message,
                include_image_attachments=True,
            ),
        )
        return _PreparedModelTurn(projection=projection, model_input=model_input)

    async def _run_model_turn(
        self,
        *,
        run: AgentRun,
        turn_context: _AgentTurnContext,
        tool_catalog: _AgentTurnToolCatalog,
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
                tool_names=tool_catalog.tool_names,
                tool_namespaces=_sdk_tool_namespaces(tool_catalog.tool_namespaces),
                tool_search_enabled=_tool_search_enabled(tool_catalog.tool_namespaces),
                tools=self._sdk_tools(run=run, tool_names=tool_catalog.tool_names, tool_namespaces=tool_catalog.tool_namespaces),
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
        raw_provider_final = str(result.final_text or "")
        provider_projector = AppendOnlyAgentResponseProjector()
        provider_projector.push(raw_provider_final)
        provider_projector.finalize()
        provider_final_text = provider_projector.text
        if not provider_final_text and raw_provider_final.strip():
            provider_final_text = "我已经整理好了。"
        await self._finalize_text_projector(run=run)
        final_text = await self._canonical_final_text(run=run, provider_final_text=provider_final_text)
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
            stream_segment_count=self._run_text_segment_counts.get(run.id, 0),
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
            projector = self._run_text_projectors[run.id]
            sanitized_delta = projector.push(delta)
            if not sanitized_delta:
                return
            self._run_text_stream_emitted[run.id] = projector.text
            await self._publish_text_delta(
                run=run,
                delta=sanitized_delta,
                event_publisher=event_publisher,
                transient_stream=transient_stream,
            )

        return publish

    async def _finalize_text_projector(self, *, run: AgentRun) -> None:
        projector = self._run_text_projectors[run.id]
        final_delta = projector.finalize()
        if not final_delta:
            return
        self._run_text_stream_emitted[run.id] = projector.text
        await self._publish_text_delta(
            run=run,
            delta=final_delta,
            event_publisher=self.event_sink,
            transient_stream=self.transient_stream,
        )

    async def _canonical_final_text(self, *, run: AgentRun, provider_final_text: str) -> str:
        streamed_text = self._run_text_stream_emitted.get(run.id, "")
        if not streamed_text:
            return provider_final_text
        if not provider_final_text or streamed_text.endswith(provider_final_text):
            return streamed_text
        if provider_final_text.startswith(streamed_text):
            missing_suffix = provider_final_text[len(streamed_text) :]
            self._run_text_stream_emitted[run.id] = provider_final_text
            await self._publish_text_delta(
                run=run,
                delta=missing_suffix,
                event_publisher=self.event_sink,
                transient_stream=self.transient_stream,
            )
            return provider_final_text
        log_agent_runtime_event(
            "agent.run.stream_final_mismatch",
            run_id=str(run.id),
            thread_id=str(run.thread_id),
            trace_id=run.trace_id,
            streamed_text_length=len(streamed_text),
            provider_final_text_length=len(provider_final_text),
        )
        return streamed_text

    async def _publish_text_delta(
        self,
        *,
        run: AgentRun,
        delta: str,
        event_publisher: AgentEventSink | None,
        transient_stream: AgentTransientStream | None,
    ) -> None:
        if not delta:
            return
        message_stream_id = str(self._run_assistant_message_ids.get(run.id) or "assistant")
        segment_index = self._run_text_segment_counts.get(run.id, 0)
        prefix_integrity = agent_response_text_integrity(self._run_text_stream_emitted.get(run.id, ""))
        if event_publisher is not None:
            await event_publisher.publish_message_delta(
                thread_id=run.thread_id,
                run_id=run.id,
                delta=delta,
                message_stream_id=message_stream_id,
                segment_index=segment_index,
                prefix_utf8_bytes=prefix_integrity.utf8_bytes,
                prefix_sha256=prefix_integrity.sha256,
            )
        elif transient_stream is not None:
            await transient_stream.publish_message_delta(
                thread_id=run.thread_id,
                run_id=run.id,
                delta=delta,
                message_stream_id=message_stream_id,
                segment_index=segment_index,
                prefix_utf8_bytes=prefix_integrity.utf8_bytes,
                prefix_sha256=prefix_integrity.sha256,
            )
        self._run_text_segment_counts[run.id] = segment_index + 1

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
        if self._unified_load_service_skill:
            load_service_skill = self._sdk_tool_definition(
                run=run,
                tool_name=LOAD_SERVICE_SKILL_TOOL_NAME,
            )
        else:
            load_service_skill = self._load_service_skill_tool_definition(run=run)
        return (load_service_skill, *business_tools)

    def _load_service_skill_tool_definition(self, *, run: AgentRun) -> SdkToolDefinition:
        contract = self.tool_registry.get(LOAD_SERVICE_SKILL_TOOL_NAME)

        async def invoke(args_json: str) -> SdkToolInvocationResult:
            args = _json_object(args_json)
            output = await self._invoke_load_service_skill_tool(run=run, args=args)
            await self._append_progress(run=run, phase="model_reasoning_after_tool", label="我接着处理下一步")
            skill = self.service_skill_registry.get(_required_text(output, "service_skill_id"))
            return SdkToolInvocationResult(
                output_json=json.dumps(output, ensure_ascii=False, sort_keys=True),
                model_context=self._service_skill_model_context(skill=skill, output=output),
            )

        return SdkToolDefinition(
            contract_name=contract.name,
            sdk_name=sdk_tool_name(contract.name),
            description=contract.description,
            params_json_schema=contract.input_schema,
            invoke=invoke,
        )

    def _sdk_tool_definition(
        self,
        *,
        run: AgentRun,
        tool_name: str,
        namespace: ToolNamespace | None = None,
    ) -> SdkToolDefinition:
        contract = self.tool_registry.get(tool_name)
        sdk_name = sdk_tool_name(contract.name)

        async def invoke(args_json: str) -> SdkToolInvocationResult:
            return await self._invoke_sdk_tool(run=run, contract_name=contract.name, sdk_name=sdk_name, args_json=args_json)

        return SdkToolDefinition(
            contract_name=contract.name,
            sdk_name=sdk_name,
            description=contract.description,
            params_json_schema=contract.input_schema,
            invoke=invoke,
            namespace_name=namespace.name if namespace is not None else "",
            defer_loading=namespace is not None and contract.loading_mode == "deferred",
        )

    async def _load_service_skill_handler(self, context: ToolHandlerContext) -> ToolHandlerResult:
        raw_skill_id = _text(context.args, "service_skill_id")
        try:
            skill_id = ServiceSkillId(raw_skill_id)
        except ValueError as exc:
            raise ApiError(code="invalid_service_skill", message="Unsupported service_skill_id.", status=422) from exc
        skill = self.service_skill_registry.get(skill_id.value)
        facts = (
            await self.business_facts_projector.project(
                actor=context.actor,
                run_id=context.run_id,
                service_skill_id=skill_id,
            )
            if self.business_facts_projector is not None
            else {}
        )
        self._run_business_facts.setdefault(context.run_id, {})[skill_id] = facts
        output = _load_service_skill_output(
            skill=skill,
            recommended_tools=_recommended_tools_for_service_skill(
                tool_registry=self.tool_registry,
                tool_namespace_registry=self.tool_namespace_registry,
                skill_id=skill_id,
            ),
            business_facts=facts,
            loaded_at=self.clock(),
        )
        self._run_loaded_service_skill_ids.setdefault(context.run_id, set()).add(skill.service_skill_id)
        output["_deferred_agent_events"] = [
            {
                "event_type": "skill.loaded",
                "payload": {
                    "service_skill_id": skill.service_skill_id,
                    "skill_version": skill.version,
                    "loaded_at": output["loaded_at"],
                    "recommended_tool_contracts": list(_recommended_tool_contracts(skill_id)),
                },
            }
        ]
        return ToolHandlerResult(
            output=output,
            model_context=self._service_skill_model_context(skill=skill, output=output),
        )

    def _service_skill_model_context(
        self,
        *,
        skill: AgentServiceSkill,
        output: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        trusted_context = {
            "runtime_loaded_service_skill": {
                "service_skill_id": skill.service_skill_id,
                "skill_version": skill.version,
                "loaded_at": _text(output, "loaded_at"),
                "name": skill.name,
                "description": skill.description,
                "instructions": skill.prompt_block(),
                "recommended_tools": _list_of_dicts(output, "recommended_tools"),
                "business_facts": _dict(output, "business_facts"),
                "instruction": "Apply this service skill only while it is relevant to the current user request.",
            }
        }
        return (
            {
                "role": "developer",
                "content": json.dumps(trusted_context, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            },
        )

    async def _invoke_sdk_tool(
        self,
        *,
        run: AgentRun,
        contract_name: str,
        sdk_name: str,
        args_json: str,
    ) -> SdkToolInvocationResult:
        if self.tool_executor is None:
            raise ApiError(code="unsupported_operation", message="Tool executor is not configured.", status=501)
        args = _json_object(args_json)
        execute_kwargs = {
            "actor": _run_actor(run),
            "run_id": run.id,
            "tool_name": contract_name,
            "call_id": f"sdk-{sdk_name}-{uuid4().hex}",
            "args": args,
        }
        trusted_args = await self._trusted_tool_args(run=run, contract_name=contract_name)
        if trusted_args:
            execute_kwargs["trusted_args"] = trusted_args
        if contract_name == LOAD_SERVICE_SKILL_TOOL_NAME and isinstance(self.tool_executor, ToolExecutor):
            result = await self.tool_executor.execute(
                **execute_kwargs,
                handler_override=self._load_service_skill_handler,
            )
        else:
            result = await self.tool_executor.execute(**execute_kwargs)
        await self._append_progress(run=run, phase="model_reasoning_after_tool", label="我接着处理下一步")
        model_output = getattr(result, "model_output", result.safe_output)
        return SdkToolInvocationResult(
            output_json=json.dumps(model_output, sort_keys=True),
            safe_output_json=json.dumps(result.safe_output, sort_keys=True),
            model_context=result.model_context,
        )

    async def _trusted_tool_args(self, *, run: AgentRun, contract_name: str) -> dict[str, Any]:
        expected_form_id = FORM_TOOL_IDS.get(contract_name)
        if expected_form_id is not None:
            submission = self._run_trusted_form_submissions.get(run.id, {}).get(expected_form_id)
            if submission is None:
                return {}
            return {
                "confirmed_form_data": _dict(submission, "values"),
                "form_submission_id": _text(submission, "submission_id"),
            }
        if contract_name == "pregnancy.plan.propose":
            facts = await self._birth_prep_business_facts(run=run)
            return {"runtime_plan_context": _pregnancy_runtime_plan_context(facts)}
        if contract_name in FORM_CREATION_TOOL_NAMES:
            facts = await self._birth_prep_business_facts(run=run)
            default_values = _birth_prep_form_default_values(facts)
            return {"default_values": default_values} if default_values else {}
        if contract_name == "hospital_bag_cart_update":
            client_groups = self._run_hospital_bag_cart_groups.get(run.id)
            if client_groups is not None:
                return {"groups": client_groups}
            groups = await self._latest_hospital_bag_cart_groups(run=run)
            return {"groups": groups} if groups is not None else {}
        if contract_name == IMAGE_INSPECT_TOOL_NAME:
            return {"visible_image_urls": list(self._run_visible_image_urls.get(run.id, ()))}
        return {}

    async def _birth_prep_business_facts(self, *, run: AgentRun) -> dict[str, Any]:
        cached = self._run_business_facts.setdefault(run.id, {}).get(ServiceSkillId.BIRTH_PREP)
        if cached is not None:
            return cached
        facts = await self._fresh_business_facts_for_skill(run=run, skill_id=ServiceSkillId.BIRTH_PREP)
        self._run_business_facts[run.id][ServiceSkillId.BIRTH_PREP] = facts
        return facts

    async def _latest_hospital_bag_cart_groups(self, *, run: AgentRun) -> list[dict[str, Any]] | None:
        loader = getattr(self.repository, "get_latest_artifact_for_thread", None)
        if not callable(loader):
            return None
        artifact = await loader(
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            artifact_type="hospital_bag_cart",
        )
        if artifact is None or not isinstance(artifact.payload, dict):
            return None
        cart_update = artifact.payload.get("cart_update")
        groups = cart_update.get("groups") if isinstance(cart_update, dict) else None
        return [dict(group) for group in groups if isinstance(group, dict)] if isinstance(groups, list) else None

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
            self._run_business_facts.setdefault(run.id, {})[skill_id] = facts
            output = _load_service_skill_output(
                skill=skill,
                recommended_tools=_recommended_tools_for_service_skill(
                    tool_registry=self.tool_registry,
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
                    "recommended_tool_contracts": list(_recommended_tool_contracts(skill_id)),
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
            tool_scope_version=_tool_catalog_version_for_ledger(
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
        return await self.memory_service.get_runtime_snapshot(
            owner_user_id=run.actor_user_id,
            limit=self.config.memory_limit,
        )

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
        loaded_service_skills = _loaded_service_skill_summaries(
            tool_outputs=tool_outputs, result_tool_calls=getattr(result, "tool_calls", [])
        )
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
    return [message for message in messages if message.sequence < before_sequence and message.role in {"user", "assistant"}]


def _visible_assistant_image_urls(*, messages: list[AgentMessage], before_sequence: int) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if message.sequence >= before_sequence or message.role != "assistant":
            continue
        for match in MARKDOWN_IMAGE_URL_PATTERN.finditer(_message_text(message)):
            image_url = str(match.group(1) or match.group(2) or "").strip()
            if image_url and image_url not in seen:
                seen.add(image_url)
                urls.append(image_url)
    return tuple(urls)


def _routing_target_agent_id(routing_plan: RoutingPlan) -> str:
    return routing_plan.selected_agent_id.value


def _load_service_skill_output(
    *,
    skill: AgentServiceSkill,
    recommended_tools: list[dict[str, str]],
    business_facts: dict[str, Any],
    loaded_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": "service_skill_load.v2",
        "service_skill_id": skill.service_skill_id,
        "skill_version": skill.version,
        "loaded_at": _aware_datetime(loaded_at).astimezone(timezone.utc).isoformat(),
        "skill": {
            "service_skill_id": skill.service_skill_id,
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.prompt_block(),
        },
        "recommended_tools": recommended_tools,
        "business_facts": business_facts,
    }


def _model_visible_state_projection(
    *,
    resident_loaded_service_skill: dict[str, Any] | None,
    expired_loaded_service_skills: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "service_skills": {
            "resident": _model_visible_resident_service_skill(resident_loaded_service_skill),
            "expired": [_model_visible_expired_service_skill(item) for item in expired_loaded_service_skills],
        },
    }


def _model_visible_resident_service_skill(resident: dict[str, Any] | None) -> dict[str, Any] | None:
    if not resident:
        return None
    skill = _dict(resident, "skill")
    return {
        "service_skill_id": _text(resident, "service_skill_id"),
        "skill_version": _text(resident, "skill_version"),
        "instructions": _text(skill, "instructions"),
        "recommended_tools": _list_of_dicts(resident, "recommended_tools"),
    }


def _model_visible_expired_service_skill(expired: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_skill_id": _text(expired, "service_skill_id"),
        "instruction": _text(expired, "instruction"),
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
        payload.update(
            {key: value for key, value in artifact.payload.items() if key in {"form", "card", "card_json", "cart_update", "summary"}}
        )
    payload["semantic"] = artifact_event_payload_semantic(artifact_type=artifact.artifact_type)
    return payload


def _tool_catalog_version_for_ledger(*, tool_executor_configured: bool) -> str:
    if not tool_executor_configured:
        return "agent_load_service_skill:v2"
    return "global_model_tool_registry:v2"


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


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)


def _timings_with_total(timings_ms: dict[str, float], run_started_at: float) -> dict[str, float]:
    return {**timings_ms, "total_before_finish_checkpoint": _elapsed_ms(run_started_at)}


SERVICE_SKILL_RECOMMENDED_TOOL_CONTRACTS: dict[ServiceSkillId, tuple[str, ...]] = {
    ServiceSkillId.BIRTH_PREP: (
        "pregnancy.plan.propose",
        "plans.plan_delete.propose",
        "plans.task_complete.propose",
        "plans.task_update.propose",
        "plans.task_delete.propose",
        "birth_plan_form_create",
        "labor_communication_card_create",
        "hospital_bag_form_create",
        "hospital_bag_card_create",
        "hospital_bag_cart_update",
        "hospital_bag_pump_recommend",
    ),
    ServiceSkillId.MILK_MANAGEMENT: (
        "records.milk_status.read",
        "records.milk_summary.read",
        "records.milk_analysis.read",
        "records.growth.read",
        "records.feeding_record.propose",
        "records.feeding_record_delete.propose",
        "records.pumping_record.propose",
        "records.pumping_record_delete.propose",
        "records.growth_record.propose",
        "records.growth_record_update.propose",
        "records.growth_record_delete.propose",
        "plans.current.read",
        "plans.calendar.read",
        "plans.milk_plan.propose",
        "plans.task_complete.propose",
        "plans.task_create.propose",
        "notifications.milk_reminder.propose",
    ),
    ServiceSkillId.HEALTH_CONSULTATION: (
        "records.milk_status.read",
        "ibclc_consult_card_create",
    ),
    ServiceSkillId.EMOTION_SUPPORT: (),
    ServiceSkillId.DEVICE_GUIDANCE: (
        "devices.pump_status.read",
        "devices.guidance_assets.read",
        "support.ticket.propose",
    ),
}


def _recommended_tool_contracts(skill_id: ServiceSkillId) -> tuple[str, ...]:
    return SERVICE_SKILL_RECOMMENDED_TOOL_CONTRACTS.get(skill_id, ())


def _recommended_tools_for_service_skill(
    *,
    tool_registry: ToolContractRegistry,
    tool_namespace_registry: ToolNamespaceRegistry,
    skill_id: ServiceSkillId,
) -> list[dict[str, str]]:
    namespace_by_tool = _namespace_by_tool(tool_namespace_registry.list())
    recommendations: list[dict[str, str]] = []
    for contract_name in _recommended_tool_contracts(skill_id):
        contract = tool_registry.get(contract_name)
        namespace = namespace_by_tool.get(contract_name)
        recommendations.append(
            {
                "namespace": namespace.name if namespace is not None else "",
                "name": sdk_tool_name(contract.name),
            }
        )
    return recommendations


def _sdk_instructions(*, projection: ContextProjection) -> str:
    return projection.stable_system_prompt


def _to_model_message(
    message: AgentMessage,
    *,
    include_image_attachments: bool = False,
) -> dict[str, Any]:
    role = message.role if message.role in {"user", "assistant"} else "user"
    text = _message_text(message)
    if role != "user" or not include_image_attachments:
        return {"role": role, "content": text}

    image_inputs = _current_message_image_inputs(message)
    if not image_inputs:
        return {"role": role, "content": text}
    return {
        "role": role,
        "content": [
            {"type": "input_text", "text": text},
            *image_inputs,
        ],
    }


def _current_message_image_inputs(message: AgentMessage) -> list[dict[str, str]]:
    content = message.content if isinstance(message.content, dict) else {}
    attachments = content.get("attachments")
    if not isinstance(attachments, list):
        return []

    image_inputs: list[dict[str, str]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("type") != "image":
            continue
        image_url = _text(attachment, "data_url")
        if not MODEL_IMAGE_DATA_URL_PATTERN.match(image_url):
            continue
        detail = _text(attachment, "detail")
        image_inputs.append(
            {
                "type": "input_image",
                "image_url": image_url,
                "detail": detail if detail in {"auto", "low", "high"} else "auto",
            }
        )
    return image_inputs


RECENT_RUN_FACT_KEYS = ("user_goal", "assistant_conclusion", "tool_facts", "actions", "artifacts")


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
    user_goal_visible = _summary_source_is_visible_in_history(
        summary=summary,
        selected_history_message_ids=selected_history_message_ids,
    )
    payload = _recent_fact_payload(
        payload,
        user_goal_visible=user_goal_visible,
        assistant_conclusion_visible=str(summary.run_id) in selected_history_assistant_run_ids,
    )
    if not payload:
        return None
    return {
        "service_skill_id": summary.service_skill_id,
        "created_at": _iso_or_empty(getattr(summary, "created_at", None)),
        "facts": _compact_mapping(payload, max_items=8, max_chars=2400),
    }


def _summary_source_is_visible_in_history(*, summary: Any, selected_history_message_ids: set[str]) -> bool:
    source_message_ids = getattr(summary, "source_message_ids", None)
    if not isinstance(source_message_ids, (list, tuple, set)):
        return False
    return any(str(message_id) in selected_history_message_ids for message_id in source_message_ids)


def _recent_fact_payload(
    payload: dict[str, Any],
    *,
    user_goal_visible: bool,
    assistant_conclusion_visible: bool,
) -> dict[str, Any]:
    compact_payload: dict[str, Any] = {}
    for key in RECENT_RUN_FACT_KEYS:
        if key == "user_goal" and user_goal_visible:
            continue
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
    tool_registry: ToolContractRegistry,
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
    recommended_tools = _recommended_tools_for_service_skill(
        tool_registry=tool_registry,
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
            "recommended_tools": recommended_tools,
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
    return {
        "service_skill_id": service_skill_id,
        "skill_version": _text(output, "skill_version"),
        "loaded_at": _text(output, "loaded_at"),
    }


def _resident_loaded_service_skill_summary(resident_loaded_service_skill: dict[str, Any] | None) -> dict[str, Any]:
    if not resident_loaded_service_skill:
        return {}
    return {
        "service_skill_id": _text(resident_loaded_service_skill, "service_skill_id"),
        "skill_version": _text(resident_loaded_service_skill, "skill_version"),
        "last_loaded_run_id": _text(resident_loaded_service_skill, "last_loaded_run_id"),
        "loaded_at": _text(resident_loaded_service_skill, "loaded_at"),
        "turns_since_loaded": int(resident_loaded_service_skill.get("turns_since_loaded") or 0),
        "remaining_turns": int(resident_loaded_service_skill.get("remaining_turns") or 0),
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
    client_context = project_agent_client_context(content.get("client_context"))
    location = _location_context(content.get("location"))
    timezone_name = _text(client_context, "timezone") or _text(content, "timezone") or _text(location, "timezone") or "UTC"
    timezone_info, normalized_timezone = _timezone_info(timezone_name)
    current_time = _aware_datetime(now).astimezone(timezone_info).isoformat()
    user_context: dict[str, Any] = {
        "current_time": current_time,
        "timezone": normalized_timezone,
        "locale": _text(client_context, "locale") or _text(content, "locale") or "zh-CN",
    }
    for key in ("message_sent_at", "hospital_bag_cart"):
        if key in client_context:
            user_context[key] = client_context[key]
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


def _trusted_form_submissions(message: AgentMessage) -> dict[str, dict[str, Any]]:
    content = message.content if isinstance(message.content, dict) else {}
    attachments = content.get("attachments")
    if not isinstance(attachments, list):
        return {}
    submissions: dict[str, dict[str, Any]] = {}
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("type") != "form_submission" or attachment.get("verified") is not True:
            continue
        form_id = _text(attachment, "form_id")
        submission_id = _text(attachment, "submission_id")
        values = attachment.get("values")
        if not form_id or not submission_id or not isinstance(values, dict):
            continue
        submissions[form_id] = {
            "submission_id": submission_id,
            "artifact_id": _text(attachment, "artifact_id"),
            "values": dict(values),
        }
    return submissions


def _current_hospital_bag_cart_groups(message: AgentMessage) -> list[dict[str, Any]] | None:
    content = message.content if isinstance(message.content, dict) else {}
    client_context = project_agent_client_context(content.get("client_context"))
    cart = client_context.get("hospital_bag_cart")
    groups = cart.get("groups") if isinstance(cart, dict) else None
    return [dict(group) for group in groups if isinstance(group, dict)] if isinstance(groups, list) else None


def _birth_prep_form_default_values(facts: dict[str, Any]) -> dict[str, Any]:
    pregnancy = _dict(facts, "pregnancy")
    profile = _dict(pregnancy, "profile") or _dict(facts, "profile")
    due_date_or_week = next(
        (
            value
            for value in (
                _text(pregnancy, "due_date_or_week"),
                _text(pregnancy, "current_week"),
                _text(profile, "delivery_date"),
            )
            if value
        ),
        "",
    )
    return {"due_date_or_week": due_date_or_week} if due_date_or_week else {}


def _pregnancy_runtime_plan_context(facts: dict[str, Any]) -> dict[str, Any]:
    pregnancy = _dict(facts, "pregnancy")
    profile = _dict(pregnancy, "profile") or _dict(facts, "profile")
    plans = pregnancy.get("plans")
    active_plans = (
        [item for item in plans if isinstance(item, dict) and _text(item, "status") == "active" and _text(item, "plan_type") == "pregnancy"]
        if isinstance(plans, list)
        else []
    )
    context: dict[str, Any] = {
        "has_active_plan": bool(active_plans),
        "delivery_date": _text(profile, "delivery_date"),
    }
    if active_plans:
        context["active_plan_id"] = _text(active_plans[0], "id")
        context["active_plan_title"] = _text(active_plans[0], "title")
    return {key: value for key, value in context.items() if value not in ("", None)}


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


def _list_of_dicts(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


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
