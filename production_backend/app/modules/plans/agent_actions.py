from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from ..agent_runtime.actions.outbox import AgentActionApplyResult
from ..agent_runtime.models import AgentAction
from .service import PlansService


MILK_PLAN_CREATE_ACTION = "plans.milk_plan.create"
PREGNANCY_PLAN_CREATE_ACTION = "pregnancy.plan.create"
PLAN_TASK_CREATE_ACTION = "plans.task.create"
PLAN_TASK_COMPLETE_ACTION = "plans.task.complete"


class MilkPlanCreateActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        title = _text(payload, "title")
        if not title:
            raise PermanentJobError("missing_plan_title")

        plan_payload = payload.get("payload")
        if not isinstance(plan_payload, dict):
            plan_payload = {}
        try:
            plan = await self.service.create_plan(
                owner_user_id=action.actor_user_id,
                plan_type="milk_management",
                title=title,
                summary=_text(payload, "summary"),
                source="agent_action",
                payload={
                    **plan_payload,
                    "agent_action_id": str(action.id),
                    "agent_run_id": str(action.run_id),
                },
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan.id),
            details={
                "plan_type": plan.plan_type,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


class PregnancyPlanCreateActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        title = _text(payload, "title")
        if not title:
            raise PermanentJobError("missing_plan_title")

        plan_payload = payload.get("payload")
        if not isinstance(plan_payload, dict):
            plan_payload = {}
        try:
            plan = await self.service.create_plan(
                owner_user_id=action.actor_user_id,
                plan_type="pregnancy",
                title=title,
                summary=_text(payload, "summary"),
                source="agent_action",
                payload={
                    **plan_payload,
                    "agent_action_id": str(action.id),
                    "agent_run_id": str(action.run_id),
                },
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan.id),
            details={
                "plan_type": plan.plan_type,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


class PlanTaskCreateActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        title = _text(payload, "title")
        if not title:
            raise PermanentJobError("missing_task_title")

        task_payload = payload.get("payload")
        if not isinstance(task_payload, dict):
            task_payload = {}
        try:
            task = await self.service.create_task(
                owner_user_id=action.actor_user_id,
                plan_id=_optional_uuid(payload, "plan_id", "invalid_plan_id"),
                task_date=_optional_date(payload, "task_date", "invalid_task_date"),
                task_time=_text(payload, "task_time"),
                title=title,
                description=_text(payload, "description"),
                payload={
                    **task_payload,
                    "agent_action_id": str(action.id),
                    "agent_run_id": str(action.run_id),
                },
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="plan_task",
            resource_id=str(task.id),
            details={
                "status": task.status,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


class PlanTaskCompleteActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        task_id = _required_uuid(payload, "task_id", "missing_task_id", "invalid_task_id")
        completed = _optional_bool(payload, "completed", default=True, code="invalid_completed")

        try:
            task = await self.service.set_task_completed(
                owner_user_id=action.actor_user_id,
                task_id=task_id,
                completed=completed,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="plan_task",
            resource_id=str(task.id),
            details={
                "status": task.status,
                "completed": completed,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _required_uuid(payload: dict[str, Any], key: str, missing_code: str, invalid_code: str) -> UUID:
    value = _text(payload, key)
    if not value:
        raise PermanentJobError(missing_code)
    try:
        return UUID(value)
    except ValueError as exc:
        raise PermanentJobError(invalid_code) from exc


def _optional_uuid(payload: dict[str, Any], key: str, code: str) -> UUID | None:
    value = _text(payload, key)
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise PermanentJobError(code) from exc


def _optional_date(payload: dict[str, Any], key: str, code: str) -> date | None:
    value = payload.get(key)
    if value in ("", None):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise PermanentJobError(code) from exc


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool, code: str) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    raise PermanentJobError(code)
