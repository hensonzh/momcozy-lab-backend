from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import ApiError
from app.core.logging import log_agent_runtime_event
from app.infrastructure.object_storage.base import ObjectStorage
from app.agent_runtime.actions.policy import AgentActionPolicy
from app.agent_runtime.context.facts import AgentFactService
from app.agent_runtime.context.items import ContextItemAppend
from app.agent_runtime.context.selection import (
    estimate_json_tokens,
    select_bounded_context_items,
)
from app.agent_runtime.context.workflow_command import normalize_workflow_command
from app.agent_runtime.context.workflow_reply import (
    build_workflow_reply_context,
    normalize_workflow_reply_context,
    validate_workflow_reply_context,
)
from app.agent_runtime.events.publisher import AgentEventPublisher
from app.agent_runtime.events.semantics import progress_live_dedupe_key, run_progress_payload, web_search_event_semantic
from app.agent_runtime.events.transient import AgentTransientStream
from app.agent_runtime.providers import (
    AgentModelRunner,
    SdkNodeRequest,
    SdkNodeResult,
    SdkToolDefinition,
    SdkToolNamespace,
)
from app.agent_runtime.runs.execution import AgentRunExecutionResult
from app.agent_runtime.runs.models import AgentAction, AgentArtifact, AgentEvent, AgentMessage, AgentRun, AgentWorkflowState
from app.agents.cozymate.quick_replies import QuickReplyFinalizer
from app.agent_runtime.runs.registry import validate_runtime
from app.agent_runtime.runs.repository import AgentRuntimeRepository
from app.agent_runtime.runs.response_text import AppendOnlyAgentResponseProjector, agent_response_text_integrity
from app.agent_runtime.tools import (
    ToolContractRegistry,
    ToolHandlerContext,
    ToolNamespace,
    ToolNamespaceRegistry,
    ToolResult,
)
from app.agent_runtime.tools.payloads import DEFAULT_MAX_INLINE_PAYLOAD_BYTES, maybe_externalize_json_payload
from app.modules.auth import CurrentUser

from .context import BusinessFactsProjector
from .context.client import project_cozymate_client_context
from .actions import cozymate_action_policy
from .event_semantics import (
    artifact_event_payload_semantic,
)
from .health_guidance import HEALTH_GUIDANCE_ALLOWED_DOMAINS
from .prompts import DEFAULT_STABLE_SYSTEM_PROMPT
from .service_skills import ServiceSkillId
from .skill_registry import (
    AgentServiceSkill,
    AgentServiceSkillRegistry,
    default_service_skill_registry,
)
from .tools import (
    CozymateToolExecutor,
    default_tool_namespace_registry,
    default_tool_registry,
)
from .tools.hospital_bag_flow import (
    HOSPITAL_BAG_WORKFLOW_TYPE,
    ensure_hospital_bag_completion_followup,
)
from .tools.pregnancy_plan_flow import (
    PREGNANCY_PLAN_WORKFLOW_TYPE,
    ensure_pregnancy_plan_final_question,
    pregnancy_plan_workflow_context,
)
from .workflows.ongoing_work import project_workflow_context
from .workflows.reply import guarded_workflow_type, workflow_accepts_reply


LOAD_SERVICE_SKILL_TOOL_NAME = "load_service_skill"
CONVERSATION_HISTORY_IMAGE_LOAD_TOOL_NAME = "conversation_history_image_load"
COZYMATE_AGENT_ID = "cozymate_service_agent"
LOGGER = logging.getLogger("production_backend.agent_runtime.executor")
FORM_TOOL_IDS = {
    "hospital_bag_card_create": "hospital_bag_intake",
}
FORM_CREATION_TOOL_NAMES = {"hospital_bag_form_create"}
FORM_CREATION_IDS = {
    "hospital_bag_form_create": "hospital_bag_intake",
}
MARKDOWN_IMAGE_URL_PATTERN = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))")
MODEL_IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


@dataclass(frozen=True)
class CozymateAgentExecutorConfig:
    history_limit: int = 40
    context_item_fetch_limit: int = 160
    model_context_item_limit: int = 64
    model_context_token_budget: int = 12_000
    workflow_context_token_budget: int = 1_200

    def __post_init__(self) -> None:
        if self.history_limit < 1:
            raise ValueError("history_limit must be positive")
        if self.context_item_fetch_limit < 1:
            raise ValueError("context_item_fetch_limit must be positive")
        if self.model_context_item_limit < 1:
            raise ValueError("model_context_item_limit must be positive")
        if self.model_context_token_budget < 256:
            raise ValueError("model_context_token_budget must be at least 256")
        if self.workflow_context_token_budget < 256:
            raise ValueError("workflow_context_token_budget must be at least 256")


@dataclass
class _AgentTurnContext:
    current_message: AgentMessage
    messages: list[AgentMessage]
    context_items: list[dict[str, Any]]
    context_item_refs: list[dict[str, Any]]
    context_estimated_tokens: int
    context_selection_policy: dict[str, Any]
    runtime_context: dict[str, Any]
    trusted_form_submissions: dict[str, dict[str, Any]]
    checkup_attachment_count: int
    workflow_reply: dict[str, Any]
    workflow_command: dict[str, Any]
    timings_ms: dict[str, float]


@dataclass(frozen=True)
class _AgentTurnToolCatalog:
    tool_namespaces: tuple[ToolNamespace, ...]
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class _PreparedModelTurn:
    user_context: dict[str, Any]
    model_input: list[dict[str, Any]]
    item_refs: list[dict[str, Any]]
    dynamic_context: dict[str, Any]
    estimated_input_tokens: int


@dataclass
class _RunTurnState:
    assistant_message_id: UUID = field(default_factory=uuid4)
    text_projector: AppendOnlyAgentResponseProjector = field(default_factory=AppendOnlyAgentResponseProjector)
    text_stream_emitted: str = ""
    authoritative_final_text: str = ""
    current_user_text: str = ""
    local_date: str = ""
    timezone: str = "UTC"
    previous_assistant_text: str = ""
    trusted_form_submissions: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkup_attachment_count: int = 0
    business_facts: dict[ServiceSkillId, dict[str, Any]] = field(default_factory=dict)
    visible_image_urls: tuple[str, ...] = ()
    hospital_bag_cart_groups: list[dict[str, Any]] | None = None
    text_segment_count: int = 0
    provider_text_delta_seen: bool = False
    workflow_reply: dict[str, Any] = field(default_factory=dict)
    guarded_workflow_types: list[str] = field(default_factory=list)
    workflow_reply_recovery_type: str = ""


