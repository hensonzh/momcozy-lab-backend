from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import UUID

from production_backend.app.core.errors import ApiError
from production_backend.app.core.metrics import RequestMetrics
from production_backend.app.infrastructure.object_storage.base import ObjectStorage
from production_backend.app.modules.agent_runtime.event_stream.sink import AgentEventSink
from production_backend.app.modules.agent_runtime.event_stream.transient import AgentTransientStream
from production_backend.app.modules.agent_runtime.event_semantics import with_tool_event_semantic
from production_backend.app.modules.agent_runtime.models import AgentToolCall
from production_backend.app.modules.agent_runtime.payloads import DEFAULT_MAX_INLINE_PAYLOAD_BYTES, maybe_externalize_json_payload
from production_backend.app.modules.agent_runtime.repository import AgentRuntimeRepository
from production_backend.app.modules.auth import CurrentUser, PermissionPolicy

from .contracts import ToolContract
from .output_policy import strip_instructional_tool_output_keys
from .registry import ToolContractRegistry
from .schemas import validate_tool_input


@dataclass(frozen=True)
class ToolHandlerContext:
    actor: CurrentUser
    run_id: UUID
    tool_name: str
    call_id: str
    args: dict[str, Any]


ToolHandler = Callable[[ToolHandlerContext], Awaitable[dict[str, Any]] | dict[str, Any]]
DEFERRED_AGENT_EVENTS_KEY = "_deferred_agent_events"
LOGGER = logging.getLogger("production_backend.agent_runtime.tools")


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_call: AgentToolCall
    safe_output: dict[str, Any]


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolContractRegistry,
        repository: AgentRuntimeRepository,
        permission_policy: PermissionPolicy | None = None,
        handlers: dict[str, ToolHandler] | None = None,
        event_sink: AgentEventSink | None = None,
        metrics: RequestMetrics | None = None,
        object_storage: ObjectStorage | None = None,
        max_inline_output_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
        transient_stream: AgentTransientStream | None = None,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.permission_policy = permission_policy or PermissionPolicy()
        self.handlers = handlers or {}
        self.event_sink = event_sink
        self.metrics = metrics
        self.object_storage = object_storage
        self.max_inline_output_bytes = max_inline_output_bytes
        self.transient_stream = transient_stream

    async def execute(
        self,
        *,
        actor: CurrentUser,
        run_id: UUID,
        tool_name: str,
        call_id: str,
        args: dict[str, Any],
        handler_override: ToolHandler | None = None,
    ) -> ToolExecutionResult:
        started_at = perf_counter()
        tool_call: AgentToolCall | None = None
        try:
            contract = self.registry.get(tool_name)
            self._authorize(actor=actor, contract=contract, args=args)
            validate_tool_input(schema_ref=contract.input_schema_ref, value=args)
            handler = handler_override or self.handlers.get(tool_name)
            if handler is None:
                raise ApiError(code="unsupported_operation", message="Tool handler is not configured.", status=501)
            run = await self.repository.get_run(run_id=run_id)
            if run is None:
                raise ApiError(code="not_found", message="Agent run not found.", status=404)

            tool_call = await self.repository.start_tool_call(
                run_id=run_id,
                tool_name=tool_name,
                call_id=call_id,
                safe_args=_safe_payload(args),
                started_at=_utcnow(),
            )
            started_payload = {
                "tool_call_id": str(tool_call.id),
                "tool_name": tool_name,
                "call_id": call_id,
                "label": _tool_event_label(tool_name),
                "safe_args": _safe_payload(args),
            }
            started_payload = with_tool_event_semantic(
                started_payload,
                event_type="tool.started",
                tool_name=tool_name,
                read_or_write=contract.read_or_write,
                requires_confirmation=contract.requires_confirmation,
            )
            await self._publish_optimistic_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.started",
                payload=started_payload,
            )
            await self._append_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type="tool.started",
                payload=started_payload,
            )
            result = await asyncio.wait_for(
                _maybe_await(
                    handler(
                        ToolHandlerContext(
                            actor=actor,
                            run_id=run_id,
                            tool_name=tool_name,
                            call_id=call_id,
                            args=args,
                        )
                    )
                ),
                timeout=contract.timeout_seconds,
            )
        except ApiError as exc:
            if tool_call is not None:
                await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code=exc.code)
                await self._append_tool_failed_event(tool_call=tool_call, error_code=exc.code)
            self._record(tool_name=tool_name, outcome="failed", error_code=exc.code, started_at=started_at)
            raise
        except TimeoutError as exc:
            if tool_call is not None:
                await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code="timeout")
                await self._append_tool_failed_event(tool_call=tool_call, error_code="timeout")
            self._record(tool_name=tool_name, outcome="failed", error_code="timeout", started_at=started_at)
            raise ApiError(code="timeout", message="Tool execution timed out.", status=504) from exc
        except Exception as exc:
            if tool_call is not None:
                await self.repository.fail_tool_call(tool_call=tool_call, completed_at=_utcnow(), error_code="tool_failed")
                await self._append_tool_failed_event(tool_call=tool_call, error_code="tool_failed")
            self._record(tool_name=tool_name, outcome="failed", error_code="tool_failed", started_at=started_at)
            raise ApiError(code="tool_failed", message="Tool execution failed.", status=500) from exc

        deferred_events = _extract_deferred_agent_events(result)
        safe_output = strip_instructional_tool_output_keys(_safe_payload(result))
        externalized_output = await maybe_externalize_json_payload(
            payload=safe_output,
            object_storage=self.object_storage,
            run_id=run.id,
            payload_kind="tool-outputs",
            key_suffix=str(tool_call.id),
            max_inline_bytes=self.max_inline_output_bytes,
        )
        completed = await self.repository.complete_tool_call(tool_call=tool_call, completed_at=_utcnow())
        output = await self.repository.create_tool_output(
            tool_call_id=completed.id,
            safe_output=externalized_output.inline_payload,
            raw_output_ref=externalized_output.raw_payload_ref,
        )
        completed_payload = {
            "tool_call_id": str(completed.id),
            "tool_output_id": str(output.id),
            "tool_name": completed.tool_name,
            "call_id": completed.call_id,
            "label": _tool_event_label(completed.tool_name),
            "safe_output": externalized_output.inline_payload,
        }
        completed_payload = with_tool_event_semantic(
            completed_payload,
            event_type="tool.completed",
            tool_name=completed.tool_name,
            safe_output=externalized_output.inline_payload,
            read_or_write=contract.read_or_write,
            requires_confirmation=contract.requires_confirmation,
        )
        await self._publish_optimistic_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.completed",
            payload=completed_payload,
        )
        await self._append_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.completed",
            payload=completed_payload,
        )
        for deferred_event in deferred_events:
            payload = dict(deferred_event["payload"])
            payload.setdefault("tool_call_id", str(completed.id))
            await self._append_tool_event(
                thread_id=run.thread_id,
                run_id=run.id,
                event_type=deferred_event["event_type"],
                payload=payload,
            )
        self._record(tool_name=tool_name, outcome="completed", error_code="", started_at=started_at)
        return ToolExecutionResult(tool_call=completed, safe_output=externalized_output.inline_payload)

    def _authorize(self, *, actor: CurrentUser, contract: ToolContract, args: dict[str, Any]) -> None:
        self.permission_policy.require_permission(actor, contract.required_permission)
        if contract.owner_scope == "actor" and args.get("owner_user_id") not in {None, "", str(actor.user_id), actor.user_id}:
            raise ApiError(code="owner_scope_violation", message="Tool arguments are outside the current user scope.", status=403)

    def _record(self, *, tool_name: str, outcome: str, error_code: str, started_at: float) -> None:
        if self.metrics is not None:
            self.metrics.record_agent_tool(
                tool_name=tool_name,
                outcome=outcome,
                error_code=error_code,
                duration_ms=(perf_counter() - started_at) * 1000,
            )

    async def _append_tool_failed_event(self, *, tool_call: AgentToolCall, error_code: str) -> None:
        run = await self.repository.get_run(run_id=tool_call.run_id)
        if run is None:
            return
        payload = {
            "tool_call_id": str(tool_call.id),
            "tool_name": tool_call.tool_name,
            "call_id": tool_call.call_id,
            "error_code": error_code,
            "label": _tool_event_label(tool_call.tool_name),
        }
        try:
            contract = self.registry.get(tool_call.tool_name)
            payload = with_tool_event_semantic(
                payload,
                event_type="tool.failed",
                tool_name=tool_call.tool_name,
                read_or_write=contract.read_or_write,
                requires_confirmation=contract.requires_confirmation,
            )
        except ApiError:
            payload = with_tool_event_semantic(
                payload,
                event_type="tool.failed",
                tool_name=tool_call.tool_name,
            )
        await self._publish_optimistic_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.failed",
            payload=payload,
        )
        await self._append_tool_event(
            thread_id=run.thread_id,
            run_id=run.id,
            event_type="tool.failed",
            payload=payload,
        )

    async def _append_tool_event(self, *, thread_id: UUID, run_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            await self.event_sink.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)
            return
        await self.repository.append_event(thread_id=thread_id, run_id=run_id, event_type=event_type, payload=payload)

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
            LOGGER.warning("Failed to publish optimistic agent tool event.", exc_info=True)


