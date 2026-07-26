from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import DEFERRED_AGENT_EVENTS_KEY, ToolHandlerContext
from app.agents.cozymate.actions.plans import (
    PLAN_TASK_COMPLETE_ACTION,
    PLAN_TASK_CREATE_ACTION,
    PLAN_TASK_DELETE_ACTION,
    PLAN_TASK_UPDATE_ACTION,
)
from app.core.errors import ApiError
from app.modules.plans.schedule_domain import (
    SCHEDULE_DOMAINS,
    SCHEDULE_DOMAIN_ORDER,
    ScheduleDomain,
    schedule_domain_for_plan_type,
    schedule_domain_for_task,
)
from app.modules.plans.schedule_timeline import ScheduleTimelineService
from app.modules.plans.service import PlansService

from .base import _StandardToolHandler
from .milk import LactationRecordMutationHandler, MilkScheduleRescheduleProposeToolHandler
from .shared import (
    _limit,
    _optional_date_arg,
    _optional_uuid_arg,
    _plan_task_complete_apply_payload,
    _plan_task_complete_preview_payload,
    _plan_task_create_apply_payload,
    _plan_task_create_preview_payload,
    _plan_task_delete_apply_payload,
    _plan_task_delete_preview_payload,
    _plan_task_update_apply_payload,
    _plan_task_update_fields,
    _plan_task_update_preview_payload,
    _proposal_result,
    _text,
)


class ScheduleTimelineReadToolHandler(_StandardToolHandler):
    def __init__(self, *, service: ScheduleTimelineService) -> None:
        self.service = service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        as_of_date = _optional_date_arg(context.args, "runtime_local_date") or datetime.now(timezone.utc).date()
        start_date = _optional_date_arg(context.args, "start_date") or as_of_date - timedelta(days=7)
        end_date = _optional_date_arg(context.args, "end_date") or as_of_date + timedelta(days=7)
        domains = _filter_values(
            context.args.get("domains"),
            default=SCHEDULE_DOMAIN_ORDER,
            field_name="domains",
        )
        states = _filter_values(
            context.args.get("states"),
            default=(),
            field_name="states",
        )
        output = await self.service.read(
            owner_user_id=context.actor.user_id,
            as_of_date=as_of_date,
            start_date=start_date,
            end_date=end_date,
            timezone_name=_text(context.args, "runtime_timezone") or "UTC",
            limit=_limit(context.args.get("limit"), default=50, max_limit=50),
            domains=domains,
            states=states,
        )
        return output.model_dump(mode="json")