class CozymateAgentExecutor:
    def __init__(
        self,
        *,
        repository: AgentRuntimeRepository,
        sdk_runner: AgentModelRunner,
        tool_registry: ToolContractRegistry | None = None,
        tool_namespace_registry: ToolNamespaceRegistry | None = None,
        tool_executor: CozymateToolExecutor | None = None,
        event_sink: AgentEventPublisher | None = None,
        action_policy: AgentActionPolicy | None = None,
        service_skill_registry: AgentServiceSkillRegistry | None = None,
        business_facts_projector: BusinessFactsProjector | None = None,
        transient_stream: AgentTransientStream | None = None,
        quick_reply_finalizer: QuickReplyFinalizer | None = None,
        fact_service: AgentFactService | None = None,
        config: CozymateAgentExecutorConfig | None = None,
        object_storage: ObjectStorage | None = None,
        max_inline_artifact_payload_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.sdk_runner = sdk_runner
        self.tool_registry = tool_registry or default_tool_registry()
        self.tool_namespace_registry = tool_namespace_registry or default_tool_namespace_registry(self.tool_registry)
        self.event_sink = event_sink
        self.action_policy = action_policy or cozymate_action_policy()
        self.service_skill_registry = service_skill_registry or default_service_skill_registry()
        self.business_facts_projector = business_facts_projector
        self.transient_stream = transient_stream
        self.quick_reply_finalizer = quick_reply_finalizer
        self.fact_service = fact_service
        self.config = config or CozymateAgentExecutorConfig()
        self.object_storage = object_storage
        self.max_inline_artifact_payload_bytes = max_inline_artifact_payload_bytes
        self.clock = clock or _utcnow
        self._business_tools_enabled = tool_executor is not None
        self.tool_executor = tool_executor or CozymateToolExecutor(
            registry=self.tool_registry,
            repository=self.repository,
            event_sink=event_sink,
            object_storage=object_storage,
            transient_stream=transient_stream,
        )
        self._turn_states: dict[UUID, _RunTurnState] = {}

    async def __call__(self, run: AgentRun) -> AgentRunExecutionResult:
        return await self.execute(run=run)

    async def execute(self, *, run: AgentRun) -> AgentRunExecutionResult:
        run_started_at = perf_counter()
        validate_runtime(version=run.runtime_version, pattern=run.runtime_pattern)
        turn_state = self._initialize_turn_state(run.id)
        try:
            turn_context = await self._load_turn_context(run=run)
            turn_state.trusted_form_submissions = turn_context.trusted_form_submissions
            turn_state.checkup_attachment_count = turn_context.checkup_attachment_count
            turn_state.current_user_text = _message_text(turn_context.current_message)
            turn_state.workflow_reply = turn_context.workflow_reply
            turn_state.previous_assistant_text = _latest_assistant_text_before(
                messages=turn_context.messages,
                before_sequence=turn_context.current_message.sequence,
            )
            turn_state.hospital_bag_cart_groups = _current_hospital_bag_cart_groups(turn_context.current_message)
            await self._append_progress(run=run, phase="context_ready", label="我先理解一下你的需求～")
            deterministic_result = await self._run_structured_workflow_command(
                run=run,
                turn_context=turn_context,
            )
            if deterministic_result is not None:
                return await self._finalize_turn_result(
                    run=run,
                    turn_context=turn_context,
                    result=deterministic_result,
                    run_started_at=run_started_at,
                )
            tool_catalog = self._tool_catalog_for_turn()
            prepared_turn = self._prepare_model_turn(turn_context=turn_context)
            await self._persist_model_context_snapshot(
                run=run,
                turn_context=turn_context,
                prepared_turn=prepared_turn,
            )
            turn_state.local_date = _user_context_local_date(prepared_turn.user_context)
            turn_state.timezone = _text(prepared_turn.user_context, "timezone") or "UTC"
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
            self._turn_states.pop(run.id, None)

    def _turn_state(self, run_id: UUID) -> _RunTurnState:
        return self._turn_states[run_id]

    def _initialize_turn_state(self, run_id: UUID) -> _RunTurnState:
        state = _RunTurnState()
        self._turn_states[run_id] = state
        return state

    async def _load_turn_context(self, *, run: AgentRun) -> _AgentTurnContext:
        timings_ms: dict[str, float] = {}
        await self._append_progress(run=run, phase="context_loading", label="我已经收到你的消息啦～")
        context_started_at = perf_counter()
        messages = await self.repository.list_messages_for_thread(thread_id=run.thread_id, limit=self.config.history_limit)
        context_item_records = await self.repository.list_context_items_for_thread(
            thread_id=run.thread_id,
            limit=self.config.context_item_fetch_limit,
        )
        current_message = next(
            (message for message in reversed(messages) if message.run_id == run.id and message.role == "user"),
            None,
        )
        if current_message is None:
            raise ApiError(code="missing_user_message", message="Agent run has no user message.", status=409)
        if not context_item_records:
            raise ApiError(
                code="missing_context_items",
                message="Agent run has no append-only context items.",
                status=409,
            )
        current_item_key = f"message:{current_message.id}"
        available_item_keys = {
            str(getattr(record, "item_key", "") or "")
            for record in context_item_records
        }
        required_item_keys = (
            {current_item_key}
            if current_item_key in available_item_keys
            else set()
        )
        selection = select_bounded_context_items(
            context_item_records,
            required_item_keys=required_item_keys,
            max_items=self.config.model_context_item_limit,
            max_estimated_tokens=self.config.model_context_token_budget,
        )
        if not selection.items:
            raise ApiError(
                code="missing_context_items",
                message="Agent run has no usable model context items.",
                status=409,
            )
        if required_item_keys and current_item_key not in {
            str(item.get("item_key") or "")
            for item in selection.item_refs
        }:
            raise ApiError(
                code="missing_current_context_item",
                message="The current user message is outside the bounded model context.",
                status=409,
            )
        self._turn_state(run.id).visible_image_urls = _visible_assistant_image_urls(
            messages=messages,
            before_sequence=current_message.sequence,
        )
        timings_ms["context_base"] = _elapsed_ms(context_started_at)
        trusted_form_submissions = _trusted_form_submissions(current_message)
        checkup_attachment_count = _runtime_checkup_attachment_count(current_message)
        workflow_reply = _workflow_reply_for_turn(current_message=current_message, messages=messages)
        workflow_command = _workflow_command_for_turn(current_message=current_message)
        workflow_states = await self._active_workflows_for_context(run=run)
        projected_workflows = project_workflow_context(
            workflow_states,
            trusted_form_submissions=trusted_form_submissions,
            checkup_attachment_count=checkup_attachment_count,
            workflow_reply=workflow_reply,
        )
        bounded_workflows = _bounded_workflow_context(
            projected_workflows,
            max_estimated_tokens=self.config.workflow_context_token_budget,
        )
        runtime_context = (
            {
                "schema_version": "cozymate.runtime_context.v1",
                "active_workflows": bounded_workflows,
                "instruction": (
                    "Treat active_workflows as authoritative process state. "
                    "Answer unrelated requests normally without advancing a workflow; "
                    "advance it only when the current user request is relevant."
                ),
            }
            if bounded_workflows
            else {}
        )
        selection_policy = {
            "schema_version": "context_selection.v1",
            "context_item_fetch_limit": self.config.context_item_fetch_limit,
            "model_context_item_limit": self.config.model_context_item_limit,
            "model_context_token_budget": self.config.model_context_token_budget,
            "workflow_context_token_budget": self.config.workflow_context_token_budget,
            "fetched_item_count": len(context_item_records),
            "selected_item_count": len(selection.items),
            "dropped_item_count": selection.dropped_item_count,
            "base_estimated_tokens": selection.estimated_tokens,
            "workflow_estimated_tokens": estimate_json_tokens(runtime_context),
        }

        return _AgentTurnContext(
            current_message=current_message,
            messages=messages,
            context_items=selection.items,
            context_item_refs=selection.item_refs,
            context_estimated_tokens=selection.estimated_tokens,
            context_selection_policy=selection_policy,
            runtime_context=runtime_context,
            trusted_form_submissions=trusted_form_submissions,
            checkup_attachment_count=checkup_attachment_count,
            workflow_reply=workflow_reply,
            workflow_command=workflow_command,
            timings_ms=timings_ms,
        )

    async def _run_structured_workflow_command(
        self,
        *,
        run: AgentRun,
        turn_context: _AgentTurnContext,
    ) -> SdkNodeResult | None:
        command_context = turn_context.workflow_command
        if not command_context:
            return None
        if _text(command_context, "workflow_type") != PREGNANCY_PLAN_WORKFLOW_TYPE:
            raise ApiError(
                code="unsupported_workflow_command",
                message="The structured workflow command is not supported.",
                status=422,
            )

        workflow = await self._latest_workflow_state(
            run=run,
            workflow_type=PREGNANCY_PLAN_WORKFLOW_TYPE,
        )
        if workflow is None:
            raise ApiError(
                code="pregnancy_plan_workflow_not_active",
                message="An active pregnancy-plan workflow is required.",
                status=409,
            )
        try:
            validate_workflow_reply_context(workflow, turn_context.workflow_reply)
        except ApiError as exc:
            if exc.code not in {"missing_workflow_reply_context", "stale_workflow_step"}:
                raise
            self._turn_state(run.id).workflow_reply_recovery_type = PREGNANCY_PLAN_WORKFLOW_TYPE
            recovery_text = "孕期计划的当前步骤已经更新，请按下方最新问题继续。"
            self._turn_state(run.id).authoritative_final_text = recovery_text
            return SdkNodeResult(final_text=recovery_text)

        workflow_state = dict(workflow.state) if isinstance(workflow.state, dict) else {}
        workflow_context = pregnancy_plan_workflow_context(
            workflow_state,
            checkup_attachment_count=turn_context.checkup_attachment_count,
        )
        command = _text(command_context, "command")
        allowed_commands = {
            str(item).strip()
            for item in workflow_context.get("allowed_commands", [])
            if isinstance(item, str) and str(item).strip()
        }
        if command not in allowed_commands:
            raise ApiError(
                code="pregnancy_plan_command_not_allowed",
                message="The pregnancy-plan command is not allowed at the current step.",
                status=409,
            )
        current_step = _dict(workflow_context, "current_step")
        supplied_step_id = _text(command_context, "step_id")
        if command == "answer_current" and supplied_step_id != _text(current_step, "id"):
            self._turn_state(run.id).workflow_reply_recovery_type = PREGNANCY_PLAN_WORKFLOW_TYPE
            recovery_text = "孕期计划的当前问题已经变化，请按下方最新问题继续。"
            self._turn_state(run.id).authoritative_final_text = recovery_text
            return SdkNodeResult(final_text=recovery_text)

        args = {
            key: command_context[key]
            for key in ("command", "choice_id", "answer", "step_id", "restart", "scope")
            if key in command_context
        }
        tool_calls: list[dict[str, Any]] = []
        safe_output = await self._execute_structured_pregnancy_plan_tool(
            run=run,
            args=args,
        )
        tool_calls.append(
            {
                "tool_name": "pregnancy_plan_workflow",
                "status": "completed",
                "args": args,
                "safe_output": safe_output,
            }
        )

        next_context = _dict(safe_output, "workflow_context")
        next_step = _dict(next_context, "current_step")
        if (
            command == "answer_current"
            and _text(next_step, "id") == "generate_plan"
            and "generate_plan"
            in {
                str(item).strip()
                for item in next_context.get("allowed_commands", [])
                if isinstance(item, str)
            }
        ):
            generation_args = {
                "command": "generate_plan",
                **({"scope": args["scope"]} if "scope" in args else {}),
            }
            safe_output = await self._execute_structured_pregnancy_plan_tool(
                run=run,
                args=generation_args,
            )
            tool_calls.append(
                {
                    "tool_name": "pregnancy_plan_workflow",
                    "status": "completed",
                    "args": generation_args,
                    "safe_output": safe_output,
                }
            )

        final_text = _pregnancy_workflow_command_final_text(safe_output)
        self._turn_state(run.id).authoritative_final_text = final_text
        return SdkNodeResult(final_text=final_text, tool_calls=tool_calls)

    async def _execute_structured_pregnancy_plan_tool(
        self,
        *,
        run: AgentRun,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        guarded_types = self._turn_state(run.id).guarded_workflow_types
        if not guarded_types or guarded_types[-1] != PREGNANCY_PLAN_WORKFLOW_TYPE:
            guarded_types.append(PREGNANCY_PLAN_WORKFLOW_TYPE)
        trusted_args = await self._trusted_tool_args(
            run=run,
            contract_name="pregnancy_plan_workflow",
            args=args,
        )
        trusted_args = {
            **trusted_args,
            "runtime_structured_workflow_command": True,
        }
        execution = await self.tool_executor.execute(
            actor=_run_actor(run),
            run_id=run.id,
            tool_name="pregnancy_plan_workflow",
            call_id=f"ui-pregnancy-plan-{uuid4().hex}",
            args=args,
            trusted_args=trusted_args,
        )
        safe_output = dict(execution.safe_output or {})
        if _text(safe_output, "status") == "urgent_care_required":
            required_response = _text(safe_output, "required_response")
            if required_response:
                self._turn_state(run.id).authoritative_final_text = required_response
        return safe_output

    async def _active_workflows_for_context(
        self,
        *,
        run: AgentRun,
    ) -> list[AgentWorkflowState]:
        thread_workflows: list[AgentWorkflowState] = []
        thread_loader = getattr(
            self.repository,
            "list_active_workflow_states_for_thread",
            None,
        )
        if callable(thread_loader):
            thread_workflows = await thread_loader(
                thread_id=run.thread_id,
                owner_user_id=run.actor_user_id,
                limit=5,
            )

        owner_workflows: list[AgentWorkflowState] = []
        owner_loader = getattr(
            self.repository,
            "list_active_workflow_states_for_owner",
            None,
        )
        if callable(owner_loader):
            owner_workflows = await owner_loader(
                owner_user_id=run.actor_user_id,
                workflow_type=PREGNANCY_PLAN_WORKFLOW_TYPE,
                limit=1,
            )
        else:
            latest_owner_loader = getattr(
                self.repository,
                "get_latest_workflow_state_for_owner",
                None,
            )
            if callable(latest_owner_loader):
                workflow = await latest_owner_loader(
                    owner_user_id=run.actor_user_id,
                    workflow_type=PREGNANCY_PLAN_WORKFLOW_TYPE,
                )
                if workflow is not None:
                    owner_workflows = [workflow]

        merged: list[AgentWorkflowState] = []
        seen_ids: set[UUID] = set()
        for workflow in (*owner_workflows, *thread_workflows):
            if workflow.id in seen_ids or not _workflow_is_active(workflow):
                continue
            seen_ids.add(workflow.id)
            merged.append(workflow)
        return merged

    def _tool_catalog_for_turn(self) -> _AgentTurnToolCatalog:
        tool_namespaces: tuple[ToolNamespace, ...] = (
            self.tool_namespace_registry.list() if self._business_tools_enabled and self.sdk_runner.supports_tool_namespaces() else ()
        )
        if not self._business_tools_enabled:
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
        user_context = _user_context(current_message=turn_context.current_message, now=self.clock())
        model_input = [dict(item) for item in turn_context.context_items]
        item_refs = [dict(item) for item in turn_context.context_item_refs]
        dynamic_context: dict[str, Any] = {}
        if turn_context.runtime_context:
            insertion_position = _current_message_context_position(
                item_refs=item_refs,
                items=model_input,
                current_message_id=turn_context.current_message.id,
            )
            developer_item = {
                "role": "developer",
                "content": {
                    "runtime_context": turn_context.runtime_context,
                },
            }
            model_input.insert(insertion_position, developer_item)
            for item_ref in item_refs:
                selected_position = int(item_ref.get("position") or 0)
                item_ref["model_input_position"] = (
                    selected_position + 1
                    if selected_position >= insertion_position
                    else selected_position
                )
            dynamic_context = {
                "model_input_position": insertion_position,
                "item": developer_item,
            }
        else:
            for item_ref in item_refs:
                item_ref["model_input_position"] = int(item_ref.get("position") or 0)
        return _PreparedModelTurn(
            user_context=user_context,
            model_input=model_input,
            item_refs=item_refs,
            dynamic_context=dynamic_context,
            estimated_input_tokens=estimate_json_tokens(model_input),
        )

    async def _persist_model_context_snapshot(
        self,
        *,
        run: AgentRun,
        turn_context: _AgentTurnContext,
        prepared_turn: _PreparedModelTurn,
    ) -> None:
        append_snapshot = getattr(
            self.repository,
            "append_model_context_snapshot",
            None,
        )
        if not callable(append_snapshot):
            return
        await append_snapshot(
            run_id=run.id,
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            schema_version="model_context_snapshot.v1",
            item_refs=prepared_turn.item_refs,
            dynamic_context=prepared_turn.dynamic_context,
            selection_policy={
                **turn_context.context_selection_policy,
                "estimated_input_tokens": prepared_turn.estimated_input_tokens,
            },
            input_item_count=len(prepared_turn.model_input),
            estimated_input_tokens=prepared_turn.estimated_input_tokens,
            model_input_sha256=_model_input_sha256(prepared_turn.model_input),
        )

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
                instructions=DEFAULT_STABLE_SYSTEM_PROMPT,
                model_input=prepared_turn.model_input,
                tool_names=tool_catalog.tool_names,
                tool_namespaces=_sdk_tool_namespaces(tool_catalog.tool_namespaces),
                tool_search_enabled=_tool_search_enabled(tool_catalog.tool_namespaces),
                tools=self._sdk_tools(run=run, tool_names=tool_catalog.tool_names, tool_namespaces=tool_catalog.tool_namespaces),
                trace_id=run.trace_id,
                service_skill_id=COZYMATE_AGENT_ID,
                on_text_delta=self._text_delta_handler(run=run),
                on_context_items=self._context_item_handler(run=run),
                web_search_enabled=web_search_enabled,
                web_search_required=False,
                web_search_allowed_domains=HEALTH_GUIDANCE_ALLOWED_DOMAINS if web_search_enabled else (),
            )
        )
        turn_context.timings_ms["model_reasoning"] = _elapsed_ms(model_started_at)
        return result

    def _context_item_handler(self, *, run: AgentRun) -> Callable[[tuple[ContextItemAppend, ...]], Awaitable[None]]:
        async def append(items: tuple[ContextItemAppend, ...]) -> None:
            await self.repository.append_context_items(
                thread_id=run.thread_id,
                run_id=run.id,
                items=items,
            )

        return append

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
                "message_id": str(self._turn_state(run.id).assistant_message_id),
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
        await self._persist_artifacts_from_result(run=run, artifacts=result.artifacts)
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
        authoritative_final_text = self._turn_state(run.id).authoritative_final_text
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
                self._turn_state(run.id).text_stream_emitted = linked_final_text
                final_text = linked_final_text
        if not final_text:
            raise ApiError(code="empty_agent_response", message="Agent runtime returned an empty response.", status=502)
        finish_timings_ms = _timings_with_total(turn_context.timings_ms, run_started_at)
        workflow_finalization_started_at = perf_counter()
        workflow_reply, quick_reply_workflow, workflow_prompt = await self._completed_turn_workflow_finalization(run=run)
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
            assistant_message_id=self._turn_state(run.id).assistant_message_id,
            quick_replies=quick_replies,
            workflow_reply=workflow_reply,
            workflow_prompt=workflow_prompt,
            stream_segment_count=self._turn_state(run.id).text_segment_count,
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
            if self._turn_state(run.id).authoritative_final_text:
                return
            projector = self._turn_state(run.id).text_projector
            sanitized_delta = projector.push(delta)
            if not sanitized_delta:
                return
            self._turn_state(run.id).provider_text_delta_seen = True
            self._turn_state(run.id).text_stream_emitted = projector.text
            await self._publish_text_delta(
                run=run,
                delta=sanitized_delta,
                event_publisher=event_publisher,
                transient_stream=transient_stream,
            )

        return publish

    async def _finalize_text_projector(self, *, run: AgentRun) -> None:
        projector = self._turn_state(run.id).text_projector
        final_delta = projector.finalize()
        if not final_delta:
            return
        self._turn_state(run.id).text_stream_emitted = projector.text
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
        streamed_text = self._turn_state(run.id).text_stream_emitted
        if authoritative:
            if streamed_text.endswith(provider_final_text):
                return streamed_text
            authoritative_delta = provider_final_text if not streamed_text else f"\n\n{provider_final_text}"
            combined_text = f"{streamed_text}{authoritative_delta}"
            self._turn_state(run.id).text_stream_emitted = combined_text
            await self._publish_text_delta(
                run=run,
                delta=authoritative_delta,
                event_publisher=self.event_sink,
                transient_stream=self.transient_stream,
            )
            return combined_text
        if not streamed_text:
            self._turn_state(run.id).text_stream_emitted = provider_final_text
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
            self._turn_state(run.id).text_stream_emitted = provider_final_text
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
        event_publisher: AgentEventPublisher | None,
        transient_stream: AgentTransientStream | None,
    ) -> None:
        if not delta:
            return
        message_stream_id = str(self._turn_state(run.id).assistant_message_id)
        segment_index = self._turn_state(run.id).text_segment_count
        prefix_integrity = agent_response_text_integrity(self._turn_state(run.id).text_stream_emitted)
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
        self._turn_state(run.id).text_segment_count = segment_index + 1

    def _sdk_tools(
        self,
        *,
        run: AgentRun,
        tool_names: tuple[str, ...],
        tool_namespaces: tuple[ToolNamespace, ...],
    ) -> tuple[SdkToolDefinition, ...]:
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
        load_service_skill = self._sdk_tool_definition(
            run=run,
            tool_name=LOAD_SERVICE_SKILL_TOOL_NAME,
        )
        return (load_service_skill, *business_tools)

    def _sdk_tool_definition(
        self,
        *,
        run: AgentRun,
        tool_name: str,
        namespace: ToolNamespace | None = None,
    ) -> SdkToolDefinition:
        contract = self.tool_registry.get(tool_name)
        async def invoke(args_json: str) -> ToolResult:
            return await self._invoke_sdk_tool(run=run, contract_name=contract.name, args_json=args_json)

        return SdkToolDefinition(
            contract_name=contract.name,
            description=contract.description,
            params_json_schema=contract.input_schema,
            invoke=invoke,
            namespace_name=namespace.name if namespace is not None else "",
            defer_loading=namespace is not None and contract.loading_mode == "deferred",
        )

    async def _load_service_skill_handler(self, context: ToolHandlerContext) -> ToolResult:
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
        self._turn_state(context.run_id).business_facts[skill_id] = facts
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
        return ToolResult.json(output)

    async def _invoke_sdk_tool(
        self,
        *,
        run: AgentRun,
        contract_name: str,
        args_json: str,
    ) -> ToolResult:
        args = _json_object(args_json)
        execute_kwargs: dict[str, Any] = {
            "actor": _run_actor(run),
            "run_id": run.id,
            "tool_name": contract_name,
            "call_id": f"sdk-{contract_name}-{uuid4().hex}",
            "args": args,
        }
        trusted_args = await self._trusted_tool_args(run=run, contract_name=contract_name, args=args)
        if trusted_args:
            execute_kwargs["trusted_args"] = trusted_args
        if contract_name == LOAD_SERVICE_SKILL_TOOL_NAME:
            result = await self.tool_executor.execute(
                **execute_kwargs,
                handler_override=self._load_service_skill_handler,
            )
        else:
            result = await self.tool_executor.execute(**execute_kwargs)
        if _text(result.safe_output, "status") == "urgent_care_required":
            required_response = _text(result.safe_output, "required_response")
            if required_response:
                self._turn_state(run.id).authoritative_final_text = required_response
        await self._append_progress(run=run, phase="model_followup", label="我接着处理下一步")
        await self._append_progress(run=run, phase="model_reasoning_after_tool", label="我想一下")
        return result.tool_result

    async def _trusted_tool_args(
        self,
        *,
        run: AgentRun,
        contract_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        guarded_type = guarded_workflow_type(contract_name, args or {})
        if guarded_type is not None:
            guarded_types = self._turn_state(run.id).guarded_workflow_types
            if not guarded_types or guarded_types[-1] != guarded_type:
                guarded_types.append(guarded_type)
            guarded_workflow = await self._latest_workflow_state(run=run, workflow_type=guarded_type)
            if guarded_workflow is not None:
                try:
                    validate_workflow_reply_context(
                        guarded_workflow,
                        self._turn_state(run.id).workflow_reply,
                    )
                except ApiError as exc:
                    if exc.code in {"missing_workflow_reply_context", "stale_workflow_step"}:
                        self._turn_state(run.id).workflow_reply_recovery_type = guarded_type
                    raise
        if contract_name in {"pregnancy_diary_query", "pregnancy_diary_save", "pregnancy_diary_delete"}:
            local_date = self._turn_state(run.id).local_date
            diary_trusted_args: dict[str, Any] = {
                "trusted_current_user_text": self._turn_state(run.id).current_user_text
            }
            if local_date:
                diary_trusted_args["runtime_local_date"] = local_date
            return diary_trusted_args
        if contract_name == "records_milk_analysis_intake":
            return {
                "trusted_current_user_text": self._turn_state(run.id).current_user_text,
                "runtime_timezone": self._turn_state(run.id).timezone,
            }
        if contract_name == "plans_milk_plan_propose":
            return {
                "runtime_local_date": self._turn_state(run.id).local_date,
                "runtime_timezone": self._turn_state(run.id).timezone,
            }
        if contract_name == "support_ticket_propose":
            return {"trusted_current_user_text": self._turn_state(run.id).current_user_text}
        if contract_name == "pregnancy_plan_workflow":
            workflow = await self._latest_pregnancy_plan_workflow(run=run)
            trusted_args: dict[str, Any] = {
                "runtime_plan_context": _pregnancy_workflow_runtime_context(workflow=workflow),
                "runtime_workflow_context": _dict(workflow, "state"),
                "trusted_current_user_text": self._turn_state(run.id).current_user_text,
                "runtime_checkup_attachment_count": self._turn_state(run.id).checkup_attachment_count,
            }
            command = _text(args or {}, "command")
            if command == "submit_form":
                submission = self._turn_state(run.id).trusted_form_submissions.get(
                    "birth_journey_basic_info_intake"
                )
                if submission is not None:
                    trusted_args.update(
                        {
                            "confirmed_form_data": _dict(submission, "values"),
                            "form_submission_id": _text(submission, "submission_id"),
                            "form_artifact_id": _text(submission, "artifact_id"),
                        }
                    )
            if command == "start_or_resume":
                default_values: dict[str, Any] = {}
                if self.fact_service is not None:
                    stored_defaults = await self.fact_service.form_defaults(
                        owner_user_id=run.actor_user_id,
                        form_id="birth_journey_basic_info_intake",
                    )
                    for key, value in stored_defaults.items():
                        default_values.setdefault(key, value)
                same_turn_defaults = _birth_prep_same_turn_form_default_values(
                    self._turn_state(run.id).current_user_text
                )
                for key, value in same_turn_defaults.items():
                    default_values.setdefault(key, value)
                if default_values:
                    trusted_args["default_values"] = default_values
            return trusted_args
        expected_form_id = FORM_TOOL_IDS.get(contract_name)
        if expected_form_id is not None:
            submission = self._turn_state(run.id).trusted_form_submissions.get(expected_form_id)
            if submission is None:
                return {}
            form_trusted_args: dict[str, Any] = {
                "confirmed_form_data": _dict(submission, "values"),
                "form_submission_id": _text(submission, "submission_id"),
            }
            if contract_name == "hospital_bag_card_create":
                workflow = await self._latest_hospital_bag_workflow(run=run)
                form_trusted_args["form_artifact_id"] = _text(submission, "artifact_id")
                workflow_state = _dict(workflow, "state")
                if workflow_state:
                    form_trusted_args["runtime_workflow_context"] = workflow_state
            return form_trusted_args
        if contract_name in FORM_CREATION_TOOL_NAMES:
            form_default_values: dict[str, Any] = {}
            if self.fact_service is not None:
                stored_defaults = await self.fact_service.form_defaults(
                    owner_user_id=run.actor_user_id,
                    form_id=FORM_CREATION_IDS[contract_name],
                )
                for key, value in stored_defaults.items():
                    form_default_values.setdefault(key, value)
            same_turn_defaults = _birth_prep_same_turn_form_default_values(self._turn_state(run.id).current_user_text)
            for key, value in same_turn_defaults.items():
                form_default_values.setdefault(key, value)
            creation_trusted_args: dict[str, Any] = (
                {"default_values": form_default_values} if form_default_values else {}
            )
            if contract_name == "hospital_bag_form_create":
                workflow = await self._latest_hospital_bag_workflow(run=run)
                workflow_state = _dict(workflow, "state")
                if workflow_state:
                    creation_trusted_args["runtime_workflow_context"] = workflow_state
            return creation_trusted_args
        if contract_name == "hospital_bag_cart_update":
            client_groups = self._turn_state(run.id).hospital_bag_cart_groups
            if client_groups is not None:
                return {"groups": client_groups}
            groups = await self._latest_hospital_bag_cart_groups(run=run)
            return {"groups": groups} if groups is not None else {}
        if contract_name == "ibclc_consult_card_create":
            return {
                "trusted_current_user_text": self._turn_state(run.id).current_user_text,
                "trusted_previous_assistant_text": self._turn_state(run.id).previous_assistant_text,
            }
        if contract_name == CONVERSATION_HISTORY_IMAGE_LOAD_TOOL_NAME:
            return {"visible_image_urls": list(self._turn_state(run.id).visible_image_urls)}
        return {}

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
        loader = (
            getattr(self.repository, "get_latest_workflow_state_for_owner", None)
            if workflow_type == PREGNANCY_PLAN_WORKFLOW_TYPE
            else None
        )
        owner_scoped = callable(loader)
        if not owner_scoped:
            loader = getattr(self.repository, "get_latest_workflow_state_for_thread", None)
        if not callable(loader):
            return None
        lookup_args: dict[str, Any] = {
            "owner_user_id": run.actor_user_id,
            "workflow_type": workflow_type,
        }
        if not owner_scoped:
            lookup_args["thread_id"] = run.thread_id
        workflow = await loader(**lookup_args)
        if workflow is None:
            return None
        expires_at = workflow.expires_at
        if expires_at is not None:
            normalized_expires_at = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
            if normalized_expires_at <= datetime.now(timezone.utc):
                return None
        return cast(AgentWorkflowState, workflow)

    async def _completed_turn_workflow_finalization(
        self,
        *,
        run: AgentRun,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        loader = getattr(self.repository, "list_active_workflow_states_for_thread", None)
        if not callable(loader):
            return {}, {}, {}
        workflows = await loader(
            thread_id=run.thread_id,
            owner_user_id=run.actor_user_id,
            limit=10,
        )
        owner_loader = getattr(self.repository, "list_active_workflow_states_for_owner", None)
        if callable(owner_loader):
            owner_workflows = await owner_loader(
                owner_user_id=run.actor_user_id,
                workflow_type=PREGNANCY_PLAN_WORKFLOW_TYPE,
                limit=1,
            )
            known_ids = {workflow.id for workflow in workflows}
            workflows.extend(
                workflow
                for workflow in owner_workflows
                if workflow.id not in known_ids
            )
        requested_type = _text(self._turn_state(run.id).workflow_reply, "workflow_type")
        recovery_type = self._turn_state(run.id).workflow_reply_recovery_type
        guarded_types = self._turn_state(run.id).guarded_workflow_types
        preferred_types = [
            recovery_type,
            *(workflow.workflow_type for workflow in workflows if workflow.run_id == run.id),
            *reversed(guarded_types),
            requested_type,
            *(
                workflow.workflow_type
                for workflow in workflows
                if workflow.workflow_type == PREGNANCY_PLAN_WORKFLOW_TYPE
            ),
            *(workflow.workflow_type for workflow in workflows),
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
                accepts_structured_pregnancy_command = (
                    workflow.workflow_type == PREGNANCY_PLAN_WORKFLOW_TYPE
                    and workflow.status in {"collecting", "ready", "waiting", "paused"}
                )
                if not workflow_accepts_reply(workflow) and not accepts_structured_pregnancy_command:
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
            return workflow_reply, {}, {}
        projected = project_workflow_context(
            [selected_workflow],
            trusted_form_submissions=self._turn_state(run.id).trusted_form_submissions,
            checkup_attachment_count=self._turn_state(run.id).checkup_attachment_count,
            workflow_reply=workflow_reply,
        )
        workflow_prompt = (
            pregnancy_plan_workflow_context(
                dict(selected_workflow.state) if isinstance(selected_workflow.state, dict) else {},
                checkup_attachment_count=self._turn_state(run.id).checkup_attachment_count,
            )
            if selected_workflow.workflow_type == PREGNANCY_PLAN_WORKFLOW_TYPE
            else {}
        )
        return workflow_reply, dict(projected[0]) if projected else {}, workflow_prompt

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

    async def _pending_confirmation_action_from_tool(
        self,
        *,
        run: AgentRun,
    ) -> AgentAction | None:
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
                if self._turn_state(run.id).provider_text_delta_seen
                else "final_fallback"
                if self._turn_state(run.id).text_segment_count > 0
                else "none"
            ),
            text_segment_count=self._turn_state(run.id).text_segment_count,
            error_type=error_type,
        )

def _latest_assistant_text_before(*, messages: list[AgentMessage], before_sequence: int) -> str:
    return next(
        (_message_text(message) for message in reversed(messages) if message.sequence < before_sequence and message.role == "assistant"),
        "",
    )


def _workflow_is_active(workflow: AgentWorkflowState) -> bool:
    if workflow.status not in {"collecting", "ready", "waiting", "paused"}:
        return False
    expires_at = workflow.expires_at
    if expires_at is None:
        return True
    normalized = (
        expires_at
        if expires_at.tzinfo is not None
        else expires_at.replace(tzinfo=timezone.utc)
    )
    return normalized > datetime.now(timezone.utc)


def _current_message_context_position(
    *,
    item_refs: list[dict[str, Any]],
    items: list[dict[str, Any]],
    current_message_id: UUID,
) -> int:
    current_item_key = f"message:{current_message_id}"
    for item_ref in item_refs:
        if str(item_ref.get("item_key") or "") == current_item_key:
            position = item_ref.get("position")
            if isinstance(position, int) and not isinstance(position, bool):
                return max(0, min(position, len(items)))
    for position in range(len(items) - 1, -1, -1):
        if items[position].get("role") == "user":
            return position
    return len(items)


def _bounded_workflow_context(
    workflows: list[dict[str, Any]],
    *,
    max_estimated_tokens: int,
) -> list[dict[str, Any]]:
    bounded = [
        cast(
            dict[str, Any],
            _bounded_runtime_value(workflow, max_string_length=600),
        )
        for workflow in workflows[:5]
    ]
    if estimate_json_tokens(bounded) <= max_estimated_tokens:
        return bounded

    for workflow in bounded:
        completed = workflow.get("completed_followups")
        if isinstance(completed, list):
            workflow["completed_followups"] = completed[-2:]
        analysis = workflow.get("analysis")
        if isinstance(analysis, dict):
            focuses = analysis.get("focuses")
            workflow["analysis"] = {
                "stage": analysis.get("stage") if isinstance(analysis.get("stage"), dict) else {},
                "focuses": [
                    {
                        key: focus[key]
                        for key in ("id", "title")
                        if key in focus
                    }
                    for focus in focuses[:6]
                    if isinstance(focus, dict)
                ]
                if isinstance(focuses, list)
                else [],
            }
    bounded = cast(
        list[dict[str, Any]],
        _bounded_runtime_value(bounded, max_string_length=300),
    )
    if estimate_json_tokens(bounded) <= max_estimated_tokens:
        return bounded

    for workflow in bounded:
        workflow.pop("analysis", None)
        completed = workflow.get("completed_followups")
        if isinstance(completed, list):
            workflow["completed_followups"] = completed[-1:]
    bounded = cast(
        list[dict[str, Any]],
        _bounded_runtime_value(bounded, max_string_length=180),
    )
    while len(bounded) > 1 and estimate_json_tokens(bounded) > max_estimated_tokens:
        bounded.pop()
    if estimate_json_tokens(bounded) <= max_estimated_tokens:
        return bounded

    essential_keys = (
        "workflow_type",
        "status",
        "phase",
        "current_message_relation",
        "collected_facts",
        "current_input",
        "current_step",
        "editable_steps",
        "allowed_commands",
        "next_transition",
        "instruction",
    )
    minimal = [
        {
            key: workflow[key]
            for key in essential_keys
            if key in workflow
        }
        for workflow in bounded[:1]
    ]
    for max_string_length in (120, 60, 30):
        candidate = cast(
            list[dict[str, Any]],
            _bounded_runtime_value(
                minimal,
                max_string_length=max_string_length,
            ),
        )
        if estimate_json_tokens(candidate) <= max_estimated_tokens:
            return candidate
        minimal = candidate
    return []


def _bounded_runtime_value(
    value: Any,
    *,
    max_string_length: int,
) -> Any:
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bounded_runtime_value(
                item,
                max_string_length=max_string_length,
            )
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, list):
        return [
            _bounded_runtime_value(
                item,
                max_string_length=max_string_length,
            )
            for item in value[:12]
        ]
    if isinstance(value, str):
        return " ".join(value.split())[:max_string_length].strip()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_string_length]


