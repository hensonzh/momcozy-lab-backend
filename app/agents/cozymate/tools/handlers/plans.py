from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.agent_runtime.tools.result import ToolResult
from app.agents.cozymate.actions.plans import PLAN_UPDATE_ACTION
from app.core.errors import ApiError
from app.modules.plans.service import PlansService

from .base import _StandardToolHandler
from .plans_diary import PlanDeleteProposeToolHandler, PregnancyPlanProposeToolHandler
from .shared import (
    _limit,
    _optional_date_arg,
    _optional_uuid_arg,
    _proposal_result,
    _propose_action_reusing_idempotency,
    _stable_payload_key,
    _text,
)


class PlanReadToolHandler(_StandardToolHandler):
    def __init__(self, *, plans_service: PlansService) -> None:
        self.plans_service = plans_service

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any]:
        mode = _text(context.args, "mode") or "list"
        if mode == "detail":
            plan_id = _optional_uuid_arg(context.args, "plan_id")
            if plan_id is None:
                raise ApiError(code="validation_failed", message="plan_id is required for detail mode.", status=422)
            plan = await self.plans_service.get_plan(
                owner_user_id=context.actor.user_id,
                plan_id=plan_id,
            )
            return {
                "status": "plan_read",
                "mode": "detail",
                "plan": _plan_item(plan, include_content=True),
                "plans": [],
                "count": 1,
                "truncated": False,
            }
        if mode != "list":
            raise ApiError(code="validation_failed", message="mode must be list or detail.", status=422)
        limit = _limit(context.args.get("limit"), default=20, max_limit=20)
        plans = await self.plans_service.list_plans(
            owner_user_id=context.actor.user_id,
            plan_type=_text(context.args, "plan_type"),
            status="active",
            as_of_date=_optional_date_arg(context.args, "runtime_local_date"),
            limit=limit,
        )
        return {
            "status": "plans_read",
            "mode": "list",
            "plan": None,
            "plans": [_plan_item(plan, include_content=False) for plan in plans],
            "count": len(plans),
            "truncated": len(plans) >= limit,
        }


class PlanMutateToolHandler(_StandardToolHandler):
    def __init__(
        self,
        *,
        runtime_service: AgentRuntimeService,
        plans_service: PlansService,
        pregnancy_create_handler: Any | None = None,
        delete_handler: Any | None = None,
    ) -> None:
        self.runtime_service = runtime_service
        self.plans_service = plans_service
        self.pregnancy_create_handler = pregnancy_create_handler or PregnancyPlanProposeToolHandler(
            runtime_service=runtime_service,
        )
        self.delete_handler = delete_handler or PlanDeleteProposeToolHandler(
            runtime_service=runtime_service,
            plans_service=plans_service,
        )

    async def execute(self, context: ToolHandlerContext) -> dict[str, Any] | ToolResult:
        operation = _text(context.args, "operation")
        if operation == "create":
            return await self._create(context)
        if operation in {"update", "delete"}:
            return await self._change_existing(context=context, operation=operation)
        raise ApiError(code="unsupported_operation", message="Unsupported plan operation.", status=422)

    async def _create(self, context: ToolHandlerContext) -> dict[str, Any] | ToolResult:
        plan_type = _text(context.args, "plan_type")
        if not plan_type:
            raise ApiError(
                code="validation_failed",
                message="plan_type is required for plan creation.",
                status=422,
            )
        handlers = {"pregnancy": self.pregnancy_create_handler}
        handler = handlers.get(plan_type)
        if handler is None:
            raise ApiError(
                code="unsupported_plan_type",
                message="Plan creation currently supports pregnancy.",
                status=422,
            )
        result = await handler.execute(context)
        return _plan_mutate_result(
            result=result,
            operation="create",
            plan_type=plan_type,
            plan_id=None,
        )

    async def _change_existing(
        self,
        *,
        context: ToolHandlerContext,
        operation: str,
    ) -> dict[str, Any]:
        plan_id = _optional_uuid_arg(context.args, "plan_id")
        if plan_id is None:
            raise ApiError(code="validation_failed", message="plan_id is required.", status=422)
        plan = await self.plans_service.get_plan(
            owner_user_id=context.actor.user_id,
            plan_id=plan_id,
        )
        plan_type = str(plan.plan_type or "").strip()
        if operation == "delete":
            result = await self.delete_handler.execute(context)
        else:
            result = await self._update(context=context, plan_id=plan_id)
        normalized = _plan_mutate_result(
            result=result,
            operation=operation,
            plan_type=plan_type,
            plan_id=plan_id,
        )
        if isinstance(normalized, ToolResult):
            raise TypeError("Existing plan mutations must return structured output.")
        return normalized

    async def _update(
        self,
        *,
        context: ToolHandlerContext,
        plan_id: UUID,
    ) -> dict[str, Any]:
        expected_version = context.args.get("expected_version")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
            raise ApiError(code="validation_failed", message="expected_version is required for update.", status=422)
        apply_payload: dict[str, Any] = {
            "plan_id": str(plan_id),
            "expected_version": expected_version,
        }
        if "title" in context.args:
            title = _text(context.args, "title")
            if not title:
                raise ApiError(code="validation_failed", message="title cannot be empty.", status=422)
            apply_payload["title"] = title
        if "summary" in context.args:
            summary = context.args.get("summary")
            if not isinstance(summary, str):
                raise ApiError(code="validation_failed", message="summary must be a string.", status=422)
            apply_payload["summary"] = summary.strip()
        if set(apply_payload) == {"plan_id", "expected_version"}:
            raise ApiError(
                code="validation_failed",
                message="title or summary is required for update.",
                status=422,
            )
        preview_payload = {
            "plan_id": str(plan_id),
            "expected_version": expected_version,
            "fields": sorted(set(apply_payload) - {"plan_id", "expected_version"}),
        }
        action = await _propose_action_reusing_idempotency(
            self.runtime_service,
            owner_user_id=context.actor.user_id,
            run_id=context.run_id,
            action_type=PLAN_UPDATE_ACTION,
            target_type="plan",
            target_id=str(plan_id),
            side_effect_level="medium",
            preview_payload=preview_payload,
            apply_payload=apply_payload,
            idempotency_key=_stable_payload_key(
                f"{context.run_id}:plan-update",
                apply_payload,
            ),
        )
        return _proposal_result(
            action=action,
            preview_payload=preview_payload,
            include_apply_result=True,
        )