class ScheduleTimelineMutateToolHandler(_StandardToolHandler):
    def __init__(self, *, runtime_service: AgentRuntimeService, plans_service: PlansService) -> None:
        self.runtime_service = runtime_service
        self.plans_service = plans_service
        self.record_handler = LactationRecordMutationHandler(runtime_service=runtime_service)

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        operation = _text(context.args, "operation")
        if operation not in {"create", "update", "delete", "set_status", "reschedule"}:
            raise ApiError(code="validation_failed", message="Unsupported schedule operation.", status=422)
        entry_type = _text(context.args, "entry_type")
        if entry_type not in {"schedule", "execution"}:
            raise ApiError(code="validation_failed", message="entry_type must be schedule or execution.", status=422)
        if entry_type == "execution":
            return await self._mutate_execution(context=context, operation=operation)
        if operation == "create":
            result, domain = await self._create(context)
        elif operation == "reschedule":
            result, domain = await self._reschedule(context)
        else:
            result, domain = await self._change_existing(context=context, operation=operation)
        output = _schedule_mutate_result(result=result, operation=operation, domain=domain)
        output["entry_type"] = "schedule"
        return output

    async def _mutate_execution(
        self,
        *,
        context: ToolHandlerContext,
        operation: str,
    ) -> dict[str, Any]:
        if operation not in {"create", "update", "delete"}:
            raise ApiError(
                code="unsupported_operation",
                message="Execution entries support only create, update, and delete.",
                status=422,
            )
        result = await self.record_handler.execute(context)
        return {
            **result,
            "entry_type": "execution",
            "domain": "lactation",
        }

    async def _create(self, context: ToolHandlerContext) -> tuple[dict[str, Any], str]:
        domain = _required_schedule_domain(context.args)
        plan_id = _optional_uuid_arg(context.args, "plan_id")
        if plan_id is not None:
            plan = await self.plans_service.get_plan(
                owner_user_id=context.actor.user_id,
                plan_id=plan_id,
            )
            plan_domain = schedule_domain_for_plan_type(plan.plan_type)
            if plan_domain != domain:
                raise ApiError(
                    code="validation_failed",
                    message="Schedule domain must match the selected plan.",
                    status=422,
                )
        event_type = _text(context.args, "event_type") or ("other" if domain == "lactation" else "task")
        _validate_event_type(domain=domain, event_type=event_type)
        payload = _schedule_payload(
            existing={},
            domain=domain,
            event_type=event_type,
        )
        apply_payload = _plan_task_create_apply_payload(
            {
                "plan_id": str(plan_id) if plan_id is not None else "",
                "task_date": _text(context.args, "task_date"),
                "task_time": _text(context.args, "task_time"),
                "title": _text(context.args, "title"),
                "description": _text(context.args, "description"),
                "payload": payload,
            }
        )
        if not all(_text(apply_payload, key) for key in ("task_date", "title")):
            raise ApiError(
                code="validation_failed",
                message="task_date and title are required.",
                status=422,
            )
        result = await self._propose_action(
            context=context,
            action_type=PLAN_TASK_CREATE_ACTION,
            target_id="",
            apply_payload=apply_payload,
            preview_payload=_plan_task_create_preview_payload(apply_payload),
            key_suffix="schedule-create",
        )
        return result, domain

    async def _change_existing(
        self,
        *,
        context: ToolHandlerContext,
        operation: str,
    ) -> tuple[dict[str, Any], str]:
        task_id = _optional_uuid_arg(context.args, "task_id")
        if task_id is None:
            raise ApiError(code="validation_failed", message="task_id is required.", status=422)
        task = await self.plans_service.get_task(
            owner_user_id=context.actor.user_id,
            task_id=task_id,
        )
        payload = dict(task.payload) if isinstance(task.payload, dict) else {}
        current_plan = None
        if task.plan_id is not None:
            current_plan = await self.plans_service.get_plan(
                owner_user_id=context.actor.user_id,
                plan_id=task.plan_id,
            )
        domain = schedule_domain_for_task(
            plan_type=current_plan.plan_type if current_plan is not None else None,
            payload=payload,
        )
        _validate_domain_hint(args=context.args, actual_domain=domain)

        if operation == "update":
            update_args: dict[str, Any] = {
                "task_id": str(task_id),
                **{
                    key: context.args[key]
                    for key in ("task_date", "task_time", "title", "description")
                    if key in context.args
                },
            }
            requested_plan_id = _optional_uuid_arg(context.args, "plan_id")
            if requested_plan_id is not None:
                target_plan = await self.plans_service.get_plan(
                    owner_user_id=context.actor.user_id,
                    plan_id=requested_plan_id,
                )
                if schedule_domain_for_plan_type(target_plan.plan_type) != domain:
                    raise ApiError(
                        code="unsupported_operation",
                        message="A schedule task cannot be moved across domains.",
                        status=422,
                    )
                update_args["plan_id"] = str(requested_plan_id)
            if "event_type" in context.args:
                event_type = _text(context.args, "event_type")
                _validate_event_type(domain=domain, event_type=event_type)
                update_args["payload"] = _schedule_payload(
                    existing=payload,
                    domain=domain,
                    event_type=event_type,
                )
            apply_payload = _plan_task_update_apply_payload(update_args)
            if not _plan_task_update_fields(apply_payload):
                raise ApiError(
                    code="validation_failed",
                    message="At least one schedule update field is required.",
                    status=422,
                )
            result = await self._propose_action(
                context=context,
                action_type=PLAN_TASK_UPDATE_ACTION,
                target_id=str(task_id),
                apply_payload=apply_payload,
                preview_payload=_plan_task_update_preview_payload(apply_payload),
                key_suffix="schedule-update",
            )
            return result, domain

        if operation == "set_status":
            if not isinstance(context.args.get("completed"), bool):
                raise ApiError(
                    code="validation_failed",
                    message="completed is required for set_status.",
                    status=422,
                )
            event_type = _text(payload, "event_type") or _text(payload, "task_type")
            if domain == "lactation" and context.args["completed"] is True and event_type in {"feeding", "pumping"}:
                return (
                    await self._complete_lactation_task(
                        context=context,
                        task_id=task_id,
                        record_type=event_type,
                    ),
                    domain,
                )
            if domain == "lactation" and context.args["completed"] is False and event_type in {"feeding", "pumping"}:
                raise ApiError(
                    code="unsupported_operation",
                    message=(
                        "Delete the linked execution record to restore a completed lactation task; "
                        "do not clear the task status independently."
                    ),
                    status=422,
                )
            apply_payload = _plan_task_complete_apply_payload(
                {
                    "task_id": str(task_id),
                    "completed": context.args.get("completed", True),
                }
            )
            result = await self._propose_action(
                context=context,
                action_type=PLAN_TASK_COMPLETE_ACTION,
                target_id=str(task_id),
                apply_payload=apply_payload,
                preview_payload=_plan_task_complete_preview_payload(apply_payload),
                key_suffix="schedule-status",
            )
            return result, domain

        if operation == "delete":
            apply_payload = _plan_task_delete_apply_payload(
                {
                    "task_id": str(task_id),
                    "reason": _text(context.args, "reason"),
                }
            )
            result = await self._propose_action(
                context=context,
                action_type=PLAN_TASK_DELETE_ACTION,
                target_id=str(task_id),
                apply_payload=apply_payload,
                preview_payload=_plan_task_delete_preview_payload(apply_payload),
                key_suffix="schedule-delete",
            )
            return result, domain

        raise ApiError(code="validation_failed", message="Unsupported schedule operation.", status=422)

    async def _complete_lactation_task(
        self,
        *,
        context: ToolHandlerContext,
        task_id: UUID,
        record_type: str,
    ) -> dict[str, Any]:
        measurement_field = {
            "feeding": "volume_ml",
            "pumping": "milk_volume_ml",
        }[record_type]
        if not _is_non_negative_number(context.args.get(measurement_field)):
            raise ApiError(
                code="validation_failed",
                message=f"{measurement_field} is required when completing a {record_type} task.",
                status=422,
            )
        if not _text(context.args, "occurred_at"):
            raise ApiError(
                code="validation_failed",
                message="occurred_at is required when completing a lactation task.",
                status=422,
            )
        if record_type == "feeding" and not _text(context.args, "feed_type"):
            raise ApiError(
                code="validation_failed",
                message="feed_type is required when completing a feeding task.",
                status=422,
            )
        record_context = ToolHandlerContext(
            actor=context.actor,
            run_id=context.run_id,
            tool_name=context.tool_name,
            call_id=context.call_id,
            args={
                **context.args,
                "operation": "create",
                "record_type": record_type,
                "plan_task_id": str(task_id),
            },
            thread_id=context.thread_id,
        )
        return await self.record_handler.execute(record_context)

    async def _reschedule(self, context: ToolHandlerContext) -> tuple[dict[str, Any], str]:
        if _optional_uuid_arg(context.args, "task_id") is not None:
            update_context = ToolHandlerContext(
                actor=context.actor,
                run_id=context.run_id,
                tool_name=context.tool_name,
                call_id=context.call_id,
                args={
                    **context.args,
                    "operation": "update",
                },
                thread_id=context.thread_id,
            )
            return await self._change_existing(context=update_context, operation="update")

        plan_id = _optional_uuid_arg(context.args, "plan_id")
        if plan_id is None:
            raise ApiError(
                code="validation_failed",
                message="task_id or plan_id is required for reschedule.",
                status=422,
            )
        plan = await self.plans_service.get_plan(
            owner_user_id=context.actor.user_id,
            plan_id=plan_id,
        )
        domain = schedule_domain_for_plan_type(plan.plan_type)
        _validate_domain_hint(args=context.args, actual_domain=domain)
        if domain != "lactation":
            raise ApiError(
                code="unsupported_operation",
                message="Batch conflict-aware reschedule is currently supported only for lactation plans.",
                status=422,
            )
        result = await MilkScheduleRescheduleProposeToolHandler(
            runtime_service=self.runtime_service,
            plans_service=self.plans_service,
        ).execute(context)
        return result, domain

    async def _propose_action(
        self,
        *,
        context: ToolHandlerContext,
        action_type: str,
        target_id: str,
        apply_payload: dict[str, Any],
        preview_payload: dict[str, Any],
        key_suffix: str,
    ) -> dict[str, Any]:
        action = await self.runtime_service.propose_action(
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=action_type,
            target_type="plan_task",
            target_id=target_id,
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_text(context.args, "idempotency_key")
            or f"{context.run_id}:{context.call_id}:{key_suffix}",
        )
        return _proposal_result(action=action, preview_payload=preview_payload)


