from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.agent_runtime.runs.service import AgentRuntimeService
from app.agent_runtime.tools.executor import ToolHandlerContext
from app.agent_runtime.tools.result import ToolResult
from app.agents.cozymate.actions.plans import PLAN_UPDATE_ACTION
from app.core.errors import ApiError
from app.modules.plans.service import PlansService

from .base import _StandardToolHandler
from .milk import MilkPlanProposeToolHandler
from .plans_diary import PlanDeleteProposeToolHandler, PregnancyPlanProposeToolHandler
from .shared import (
    _limit,
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
        include_content = context.args.get("include_content") is True
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
                "plan": _plan_item(plan, include_content=include_content),
                "plans": [],
                "count": 1,
                "truncated": False,
            }
        if mode != "list":
            raise ApiError(code="validation_failed", message="mode must be list or detail.", status=422)
        limit = _limit(context.args.get("limit"), default=20, max_limit=50)
        plans = await self.plans_service.list_plans(
            owner_user_id=context.actor.user_id,
            plan_type=_text(context.args, "plan_type"),
            status=_text(context.args, "status") or "active",
            limit=limit,
        )
        return {
            "status": "plans_read",
            "mode": "list",
            "plan": None,
            "plans": [_plan_item(plan, include_content=include_content) for plan in plans],
            "count": len(plans),
            "truncated": len(plans) >= limit,
        }


class PlanMutateToolHandler(_StandardToolHandler):
    def __init__(
        self,
        *,
        runtime_service: AgentRuntimeService,
        plans_service: PlansService,
        milk_create_handler: Any | None = None,
        pregnancy_create_handler: Any | None = None,
        delete_handler: Any | None = None,
    ) -> None:
        self.runtime_service = runtime_service
        self.plans_service = plans_service
        self.milk_create_handler = milk_create_handler or MilkPlanProposeToolHandler(
            runtime_service=runtime_service,
            plans_service=plans_service,
        )
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
        handlers = {
            "milk_management": self.milk_create_handler,
            "pregnancy": self.pregnancy_create_handler,
        }
        handler = handlers.get(plan_type)
        if handler is None:
            raise ApiError(
                code="unsupported_plan_type",
                message="Plan creation currently supports milk_management and pregnancy.",
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
        type_hint = _text(context.args, "plan_type")
        if type_hint and type_hint != plan_type:
            raise ApiError(
                code="plan_type_mismatch",
                message="plan_type does not match the owner-scoped plan.",
                status=409,
            )
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
        return _proposal_result(action=action, preview_payload=preview_payload)


def _plan_item(plan: Any, *, include_content: bool) -> dict[str, Any]:
    return {
        "plan_id": str(plan.id),
        "plan_type": str(plan.plan_type or ""),
        "title": str(plan.title or ""),
        "summary": str(plan.summary or ""),
        "status": str(plan.status or ""),
        "source": str(plan.source or ""),
        "version": int(plan.version),
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
        audit_output = dict(result.audit_output or {})
        audit_output.update(
            {
                "operation": operation,
                "plan_type": plan_type,
                "plan_id": str(plan_id) if plan_id is not None else None,
            }
        )
        return ToolResult(output=result.output, audit_output=audit_output)
    output = {
        **result,
        "operation": operation,
        "plan_type": plan_type,
        "plan_id": str(plan_id) if plan_id is not None else None,
    }
    if not _text(output, "status"):
        if output.get("write_succeeded") is True:
            output["status"] = "plan_change_applied"
        elif output.get("requires_confirmation") is True:
            output["status"] = "plan_change_pending_confirmation"
        else:
            output["status"] = "plan_change_proposed"
    return output


def _datetime_iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
