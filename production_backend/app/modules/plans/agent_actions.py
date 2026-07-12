from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from ..agent_runtime.actions.executor import AgentActionApplyResult, AgentApplicationEvent
from ..agent_runtime.models import AgentAction
from .service import PlansService


MILK_PLAN_CREATE_ACTION = "plans.milk_plan.create"
MILK_PLAN_CHANGED_EVENT = "milk_plan.changed"
PREGNANCY_PLAN_CREATE_ACTION = "pregnancy.plan.create"
PREGNANCY_PLAN_CHANGED_EVENT = "pregnancy_plan.changed"
PLAN_TASK_CREATE_ACTION = "plans.task.create"
PLAN_TASK_COMPLETE_ACTION = "plans.task.complete"
PLAN_TASK_UPDATE_ACTION = "plans.task.update"
PLAN_TASK_DELETE_ACTION = "plans.task.delete"
PLAN_DELETE_ACTION = "plans.plan.delete"
_MILK_PLAN_CHANGED_MAX_AFFECTED_DATES = 30


def _milk_plan_affected_dates(plan_payload: dict[str, Any]) -> list[str]:
    affected: set[date] = set()
    start_date = _event_date(plan_payload.get("start_date"))
    raw_days = plan_payload.get("days")
    days = raw_days if isinstance(raw_days, int) and not isinstance(raw_days, bool) else 1
    days = max(1, min(days, _MILK_PLAN_CHANGED_MAX_AFFECTED_DATES))
    if start_date is not None:
        affected.update(start_date + timedelta(days=offset) for offset in range(days))

    for collection_key in ("tasks", "reminders"):
        collection = plan_payload.get(collection_key)
        if not isinstance(collection, list):
            continue
        for item in collection[:40]:
            if not isinstance(item, dict):
                continue
            for key in ("date", "task_date", "remind_at", "scheduled_at"):
                parsed = _event_date(item.get(key))
                if parsed is not None:
                    affected.add(parsed)
                    break
    return [value.isoformat() for value in sorted(affected)[:_MILK_PLAN_CHANGED_MAX_AFFECTED_DATES]]


def _event_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


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
            application_events=(
                AgentApplicationEvent(
                    event_type=MILK_PLAN_CHANGED_EVENT,
                    payload={
                        "operation": "created",
                        "reason": "created",
                        "plan_id": str(plan.id),
                        "plan_type": plan.plan_type,
                        "source": "agent_action",
                        "affected_dates": _milk_plan_affected_dates(plan_payload),
                    },
                ),
            ),
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
            application_events=(
                AgentApplicationEvent(
                    event_type=PREGNANCY_PLAN_CHANGED_EVENT,
                    payload={
                        "operation": "created",
                        "plan_id": str(plan.id),
                        "plan_type": plan.plan_type,
                        "source": plan.source,
                    },
                ),
            ),
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


class PlanTaskUpdateActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        task_id = _required_uuid(payload, "task_id", "missing_task_id", "invalid_task_id")
        updates = _task_updates(payload)
        if not updates:
            raise PermanentJobError("missing_task_update")
        try:
            task = await self.service.update_task(
                owner_user_id=action.actor_user_id,
                task_id=task_id,
                updates=updates,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        return AgentActionApplyResult(
            resource_type="plan_task",
            resource_id=str(task.id),
            details={
                "status": task.status,
                "fields": sorted(updates),
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


class PlanTaskDeleteActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        task_id = _required_uuid(payload, "task_id", "missing_task_id", "invalid_task_id")
        try:
            await self.service.delete_task(
                owner_user_id=action.actor_user_id,
                task_id=task_id,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        return AgentActionApplyResult(
            resource_type="plan_task",
            resource_id=str(task_id),
            details={
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


class PlanDeleteActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        plan_id = _required_uuid(payload, "plan_id", "missing_plan_id", "invalid_plan_id")
        try:
            await self.service.delete_plan(
                owner_user_id=action.actor_user_id,
                plan_id=plan_id,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan_id),
            details={
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


def _task_updates(payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if "plan_id" in payload:
        updates["plan_id"] = _optional_uuid(payload, "plan_id", "invalid_plan_id")
    if "task_date" in payload:
        updates["task_date"] = _optional_date(payload, "task_date", "invalid_task_date")
    for key in ("task_time", "title", "description"):
        if key in payload:
            updates[key] = _text(payload, key)
    task_payload = payload.get("payload")
    if isinstance(task_payload, dict):
        updates["payload"] = task_payload
    return updates