def _model_input_sha256(model_input: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        model_input,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        _text(tool_call, "tool_name") == "pregnancy_plan_workflow"
        and _text(_dict(tool_call, "safe_args"), "command") == "submit_form"
        and _text(_dict(tool_call, "safe_output"), "status") in {"intake_analyzed", "intake_in_progress"}
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
        "pregnancy_plan_workflow",
        "plans_plan_delete_propose",
        "plans_task_update_propose",
        "plans_task_delete_propose",
        "hospital_bag_form_create",
        "hospital_bag_card_create",
        "hospital_bag_cart_update",
        "hospital_bag_pump_recommend",
    ),
    ServiceSkillId.MILK_MANAGEMENT: (
        "records_milk_status_read",
        "records_milk_summary_read",
        "records_milk_analysis_read",
        "records_growth_read",
        "records_feeding_record_propose",
        "records_feeding_record_delete_propose",
        "records_pumping_record_propose",
        "records_pumping_record_delete_propose",
        "records_growth_record_propose",
        "records_growth_record_update_propose",
        "records_growth_record_delete_propose",
        "plans_current_read",
        "plans_calendar_read",
        "plans_milk_plan_propose",
        "plans_task_complete_propose",
        "plans_task_create_propose",
        "notifications_milk_reminder_propose",
    ),
    ServiceSkillId.HEALTH_CONSULTATION: (
        "records_milk_status_read",
        "ibclc_consult_card_create",
    ),
    ServiceSkillId.EMOTION_SUPPORT: (),
    ServiceSkillId.DEVICE_GUIDANCE: (
        "devices_pump_status_read",
        "devices_guidance_read",
        "devices_unboxing_advance",
        "support_ticket_propose",
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
                "name": contract.name,
            }
        )
    return recommendations