def _plan_item(plan: Any, *, include_content: bool) -> dict[str, Any]:
    return {
        "plan_id": str(plan.id),
        "plan_type": str(plan.plan_type or ""),
        "title": str(plan.title or ""),
        "summary": str(plan.summary or ""),
        "status": str(plan.status or ""),
        "source": str(plan.source or ""),
        "version": int(plan.version),
        "starts_on": _date_iso(getattr(plan, "starts_on", None)),
        "ends_on": _date_iso(getattr(plan, "ends_on", None)),
        "created_at": _datetime_iso(getattr(plan, "created_at", None)),
        "updated_at": _datetime_iso(getattr(plan, "updated_at", None)),
        "content": _safe_plan_content(
            plan_type=str(plan.plan_type or ""),
            payload=plan.payload if isinstance(plan.payload, dict) else {},
        )
        if include_content
        else {},
    }


def _safe_plan_content(*, plan_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if plan_type == "milk_management":
        allowed = (
            "direction",
            "start_date",
            "days",
            "goal",
            "strategy_summary",
            "checkpoints",
            "observation_items",
            "safety_notes",
        )
        return {key: payload[key] for key in allowed if key in payload}
    if plan_type == "pregnancy":
        card = payload.get("card")
        return {"card": card} if isinstance(card, dict) else {}
    return {}


def _plan_mutate_result(
    *,
    result: dict[str, Any] | ToolResult,
    operation: str,
    plan_type: str,
    plan_id: UUID | None,
) -> dict[str, Any] | ToolResult:
    if isinstance(result, ToolResult):
        canonical_output = _normalized_plan_mutate_output(
            output=dict(result.canonical_output),
            operation=operation,
            plan_type=plan_type,
            plan_id=plan_id,
        )
        return ToolResult(
            canonical_output=canonical_output,
            supplemental_content=result.supplemental_content,
            serialization=result.serialization,
            deferred_events=result.deferred_events,
        )
    return _normalized_plan_mutate_output(
        output=dict(result),
        operation=operation,
        plan_type=plan_type,
        plan_id=plan_id,
    )


def _normalized_plan_mutate_output(
    *,
    output: dict[str, Any],
    operation: str,
    plan_type: str,
    plan_id: UUID | None,
) -> dict[str, Any]:
    original_status = _text(output, "status")
    action_status = _text(output, "action_status")
    resource_type = _text(output, "resource_type")
    resource_id = _text(output, "resource_id")
    result_details = output.get("result_details")
    details = dict(result_details) if isinstance(result_details, dict) else {}
    resolved_plan_id = str(plan_id) if plan_id is not None else None
    if plan_id is None and resource_type == "plan" and resource_id:
        resolved_plan_id = resource_id
    version = details.get("version")
    plan_version = (
        version
        if isinstance(version, int) and not isinstance(version, bool) and version >= 1
        else None
    )
    if output.get("task_count") is None:
        task_count = details.get("task_count")
        if isinstance(task_count, int) and not isinstance(task_count, bool) and task_count >= 0:
            output["task_count"] = task_count
    output.update(
        {
            "status": _plan_mutate_status(
                action_status=action_status,
                original_status=original_status,
                output=output,
            ),
            "result_code": f"action_{action_status}" if action_status else original_status or "input_required",
            "operation": operation,
            "plan_type": plan_type,
            "plan_id": resolved_plan_id,
            "plan_version": plan_version,
        }
    )
    return output


def _plan_mutate_status(
    *,
    action_status: str,
    original_status: str,
    output: dict[str, Any],
) -> str:
    if action_status == "applied" or output.get("write_succeeded") is True:
        return "applied"
    if action_status == "confirmation_required" or output.get("requires_confirmation") is True:
        return "confirmation_required"
    if action_status == "failed" or output.get("error_code"):
        return "failed"
    if output.get("blocks_plan_flow") is True or original_status == "urgent_care_required":
        return "blocked"
    return "input_required"


def _datetime_iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _date_iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, date) and not isinstance(value, datetime) else None