def _required_schedule_domain(args: dict[str, Any]) -> ScheduleDomain:
    domain = _text(args, "domain")
    if domain not in SCHEDULE_DOMAINS:
        raise ApiError(code="validation_failed", message="A valid schedule domain is required.", status=422)
    return cast(ScheduleDomain, domain)


def _validate_domain_hint(*, args: dict[str, Any], actual_domain: ScheduleDomain) -> None:
    requested_domain = _text(args, "domain")
    if requested_domain and requested_domain != actual_domain:
        raise ApiError(
            code="validation_failed",
            message="Schedule domain does not match the existing task or plan.",
            status=422,
        )


def _validate_event_type(*, domain: str, event_type: str) -> None:
    if not event_type:
        raise ApiError(code="validation_failed", message="event_type cannot be empty.", status=422)
    if domain == "lactation" and event_type not in {"feeding", "pumping", "other"}:
        raise ApiError(
            code="validation_failed",
            message="Lactation schedule event_type must be feeding, pumping, or other.",
            status=422,
        )


def _schedule_payload(*, existing: dict[str, Any], domain: str, event_type: str) -> dict[str, Any]:
    payload = {
        **existing,
        "domain": domain,
        "event_type": event_type,
    }
    if domain == "lactation":
        payload["task_type"] = event_type
    return payload


