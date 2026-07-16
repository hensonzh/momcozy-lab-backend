from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit
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
from ..agents.cozymate_service_agent.health_guidance import (
    HEALTH_GUIDANCE_ALLOWED_DOMAINS,
)
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
from ..agents.cozymate_service_agent.tools.executor import project_load_service_skill_model_output
from ..agents.cozymate_service_agent.tools.hospital_bag_flow import (
    HOSPITAL_BAG_WORKFLOW_TYPE,
    ensure_hospital_bag_completion_followup,
)
from ..agents.cozymate_service_agent.tools.pregnancy_plan_flow import (
    PREGNANCY_PLAN_WORKFLOW_TYPE,
    ensure_pregnancy_plan_final_question,
)
from ..event_stream.sink import AgentEventSink
from ..event_stream.transient import AgentTransientStream
from ..event_semantics import (
    action_event_payload_semantic,
    artifact_event_payload_semantic,
    progress_live_dedupe_key,
    run_progress_payload,
    web_search_event_semantic,
    with_tool_event_semantic,
)
from ..runtime_registry import AgentRuntimeRegistry, default_runtime_registry
from ..facts import AgentFactService
from ..memory.service import AgentMemoryService
from ..models import AgentAction, AgentArtifact, AgentEvent, AgentMessage, AgentRun, AgentWorkflowState
from ..payloads import DEFAULT_MAX_INLINE_PAYLOAD_BYTES, maybe_externalize_json_payload
from ..repository import AgentRuntimeRepository
from ..response_text import AppendOnlyAgentResponseProjector, agent_response_text_integrity
from ..workflow_reply import (
    build_workflow_reply_context,
    guarded_workflow_type,
    normalize_workflow_reply_context,
    validate_workflow_reply_context,
    workflow_accepts_reply,
)
from ..sdk import (
    AgentModelRunner,
    SdkNodeRequest,
    SdkNodeResult,
    SdkToolDefinition,
    SdkToolInvocationResult,
    SdkToolNamespace,
    sdk_tool_name,
)
from .execution import AgentRunExecutionResult
from .ongoing_work import project_ongoing_work, project_workflow_context
from .quick_replies import QuickReplyFinalizer
from .state_store import AgentRuntimeStateStore
from .working_context import (
    AgentWorkingContextState,
    AgentWorkingContextStore,
    empty_working_context_state,
    project_working_context,
)


LOAD_SERVICE_SKILL_TOOL_NAME = "load_service_skill"
CONVERSATION_HISTORY_IMAGE_LOAD_TOOL_NAME = "conversation_history.image.load"
COZYMATE_AGENT_ID = "cozymate_service_agent"
LOGGER = logging.getLogger("production_backend.agent_runtime.executor")
DEFAULT_RESIDENT_SERVICE_SKILL_TTL_TURNS = 3
FORM_TOOL_IDS = {
    "pregnancy.plan_intake.analyze": "birth_journey_basic_info_intake",
    "hospital_bag_card_create": "hospital_bag_intake",
    "labor_communication_card_create": "birth_plan_card_intake",
}
FORM_CREATION_TOOL_NAMES = {"pregnancy.plan_intake.start", "birth_plan_form_create", "hospital_bag_form_create"}
FORM_CREATION_IDS = {
    "pregnancy.plan_intake.start": "birth_journey_basic_info_intake",
    "hospital_bag_form_create": "hospital_bag_intake",
    "birth_plan_form_create": "birth_plan_card_intake",
}
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
    resident_service_skill_ttl_turns: int = DEFAULT_RESIDENT_SERVICE_SKILL_TTL_TURNS
    known_information_token_budget: int = 4000