def _user_context(*, current_message: AgentMessage, now: datetime) -> dict[str, Any]:
    content = current_message.content if isinstance(current_message.content, dict) else {}
    client_context = project_cozymate_client_context(content.get("client_context"))
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
            # Pregnancy-plan free text is a side conversation by default. Only
            # an explicit App workflow command may carry its reply cursor.
            if _text(reply, "workflow_type") == PREGNANCY_PLAN_WORKFLOW_TYPE:
                return {}
            return reply
        break
    return {}


def _workflow_command_for_turn(*, current_message: AgentMessage) -> dict[str, Any]:
    content = current_message.content if isinstance(current_message.content, dict) else {}
    client_context = content.get("client_context")
    if not isinstance(client_context, dict):
        return {}
    return normalize_workflow_command(client_context.get("workflow_command"))


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
        if (
            attachment_type == "image"
            and content_type in MODEL_IMAGE_CONTENT_TYPES
            and _text(attachment, "trust_source") == "owned_image_asset"
            and _text(attachment, "asset_id")
        ):
            count += 1
        elif attachment_type == "file" and content_type == "application/pdf" and _text(attachment, "file_id"):
            count += 1
    return count


def _pregnancy_workflow_command_final_text(safe_output: dict[str, Any]) -> str:
    required_response = _text(safe_output, "required_response")
    if required_response:
        return required_response
    status = _text(safe_output, "status")
    if safe_output.get("write_succeeded") is True:
        return "孕期计划已生成，并同步到「宝宝和我」。"
    if status == "action_failed":
        return "这次孕期计划没有生成或同步成功，你可以稍后重试。"
    if status == "form_created":
        return "请先填写下方的孕期基础信息表。提交后，我会按当前状态继续下一步。"
    if status == "pregnancy_plan_intake_abandoned":
        return "已结束这次孕期计划采集。以后需要时可以重新开始。"
    workflow_context = _dict(safe_output, "workflow_context")
    current_step = _dict(workflow_context, "current_step")
    question = _text(current_step, "question")
    if question:
        return question
    if _text(current_step, "id") == "generate_plan":
        return "信息已经确认，可以开始生成孕期计划。"
    return "孕期计划已更新，请按下方当前步骤继续。"


def _current_hospital_bag_cart_groups(message: AgentMessage) -> list[dict[str, Any]] | None:
    content = message.content if isinstance(message.content, dict) else {}
    client_context = project_cozymate_client_context(content.get("client_context"))
    cart = client_context.get("hospital_bag_cart")
    groups = cart.get("groups") if isinstance(cart, dict) else None
    return [dict(group) for group in groups if isinstance(group, dict)] if isinstance(groups, list) else None


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


def _pregnancy_workflow_runtime_context(*, workflow: dict[str, Any] | None = None) -> dict[str, Any]:
    context: dict[str, Any] = {}
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