def _schedule_mutate_result(
    *,
    result: dict[str, Any],
    operation: str,
    domain: str,
) -> dict[str, Any]:
    write_succeeded = result.get("write_succeeded") is True
    requires_confirmation = result.get("requires_confirmation") is True
    raw_status = _text(result, "status")
    if raw_status == "action_failed":
        status = "action_failed"
    elif raw_status == "milk_schedule_no_changes":
        status = "schedule_no_changes"
    elif write_succeeded:
        status = "schedule_change_applied"
    elif requires_confirmation:
        status = "schedule_change_pending_confirmation"
    else:
        status = "schedule_change_proposed"
    output: dict[str, Any] = {
        "status": status,
        "operation": operation,
        "domain": domain,
        "requires_confirmation": requires_confirmation,
        "confirmation_policy": _text(result, "confirmation_policy") or "explicit_intent",
        "user_visible": result.get("user_visible") is True,
        "write_succeeded": write_succeeded,
        "preview_payload": (
            dict(result["preview_payload"])
            if isinstance(result.get("preview_payload"), dict)
            else {}
        ),
    }
    for key in (
        "action_id",
        "action_type",
        "action_status",
        "error_code",
        "artifact_id",
        "artifact_type",
        "plan_id",
        "record_type",
        "conflict_count",
        "updated_count",
        "affected_dates",
        DEFERRED_AGENT_EVENTS_KEY,
    ):
        if key in result:
            output[key] = result[key]
    return output


def _is_non_negative_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def _filter_values(
    value: Any,
    *,
    default: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ApiError(code="validation_failed", message=f"{field_name} must be a non-empty string list.", status=422)
    return tuple(dict.fromkeys(item.strip().lower() for item in value))