async def _maybe_await(value: Awaitable[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    if hasattr(value, "__await__"):
        return await value
    return value


def _extract_deferred_agent_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_events = result.pop(DEFERRED_AGENT_EVENTS_KEY, [])
    if not isinstance(raw_events, list):
        return []
    events: list[dict[str, Any]] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        payload = item.get("payload")
        if event_type and isinstance(payload, dict):
            events.append({"event_type": event_type, "payload": payload})
    return events


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redacted_value(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    return value


def _redacted_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("authorization", "password", "secret", "token", "api_key")):
        return "[redacted]"
    return _safe_payload(value)


def _tool_live_dedupe_key(*, run_id: UUID, event_type: str, payload: dict[str, Any]) -> str:
    if event_type not in {"tool.started", "tool.completed", "tool.failed"}:
        return ""
    tool_call_id = str(payload.get("tool_call_id") or payload.get("call_id") or "").strip()
    return f"{run_id}:{event_type}:{tool_call_id}" if tool_call_id else ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tool_event_label(tool_name: str) -> str:
    return {
        "load_service_skill": "加载服务技能",
        "profile.read": "个人资料",
        "profile_update": "更新个人资料",
        "business.context.read": "业务上下文",
        "records.milk_summary.read": "奶量摘要",
        "records.milk_status.read": "奶量状态",
        "records.feeding_record.propose": "喂养记录草稿",
        "records.pumping_record.propose": "吸奶记录草稿",
        "plans.current.read": "计划信息",
        "plans.milk_plan.propose": "泌乳计划草稿",
        "plans.task_create.propose": "任务草稿",
        "plans.task_complete.propose": "任务状态",
        "pregnancy.plan_context.read": "孕期计划上下文",
        "pregnancy.plan_create.propose": "孕期计划草稿",
        "diary.recent.read": "近期日记",
        "diary.entry_upsert.propose": "日记草稿",
        "devices.pump_status.read": "设备状态",
        "devices.guidance_assets.read": "设备指导资料",
        "files.vision_summary.read": "图片内容",
        "birth_plan_form_create": "我先帮你准备确认内容～",
        "labor_communication_card_create": "我先帮你整理分娩沟通单～",
        "birth_journey_plan_card_create": "我先帮你整理孕期计划～",
        "hospital_bag_form_create": "我先帮你准备确认内容～",
        "hospital_bag_card_create": "我先帮你整理待产包清单～",
        "hospital_bag_cart_update": "我先帮你调整待产包购物车～",
        "hospital_bag_pump_recommend": "我先帮你看看吸奶器型号～",
        "notifications.milk_reminder.propose": "奶量提醒草稿",
        "memory.create.propose": "长期记忆草稿",
        "support.ticket.propose": "售后工单草稿",
    }.get(tool_name, "相关信息")