@dataclass
class _AgentTurnContext:
    current_message: AgentMessage
    messages: list[AgentMessage]
    memory_projection: list[dict[str, Any]]
    service_skills: tuple[AgentServiceSkill, ...]
    working_context_state: AgentWorkingContextState
    workflow_states: list[AgentWorkflowState]
    ongoing_work: list[dict[str, str]]
    workflow_context: list[dict[str, Any]]
    trusted_form_submissions: dict[str, dict[str, Any]]
    checkup_attachment_count: int
    workflow_reply: dict[str, Any]
    recent_client_events: list[dict[str, str]]
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
        runtime_registry: AgentRuntimeRegistry | None = None,
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
        working_context_store: AgentWorkingContextStore | None = None,
        fact_service: AgentFactService | None = None,
        input_builder: ModelInputBuilder | None = None,
        config: AgentRuntimeExecutorConfig | None = None,
        object_storage: ObjectStorage | None = None,
        max_inline_artifact_payload_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.sdk_runner = sdk_runner
        self.runtime_registry = runtime_registry or default_runtime_registry()
        self.state_store = AgentRuntimeStateStore(repository=repository)
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
        self.working_context_store = working_context_store
        self.fact_service = fact_service
        self.input_builder = input_builder or ModelInputBuilder()
        self.config = config or AgentRuntimeExecutorConfig()
        self.object_storage = object_storage
        self.max_inline_artifact_payload_bytes = max_inline_artifact_payload_bytes
        self.clock = clock or _utcnow
        self._run_assistant_message_ids: dict[UUID, UUID] = {}
        self._run_text_projectors: dict[UUID, AppendOnlyAgentResponseProjector] = {}
        self._run_text_stream_emitted: dict[UUID, str] = {}
        self._run_authoritative_final_text: dict[UUID, str] = {}
        self._run_current_user_text: dict[UUID, str] = {}
        self._run_local_dates: dict[UUID, str] = {}
        self._run_timezones: dict[UUID, str] = {}
        self._run_previous_assistant_text: dict[UUID, str] = {}
        self._run_trusted_form_submissions: dict[UUID, dict[str, dict[str, Any]]] = {}
        self._run_checkup_attachment_counts: dict[UUID, int] = {}
        self._run_business_facts: dict[UUID, dict[ServiceSkillId, dict[str, Any]]] = {}
        self._run_visible_image_urls: dict[UUID, tuple[str, ...]] = {}
        self._run_hospital_bag_cart_groups: dict[UUID, list[dict[str, Any]] | None] = {}
        self._run_text_segment_counts: dict[UUID, int] = {}
        self._run_provider_text_delta_seen: dict[UUID, bool] = {}
        self._run_workflow_replies: dict[UUID, dict[str, Any]] = {}
        self._run_guarded_workflow_types: dict[UUID, list[str]] = {}
        self._run_workflow_reply_recovery_types: dict[UUID, str] = {}
        self._unified_load_service_skill = isinstance(self.tool_executor, ToolExecutor)

    async def __call__(self, run: AgentRun) -> AgentRunExecutionResult:
        return await self.execute(run=run)

    async def execute(self, *, run: AgentRun) -> AgentRunExecutionResult:
        run_started_at = perf_counter()
        runtime = self.runtime_registry.get(run.runtime_version)
        if runtime.runtime_pattern != run.runtime_pattern:
            raise ApiError(
                code="runtime_pattern_mismatch",
                message="Run runtime pattern does not match its runtime version.",
                status=409,
            )
        self._run_assistant_message_ids[run.id] = uuid4()
        self._run_text_projectors[run.id] = AppendOnlyAgentResponseProjector()
        self._run_text_stream_emitted[run.id] = ""
        self._run_visible_image_urls[run.id] = ()
        self._run_business_facts[run.id] = {}
        self._run_text_segment_counts[run.id] = 0
        self._run_provider_text_delta_seen[run.id] = False
        try:
            turn_context = await self._load_turn_context(run=run)
            self._run_trusted_form_submissions[run.id] = turn_context.trusted_form_submissions
            self._run_checkup_attachment_counts[run.id] = turn_context.checkup_attachment_count
            self._run_current_user_text[run.id] = _message_text(turn_context.current_message)
            self._run_workflow_replies[run.id] = turn_context.workflow_reply
            self._run_guarded_workflow_types[run.id] = []
            self._run_workflow_reply_recovery_types[run.id] = ""
            self._run_previous_assistant_text[run.id] = _latest_assistant_text_before(
                messages=turn_context.messages,
                before_sequence=turn_context.current_message.sequence,
            )
            self._run_hospital_bag_cart_groups[run.id] = _current_hospital_bag_cart_groups(turn_context.current_message)
            tool_catalog = self._tool_catalog_for_turn()
            await self._append_progress(run=run, phase="context_ready", label="我先理解一下你的需求～")
            prepared_turn = self._prepare_model_turn(turn_context=turn_context)
            self._run_local_dates[run.id] = _user_context_local_date(prepared_turn.projection.user_context)
            self._run_timezones[run.id] = _text(prepared_turn.projection.user_context, "timezone") or "UTC"
            result = await self._run_model_turn(
                run=run,
                turn_context=turn_context,
                tool_catalog=tool_catalog,
                prepared_turn=prepared_turn,
                web_search_enabled=_runner_supports_web_search(self.sdk_runner),
            )
            await self._emit_web_search_events(run=run, result=result)
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
            self._run_text_projectors.pop(run.id, None)
            self._run_text_stream_emitted.pop(run.id, None)
            self._run_authoritative_final_text.pop(run.id, None)
            self._run_current_user_text.pop(run.id, None)
            self._run_local_dates.pop(run.id, None)
            self._run_timezones.pop(run.id, None)
            self._run_previous_assistant_text.pop(run.id, None)
            self._run_trusted_form_submissions.pop(run.id, None)
            self._run_checkup_attachment_counts.pop(run.id, None)
            self._run_business_facts.pop(run.id, None)
            self._run_visible_image_urls.pop(run.id, None)
            self._run_hospital_bag_cart_groups.pop(run.id, None)
            self._run_text_segment_counts.pop(run.id, None)
            self._run_provider_text_delta_seen.pop(run.id, None)
            self._run_workflow_replies.pop(run.id, None)
            self._run_guarded_workflow_types.pop(run.id, None)
            self._run_workflow_reply_recovery_types.pop(run.id, None)

    async def _load_turn_context(self, *, run: AgentRun) -> _AgentTurnContext:
        timings_ms: dict[str, float] = {}
        await self._append_progress(run=run, phase="context_loading", label="我已经收到你的消息啦～")
        context_started_at = perf_counter()
        messages = await self.repository.list_messages_for_thread(thread_id=run.thread_id, limit=self.config.history_limit)
        current_message = next(
            (message for message in reversed(messages) if message.run_id == run.id and message.role == "user"),
            None,
        )
        if current_message is None:
            raise ApiError(code="missing_user_message", message="Agent run has no user message.", status=409)
        self._run_visible_image_urls[run.id] = _visible_assistant_image_urls(
            messages=messages,
            before_sequence=current_message.sequence,
        )
        memory_projection = await self._memory_projection(run=run)
        timings_ms["context_base"] = _elapsed_ms(context_started_at)

        service_skills = self.service_skill_registry.list()

        working_context_started_at = perf_counter()
        working_context_state = await self._begin_working_context_turn(run=run)
        timings_ms["working_context"] = _elapsed_ms(working_context_started_at)

        ongoing_work_started_at = perf_counter()
        workflow_states = await self._active_workflow_states(run=run)
        trusted_form_submissions = _trusted_form_submissions(current_message)
        checkup_attachment_count = _runtime_checkup_attachment_count(current_message)
        workflow_reply = _workflow_reply_for_turn(current_message=current_message, messages=messages)
        resident_skill_ids = {
            skill.service_skill_id for skill in working_context_state.skills if working_context_state.turn_index <= skill.expires_after_turn
        }
        ongoing_work = project_ongoing_work(workflow_states, resident_skill_ids=resident_skill_ids)
        workflow_context = project_workflow_context(
            workflow_states,
            trusted_form_submissions=trusted_form_submissions,
            checkup_attachment_count=checkup_attachment_count,
            workflow_reply=workflow_reply,
        )
        client_events = await self.repository.list_client_events_for_thread(
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            limit=10,
        )
        timings_ms["ongoing_work"] = _elapsed_ms(ongoing_work_started_at)

        return _AgentTurnContext(
            current_message=current_message,
            messages=messages,
            memory_projection=memory_projection,
            service_skills=service_skills,
            working_context_state=working_context_state,
            workflow_states=workflow_states,
            ongoing_work=ongoing_work,
            workflow_context=workflow_context,
            trusted_form_submissions=trusted_form_submissions,
            checkup_attachment_count=checkup_attachment_count,
            workflow_reply=workflow_reply,
            recent_client_events=_recent_ibclc_client_event_context(client_events),
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
        working_context = project_working_context(
            turn_context.working_context_state,
            ongoing_work=turn_context.ongoing_work,
        )
        if turn_context.recent_client_events:
            working_context["client_events"] = turn_context.recent_client_events
        projection = ContextProjection(
            stable_system_prompt=self.config.stable_system_prompt,
            selected_conversation_history=_history_before(
                messages=turn_context.messages,
                before_sequence=turn_context.current_message.sequence,
            ),
            user_context=_user_context(current_message=turn_context.current_message, now=self.clock()),
            memory_projection=turn_context.memory_projection,
            workflow_context=turn_context.workflow_context,
            working_context=working_context,
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
        web_search_enabled: bool = False,
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
                service_skill_id=COZYMATE_AGENT_ID,
                on_text_delta=self._text_delta_handler(run=run),
                web_search_enabled=web_search_enabled,
                web_search_required=False,
                web_search_allowed_domains=HEALTH_GUIDANCE_ALLOWED_DOMAINS if web_search_enabled else (),
            )
        )
        turn_context.timings_ms["model_reasoning"] = _elapsed_ms(model_started_at)
        return result

    async def _emit_web_search_events(
        self,
        *,
        run: AgentRun,
        result: SdkNodeResult,
    ) -> None:
        if not result.web_search_used and not result.web_search_citations:
            return
        await self._append_web_search_status(run=run, status="completed")
        citations = _allowed_health_web_search_citations(result.web_search_citations)
        if not citations:
            return
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="CUSTOM",
            payload={
                "name": "momcozy.web_search.citations",
                "message_id": str(self._run_assistant_message_ids[run.id]),
                "value": {"citations": citations},
            },
        )

    async def _append_web_search_status(self, *, run: AgentRun, status: str) -> None:
        await self._append_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="CUSTOM",
            payload={
                "name": "momcozy.agent.web_search",
                "value": {"status": status},
                "semantic": web_search_event_semantic(status=status),
            },
        )

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
            self._log_executor_timing(run=run, status="waiting_for_confirmation", timings_ms=timings_ms)
            return AgentRunExecutionResult(status="waiting_for_confirmation", pending_action_id=action.id)
        pending_timings_ms = _timings_with_total(turn_context.timings_ms, run_started_at)
        pending_action = await self._pending_confirmation_action_from_tool(run=run)
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
        authoritative_final_text = self._run_authoritative_final_text.get(run.id, "")
        if authoritative_final_text:
            provider_final_text = authoritative_final_text
        elif _has_completed_pregnancy_plan_analysis(result.tool_calls):
            provider_final_text = ensure_pregnancy_plan_final_question(provider_final_text)
        await self._finalize_text_projector(run=run)
        final_text = await self._canonical_final_text(
            run=run,
            provider_final_text=provider_final_text,
            authoritative=bool(authoritative_final_text),
        )
        if not authoritative_final_text and _has_completed_hospital_bag_card(result.tool_calls):
            linked_final_text = ensure_hospital_bag_completion_followup(
                final_text,
                card=_completed_hospital_bag_card(result.tool_calls),
            )
            if linked_final_text != final_text:
                await self._publish_text_delta(
                    run=run,
                    delta=linked_final_text[len(final_text) :],
                    event_publisher=self.event_sink,
                    transient_stream=self.transient_stream,
                )
                self._run_text_stream_emitted[run.id] = linked_final_text
                final_text = linked_final_text
        if not final_text:
            raise ApiError(code="empty_agent_response", message="Agent runtime returned an empty response.", status=502)
        finish_timings_ms = _timings_with_total(turn_context.timings_ms, run_started_at)
        workflow_finalization_started_at = perf_counter()
        workflow_reply, quick_reply_workflow = await self._completed_turn_workflow_finalization(run=run)
        finish_timings_ms["workflow_finalization"] = _elapsed_ms(workflow_finalization_started_at)
        quick_reply_started_at = perf_counter()
        if not authoritative_final_text and self.quick_reply_finalizer is not None:
            await self._append_progress(
                run=run,
                phase="quick_replies_preparing",
                label="我在帮你准备下一轮的快捷输入～",
            )
        quick_replies = (
            []
            if authoritative_final_text
            else await self._generate_quick_replies(
                run=run,
                turn_context=turn_context,
                final_text=final_text,
                artifacts=list(result.artifacts or []),
                tool_calls=list(result.tool_calls or []),
                active_workflow=quick_reply_workflow,
            )
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
            workflow_reply=workflow_reply,
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
        if getattr(event_publisher, "transient_stream", None) is None and transient_stream is None:
            return None

        async def publish(delta: str) -> None:
            if self._run_authoritative_final_text.get(run.id):
                return
            projector = self._run_text_projectors[run.id]
            sanitized_delta = projector.push(delta)
            if not sanitized_delta:
                return
            self._run_provider_text_delta_seen[run.id] = True
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

    async def _canonical_final_text(
        self,
        *,
        run: AgentRun,
        provider_final_text: str,
        authoritative: bool = False,
    ) -> str:
        streamed_text = self._run_text_stream_emitted.get(run.id, "")
        if authoritative:
            if streamed_text.endswith(provider_final_text):
                return streamed_text
            authoritative_delta = provider_final_text if not streamed_text else f"\n\n{provider_final_text}"
            combined_text = f"{streamed_text}{authoritative_delta}"
            self._run_text_stream_emitted[run.id] = combined_text
            await self._publish_text_delta(
                run=run,
                delta=authoritative_delta,
                event_publisher=self.event_sink,
                transient_stream=self.transient_stream,
            )
            return combined_text
        if not streamed_text:
            self._run_text_stream_emitted[run.id] = provider_final_text
            await self._publish_text_delta(
                run=run,
                delta=provider_final_text,
                event_publisher=self.event_sink,
                transient_stream=self.transient_stream,
            )
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
        if event_publisher is not None and getattr(event_publisher, "transient_stream", None) is not None:
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
        else:
            return
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
            await self._append_progress(run=run, phase="model_followup", label="我接着处理下一步")
            await self._append_progress(run=run, phase="model_reasoning_after_tool", label="我想一下")
            skill = self.service_skill_registry.get(_required_text(output, "service_skill_id"))
            return SdkToolInvocationResult(
                output_json=json.dumps(project_load_service_skill_model_output(output), ensure_ascii=False, sort_keys=True),
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
        if context.thread_id is not None:
            await self._retain_service_skill(thread_id=context.thread_id, skill=skill)
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
        trusted_args = await self._trusted_tool_args(run=run, contract_name=contract_name, args=args)
        if trusted_args:
            execute_kwargs["trusted_args"] = trusted_args
        if contract_name == LOAD_SERVICE_SKILL_TOOL_NAME and isinstance(self.tool_executor, ToolExecutor):
            result = await self.tool_executor.execute(
                **execute_kwargs,
                handler_override=self._load_service_skill_handler,
            )
        else:
            result = await self.tool_executor.execute(**execute_kwargs)
        await self._retain_tool_information(
            thread_id=run.thread_id,
            source=contract_name,
            retained_information=getattr(result, "retained_information", ()),
        )
        if _text(result.safe_output, "status") == "urgent_care_required":
            required_response = _text(result.safe_output, "required_response")
            if required_response:
                self._run_authoritative_final_text[run.id] = required_response
        await self._append_progress(run=run, phase="model_followup", label="我接着处理下一步")
        await self._append_progress(run=run, phase="model_reasoning_after_tool", label="我想一下")
        model_output = getattr(result, "model_output", result.safe_output)
        return SdkToolInvocationResult(
            output_json=json.dumps(model_output, sort_keys=True),
            safe_output_json=json.dumps(result.safe_output, sort_keys=True),
            model_context=result.model_context,
        )

    async def _trusted_tool_args(
        self,
        *,
        run: AgentRun,
        contract_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        guarded_type = guarded_workflow_type(contract_name, args or {})
        if guarded_type is not None:
            guarded_types = self._run_guarded_workflow_types.setdefault(run.id, [])
            if not guarded_types or guarded_types[-1] != guarded_type:
                guarded_types.append(guarded_type)
            guarded_workflow = await self._latest_workflow_state(run=run, workflow_type=guarded_type)
            if guarded_workflow is not None:
                try:
                    validate_workflow_reply_context(
                        guarded_workflow,
                        self._run_workflow_replies.get(run.id, {}),
                    )
                except ApiError as exc:
                    if exc.code in {"missing_workflow_reply_context", "stale_workflow_step"}:
                        self._run_workflow_reply_recovery_types[run.id] = guarded_type
                    raise
        if contract_name == "pregnancy_diary.manage":
            local_date = self._run_local_dates.get(run.id, "")
            return {"runtime_local_date": local_date} if local_date else {}
        if contract_name == "records.milk_analysis.intake":
            return {
                "trusted_current_user_text": self._run_current_user_text.get(run.id, ""),
                "runtime_timezone": self._run_timezones.get(run.id, "UTC"),
            }
        if contract_name == "plans.milk_plan.propose":
            return {
                "runtime_local_date": self._run_local_dates.get(run.id, ""),
                "runtime_timezone": self._run_timezones.get(run.id, "UTC"),
            }
        if contract_name == "support.ticket.propose":
            return {"trusted_current_user_text": self._run_current_user_text.get(run.id, "")}
        expected_form_id = FORM_TOOL_IDS.get(contract_name)
        if expected_form_id is not None:
            submission = self._run_trusted_form_submissions.get(run.id, {}).get(expected_form_id)
            if submission is None:
                return {}
            trusted_args: dict[str, Any] = {
                "confirmed_form_data": _dict(submission, "values"),
                "form_submission_id": _text(submission, "submission_id"),
            }
            if contract_name == "pregnancy.plan_intake.analyze":
                trusted_args["form_artifact_id"] = _text(submission, "artifact_id")
                facts = await self._birth_prep_business_facts(run=run)
                workflow = await self._latest_pregnancy_plan_workflow(run=run)
                trusted_args["runtime_plan_context"] = _pregnancy_runtime_plan_context(facts, workflow=workflow)
                trusted_args["runtime_workflow_context"] = _dict(workflow, "state")
            elif contract_name == "hospital_bag_card_create":
                workflow = await self._latest_hospital_bag_workflow(run=run)
                trusted_args["form_artifact_id"] = _text(submission, "artifact_id")
                workflow_state = _dict(workflow, "state")
                if workflow_state:
                    trusted_args["runtime_workflow_context"] = workflow_state
            return trusted_args
        if contract_name == "pregnancy.plan.propose":
            facts = await self._birth_prep_business_facts(run=run)
            workflow = await self._latest_pregnancy_plan_workflow(run=run)
            return {
                "runtime_plan_context": _pregnancy_runtime_plan_context(facts, workflow=workflow),
                "runtime_workflow_context": _dict(workflow, "state"),
                "trusted_current_user_text": self._run_current_user_text.get(run.id, ""),
            }
        if contract_name == "pregnancy.plan_intake.advance":
            workflow = await self._latest_pregnancy_plan_workflow(run=run)
            return {
                "runtime_workflow_context": _dict(workflow, "state"),
                "trusted_current_user_text": self._run_current_user_text.get(run.id, ""),
                "runtime_checkup_attachment_count": self._run_checkup_attachment_counts.get(run.id, 0),
            }
        if contract_name in FORM_CREATION_TOOL_NAMES:
            facts = await self._birth_prep_business_facts(run=run)
            default_values = _birth_prep_form_default_values(facts)
            if self.fact_service is not None:
                stored_defaults = await self.fact_service.form_defaults(
                    owner_user_id=run.actor_user_id,
                    form_id=FORM_CREATION_IDS[contract_name],
                )
                for key, value in stored_defaults.items():
                    default_values.setdefault(key, value)
            same_turn_defaults = _birth_prep_same_turn_form_default_values(self._run_current_user_text.get(run.id, ""))
            for key, value in same_turn_defaults.items():
                default_values.setdefault(key, value)
            trusted_args = {"default_values": default_values} if default_values else {}
            if contract_name == "pregnancy.plan_intake.start":
                workflow = await self._latest_pregnancy_plan_workflow(run=run)
                trusted_args["runtime_plan_context"] = _pregnancy_runtime_plan_context(facts, workflow=workflow)
                trusted_args["runtime_workflow_context"] = _dict(workflow, "state")
            elif contract_name == "hospital_bag_form_create":
                workflow = await self._latest_hospital_bag_workflow(run=run)
                workflow_state = _dict(workflow, "state")
                if workflow_state:
                    trusted_args["runtime_workflow_context"] = workflow_state
            return trusted_args
        if contract_name == "hospital_bag_cart_update":
            client_groups = self._run_hospital_bag_cart_groups.get(run.id)
            if client_groups is not None:
                return {"groups": client_groups}
            groups = await self._latest_hospital_bag_cart_groups(run=run)
            return {"groups": groups} if groups is not None else {}
        if contract_name == "ibclc_consult_card_create":
            return {
                "trusted_current_user_text": self._run_current_user_text.get(run.id, ""),
                "trusted_previous_assistant_text": self._run_previous_assistant_text.get(run.id, ""),
            }
        if contract_name == CONVERSATION_HISTORY_IMAGE_LOAD_TOOL_NAME:
            return {"visible_image_urls": list(self._run_visible_image_urls.get(run.id, ()))}
        return {}

    async def _birth_prep_business_facts(self, *, run: AgentRun) -> dict[str, Any]:
        cached = self._run_business_facts.setdefault(run.id, {}).get(ServiceSkillId.BIRTH_PREP)
        if cached is not None:
            return cached
        facts = await self._fresh_business_facts_for_skill(run=run, skill_id=ServiceSkillId.BIRTH_PREP)
        self._run_business_facts[run.id][ServiceSkillId.BIRTH_PREP] = facts
        return facts

    async def _latest_pregnancy_plan_workflow(self, *, run: AgentRun) -> dict[str, Any]:
        workflow = await self._latest_workflow_state(run=run, workflow_type=PREGNANCY_PLAN_WORKFLOW_TYPE)
        if workflow is None:
            return {}
        return {
            "workflow_state_id": str(workflow.id),
            "run_id": str(workflow.run_id or ""),
            "status": workflow.status,
            "state": dict(workflow.state) if isinstance(workflow.state, dict) else {},
        }

    async def _latest_workflow_state(self, *, run: AgentRun, workflow_type: str) -> AgentWorkflowState | None:
        loader = getattr(self.repository, "get_latest_workflow_state_for_thread", None)
        if not callable(loader):
            return None
        workflow = await loader(
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            workflow_type=workflow_type,
        )
        if workflow is None:
            return None
        expires_at = workflow.expires_at
        if expires_at is not None:
            normalized_expires_at = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
            if normalized_expires_at <= datetime.now(timezone.utc):
                return None
        return workflow

    async def _completed_turn_workflow_finalization(self, *, run: AgentRun) -> tuple[dict[str, Any], dict[str, Any]]:
        loader = getattr(self.repository, "list_active_workflow_states_for_thread", None)
        if not callable(loader):
            return {}, {}
        workflows = await loader(
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            limit=10,
        )
        requested_type = _text(self._run_workflow_replies.get(run.id, {}), "workflow_type")
        recovery_type = self._run_workflow_reply_recovery_types.get(run.id, "")
        guarded_types = self._run_guarded_workflow_types.get(run.id, [])
        preferred_types = [
            recovery_type,
            *(workflow.workflow_type for workflow in workflows if workflow.run_id == run.id),
            *reversed(guarded_types),
            requested_type,
        ]
        checked_types: set[str] = set()
        preferred_workflow: AgentWorkflowState | None = None
        reply_workflow: AgentWorkflowState | None = None
        workflow_reply: dict[str, Any] = {}
        for workflow_type in preferred_types:
            if not workflow_type or workflow_type in checked_types:
                continue
            checked_types.add(workflow_type)
            matching = [workflow for workflow in workflows if workflow.workflow_type == workflow_type]
            if preferred_workflow is None and matching:
                preferred_workflow = matching[0]
            for workflow in matching:
                if not workflow_accepts_reply(workflow):
                    continue
                reply = build_workflow_reply_context(workflow)
                if reply:
                    workflow_reply = reply
                    reply_workflow = workflow
                    break
            if workflow_reply:
                break

        selected_workflow = reply_workflow or preferred_workflow
        if selected_workflow is None:
            return workflow_reply, {}
        projected = project_workflow_context(
            [selected_workflow],
            trusted_form_submissions=self._run_trusted_form_submissions.get(run.id, {}),
            checkup_attachment_count=self._run_checkup_attachment_counts.get(run.id, 0),
            workflow_reply=workflow_reply,
        )
        return workflow_reply, dict(projected[0]) if projected else {}

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

    async def _latest_hospital_bag_workflow(self, *, run: AgentRun) -> dict[str, Any]:
        loader = getattr(self.repository, "get_latest_workflow_state_for_thread", None)
        if not callable(loader):
            return {}
        workflow = await loader(
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            workflow_type=HOSPITAL_BAG_WORKFLOW_TYPE,
        )
        if workflow is None:
            return {}
        return {
            "workflow_state_id": str(workflow.id),
            "status": workflow.status,
            "state": dict(workflow.state) if isinstance(workflow.state, dict) else {},
        }

    async def _generate_quick_replies(
        self,
        *,
        run: AgentRun,
        turn_context: _AgentTurnContext,
        final_text: str,
        artifacts: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        active_workflow: dict[str, Any],
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
                tool_calls=tool_calls,
                active_workflow=active_workflow,
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
            await self._retain_service_skill(thread_id=run.thread_id, skill=skill)
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
    ) -> AgentAction | None:
        if self.tool_executor is None:
            return None
        actions = await self.repository.list_actions_for_run(run_id=run.id)
        pending_action = next((action for action in reversed(actions) if action.status == "confirmation_required"), None)
        if pending_action is None:
            return None
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
            except Exception:
                LOGGER.warning("Failed to publish live run.progress event.", exc_info=True)
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
            timings_ms=timings_ms,
            final_text_length=final_text_length,
            quick_reply_count=quick_reply_count,
            text_delivery_mode=(
                "provider_delta"
                if self._run_provider_text_delta_seen.get(run.id, False)
                else "final_fallback"
                if self._run_text_segment_counts.get(run.id, 0) > 0
                else "none"
            ),
            text_segment_count=self._run_text_segment_counts.get(run.id, 0),
            error_type=error_type,
        )

    async def _memory_projection(self, *, run: AgentRun) -> list[dict[str, Any]]:
        if self.memory_service is None:
            return []
        return await self.memory_service.get_runtime_snapshot(
            owner_user_id=run.actor_user_id,
            limit=self.config.memory_limit,
        )

    async def _begin_working_context_turn(self, *, run: AgentRun) -> AgentWorkingContextState:
        if self.working_context_store is None:
            return empty_working_context_state()
        try:
            return await self.working_context_store.begin_turn(
                thread_id=run.thread_id,
                skill_ttl_turns=self.config.resident_service_skill_ttl_turns,
                information_token_budget=self.config.known_information_token_budget,
            )
        except Exception:
            LOGGER.warning("Failed to load working context; continuing with an empty short-term context.", exc_info=True)
            return empty_working_context_state()

    async def _active_workflow_states(self, *, run: AgentRun) -> list[AgentWorkflowState]:
        loader = getattr(self.repository, "list_active_workflow_states_for_thread", None)
        if not callable(loader):
            return []
        return await loader(
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            limit=5,
        )

    async def _retain_service_skill(self, *, thread_id: UUID, skill: AgentServiceSkill) -> None:
        if self.working_context_store is None:
            return
        try:
            await self.working_context_store.retain_skill(
                thread_id=thread_id,
                service_skill_id=skill.service_skill_id,
                instructions=skill.prompt_block(),
                skill_ttl_turns=self.config.resident_service_skill_ttl_turns,
            )
        except Exception:
            LOGGER.warning("Failed to retain loaded service skill in working context.", exc_info=True)

    async def _retain_tool_information(
        self,
        *,
        thread_id: UUID,
        source: str,
        retained_information: Any,
    ) -> None:
        if self.working_context_store is None or not isinstance(retained_information, (list, tuple)):
            return
        try:
            for item in retained_information:
                await self.working_context_store.retain_information(
                    thread_id=thread_id,
                    context_key=str(getattr(item, "context_key", "") or ""),
                    source=source,
                    information=dict(getattr(item, "information", {}) or {}),
                    guidance=str(getattr(item, "guidance", "") or ""),
                    ttl_turns=(None if getattr(item, "ttl_turns", 3) is None else max(1, int(getattr(item, "ttl_turns", 3) or 3))),
                    token_budget=self.config.known_information_token_budget,
                    invalidate_prefixes=tuple(getattr(item, "invalidate_prefixes", ()) or ()),
                    priority=max(0, int(getattr(item, "priority", 100) or 100)),
                )
        except Exception:
            LOGGER.warning("Failed to retain tool information in working context.", exc_info=True)

    async def _fresh_business_facts_for_skill(self, *, run: AgentRun, skill_id: ServiceSkillId) -> dict[str, Any]:
        if self.business_facts_projector is None:
            return {}
        return await self.business_facts_projector.project(
            actor=_run_actor(run),
            run_id=run.id,
            service_skill_id=skill_id,
        )


def _history_before(*, messages: list[AgentMessage], before_sequence: int) -> list[dict[str, Any]]:
    return [_to_model_message(message) for message in _history_messages_before(messages=messages, before_sequence=before_sequence)]


def _history_messages_before(*, messages: list[AgentMessage], before_sequence: int) -> list[AgentMessage]:
    return [message for message in messages if message.sequence < before_sequence and message.role in {"user", "assistant"}]


def _latest_assistant_text_before(*, messages: list[AgentMessage], before_sequence: int) -> str:
    return next(
        (_message_text(message) for message in reversed(messages) if message.sequence < before_sequence and message.role == "assistant"),
        "",
    )


def _recent_ibclc_client_event_context(events: list[AgentEvent]) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    for event in events:
        event_type = _text(event.payload, "client_event_type")
        if event_type not in {"ibclc_consult_started", "ibclc_consult_completed"}:
            continue
        projected.append({"type": event_type})
    return projected


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
    payload["semantic"] = artifact_event_payload_semantic(
        artifact_type=artifact.artifact_type,
        artifact_id=str(artifact.id),
    )
    return payload


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


def _has_completed_pregnancy_plan_analysis(tool_calls: list[dict[str, Any]]) -> bool:
    return any(
        _text(tool_call, "tool_name") == "pregnancy.plan_intake.analyze"
        and _text(_dict(tool_call, "safe_output"), "status") == "intake_analyzed"
        for tool_call in tool_calls
    )


def _has_completed_hospital_bag_card(tool_calls: list[dict[str, Any]]) -> bool:
    completed_statuses = {"card_created", "hospital_bag_card_already_created"}
    return any(
        _text(tool_call, "tool_name") == "hospital_bag_card_create"
        and _text(_hospital_bag_tool_output(tool_call), "status") in completed_statuses
        for tool_call in tool_calls
    )


def _completed_hospital_bag_card(tool_calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    completed_statuses = {"card_created", "hospital_bag_card_already_created"}
    for tool_call in reversed(tool_calls):
        if _text(tool_call, "tool_name") != "hospital_bag_card_create":
            continue
        safe_output = _hospital_bag_tool_output(tool_call)
        if _text(safe_output, "status") not in completed_statuses:
            continue
        card = _dict(safe_output, "card")
        card_json = card.get("card_json") or card.get("cardJson")
        if isinstance(card_json, dict):
            return dict(card_json)
    return None


def _hospital_bag_tool_output(tool_call: dict[str, Any]) -> dict[str, Any]:
    safe_output = _dict(tool_call, "safe_output")
    payload_summary = _dict(safe_output, "payload_summary")
    return payload_summary or safe_output


def _timings_with_total(timings_ms: dict[str, float], run_started_at: float) -> dict[str, float]:
    return {**timings_ms, "total_before_finalize": _elapsed_ms(run_started_at)}


SERVICE_SKILL_RECOMMENDED_TOOL_CONTRACTS: dict[ServiceSkillId, tuple[str, ...]] = {
    ServiceSkillId.BIRTH_PREP: (
        "pregnancy.plan_intake.start",
        "pregnancy.plan_intake.analyze",
        "pregnancy.plan_intake.advance",
        "pregnancy.plan.propose",
        "pregnancy.plan_todo.propose",
        "plans.plan_delete.propose",
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
        "devices.guidance.read",
        "devices.unboxing.advance",
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


def _user_context_local_date(user_context: dict[str, Any]) -> str:
    raw = _text(user_context, "message_sent_at") or _text(user_context, "current_time")
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw[:10] if len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-" else ""


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


def _message_text(message: AgentMessage) -> str:
    text = message.content.get("text") if isinstance(message.content, dict) else None
    if isinstance(text, str):
        return text
    return ""


def _workflow_reply_for_turn(
    *,
    current_message: AgentMessage,
    messages: list[AgentMessage],
) -> dict[str, Any]:
    current_content = current_message.content if isinstance(current_message.content, dict) else {}
    client_context = current_content.get("client_context")
    if isinstance(client_context, dict):
        supplied = normalize_workflow_reply_context(client_context.get("workflow_reply"))
        if supplied:
            return supplied
    for message in sorted(messages, key=lambda item: item.sequence, reverse=True):
        if message.sequence >= current_message.sequence or message.role != "assistant":
            continue
        content = message.content if isinstance(message.content, dict) else {}
        reply = normalize_workflow_reply_context(content.get("workflow_reply"))
        if reply:
            return reply
        break
    return {}


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


def _runtime_checkup_attachment_count(message: AgentMessage) -> int:
    content = message.content if isinstance(message.content, dict) else {}
    attachments = content.get("attachments")
    if not isinstance(attachments, list):
        return 0
    count = 0
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("runtime_validated") is not True:
            continue
        attachment_type = _text(attachment, "type")
        content_type = _text(attachment, "content_type").lower()
        if attachment_type == "image" and MODEL_IMAGE_DATA_URL_PATTERN.match(_text(attachment, "data_url")):
            count += 1
        elif attachment_type == "file" and content_type == "application/pdf" and _text(attachment, "file_id"):
            count += 1
    return count


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
    defaults: dict[str, Any] = {}
    if due_date_or_week:
        defaults["due_date_or_week"] = due_date_or_week
        defaults["current_week"] = due_date_or_week
    age = profile.get("age")
    if isinstance(age, (int, float)) and 12 <= int(age) <= 70:
        defaults["age"] = int(age)
    plans = pregnancy.get("plans")
    active_plan = (
        next(
            (
                plan
                for plan in plans
                if isinstance(plan, dict) and _text(plan, "status") == "active" and _text(plan, "plan_type") == "pregnancy"
            ),
            None,
        )
        if isinstance(plans, list)
        else None
    )
    owner = _dict(active_plan or {}, "owner")
    active_defaults = {
        "due_date_or_week": _text(owner, "due_date_or_week") or _text(owner, "current_week"),
        "current_week": _text(owner, "current_week") or _text(owner, "due_date_or_week"),
        "age": owner.get("age"),
        "ivf": _text(owner, "ivf"),
        "fetus_count": _text(owner, "fetus_count"),
        "first_birth": _text(owner, "first_birth"),
        "birth_path": _text(owner, "birth_path"),
        "birth_hospital": _text(owner, "birth_setting"),
        "feeding_intention": _text(owner, "feeding_intention"),
        "support_person": _text(owner, "support_person"),
        "medical_notes": _text(owner, "medical_notes"),
        "doctor_notes": _text(owner, "doctor_notes"),
    }
    active_notes = [note for note in (_text(owner, "medical_notes"), _text(owner, "doctor_notes")) if note]
    if active_notes:
        active_defaults["pregnancy_history_or_notes"] = active_notes
    for key, value in active_defaults.items():
        if value not in ("", None):
            defaults.setdefault(key, value)
    return defaults


def _birth_prep_same_turn_form_default_values(text: str) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    age_match = re.search(r"(?<!\d)(\d{2})\s*岁", str(text or ""))
    if age_match is not None:
        age = int(age_match.group(1))
        if 12 <= age <= 70:
            defaults["age"] = age
    week_match = re.search(r"(?:怀孕|孕)?\s*(\d{1,2})(?:\s*[+＋]\s*(\d))?\s*周", str(text or ""))
    if week_match is not None:
        week = int(week_match.group(1))
        days = int(week_match.group(2) or 0)
        if 1 <= week <= 42 and 0 <= days <= 6:
            current_week = f"{week}+{days}周" if days else f"{week}周"
            defaults["current_week"] = current_week
            defaults["due_date_or_week"] = current_week
    return defaults


def _pregnancy_runtime_plan_context(
    facts: dict[str, Any],
    *,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    workflow_payload = _dict(workflow or {}, "state")
    if workflow_payload:
        if _text(workflow_payload, "consumed_by_action_id") or workflow_payload.get("interrupted_by_safety_signal") is True:
            return {key: value for key, value in context.items() if value not in ("", None)}
        context["workflow_phase"] = _text(workflow_payload, "phase")
        for key in ("analysis_run_id", "source_form_artifact_id", "source_form_submission_id"):
            value = _text(workflow_payload, key)
            if value:
                context[key] = value
        if _text(workflow_payload, "phase") in {
            "personalized_followup",
            "checkup_done_question",
            "checkup_records_upload",
            "final_plan_confirmation",
            "ready_to_generate",
            "awaiting_additional_information",
        }:
            workflow_state_id = _text(workflow or {}, "workflow_state_id")
            if workflow_state_id:
                context["workflow_state_id"] = workflow_state_id
            plan_context = _dict(workflow_payload, "plan_context")
            context.update({key: value for key, value in plan_context.items() if value not in ("", None)})
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
        "requires_confirmation": True,
        "confirmation_policy": "always",
        "user_visible": True,
        "semantic": action_event_payload_semantic(action_status=action.status, action_id=str(action.id)),
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


def _runner_supports_web_search(runner: AgentModelRunner) -> bool:
    supports = getattr(runner, "supports_web_search", None)
    return bool(supports()) if callable(supports) else False


def _allowed_health_web_search_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed_domains = set(HEALTH_GUIDANCE_ALLOWED_DOMAINS)
    allowed: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for citation in citations:
        url = str(citation.get("url") or "").strip()
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            continue
        if host not in allowed_domains or url in seen_urls:
            continue
        seen_urls.add(url)
        allowed.append(
            {
                "index": len(allowed) + 1,
                "url": url,
                "title": str(citation.get("title") or "").strip() or "参考来源",
            }
        )
        if len(allowed) >= 8:
            break
    return allowed
