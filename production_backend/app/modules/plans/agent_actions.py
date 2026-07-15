from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from ..agent_runtime.actions.executor import AgentActionApplyResult, AgentApplicationEvent
from ..agent_runtime.models import AgentAction
from .service import PlansService
from .milk_plan_schedule import (
    MilkPlanScheduleValidationError,
    normalize_milk_plan_payload,
    scheduled_task_dates,
)


MILK_PLAN_CREATE_ACTION = "plans.milk_plan.create"
MILK_PLAN_CHANGED_EVENT = "milk_plan.changed"
MILK_SCHEDULE_RESCHEDULE_ACTION = "plans.milk_schedule.reschedule"
PREGNANCY_PLAN_CREATE_ACTION = "pregnancy.plan.create"
PREGNANCY_PLAN_CHANGED_EVENT = "pregnancy_plan.changed"
PREGNANCY_PLAN_TODO_UPDATE_ACTION = "pregnancy.plan_todo.update"
PLAN_TASK_CREATE_ACTION = "plans.task.create"
PLAN_TASK_COMPLETE_ACTION = "plans.task.complete"
PLAN_TASK_UPDATE_ACTION = "plans.task.update"
PLAN_TASK_DELETE_ACTION = "plans.task.delete"
PLAN_DELETE_ACTION = "plans.plan.delete"


class MilkPlanCreateActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        if _is_expired(action.expires_at):
            raise PermanentJobError("milk_analysis_expired_before_plan")
        payload = dict(action.apply_payload or {})
        title = _text(payload, "title")
        if not title:
            raise PermanentJobError("missing_plan_title")

        plan_payload = payload.get("payload")
        if not isinstance(plan_payload, dict):
            plan_payload = {}
        _validate_milk_plan_lineage(plan_payload)
        try:
            normalized_plan_payload, scheduled_tasks = normalize_milk_plan_payload(plan_payload)
        except MilkPlanScheduleValidationError as exc:
            raise PermanentJobError("invalid_milk_plan_schedule") from exc
        try:
            plan = await self.service.create_plan(
                owner_user_id=action.actor_user_id,
                plan_type="milk_management",
                title=title,
                summary=_text(payload, "summary"),
                source="agent_action",
                payload={
                    **normalized_plan_payload,
                    "agent_action_id": str(action.id),
                    "agent_run_id": str(action.run_id),
                },
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
            for index, scheduled in enumerate(scheduled_tasks):
                await self.service.create_task(
                    owner_user_id=action.actor_user_id,
                    plan_id=plan.id,
                    task_date=scheduled.task_date,
                    task_time=scheduled.task_time,
                    title=scheduled.title,
                    description=scheduled.description,
                    payload={
                        "task_type": scheduled.task_type,
                        "source": "agent_action",
                        "agent_action_id": str(action.id),
                        "agent_run_id": str(action.run_id),
                        **({"duration_minutes": scheduled.duration_minutes} if scheduled.duration_minutes is not None else {}),
                    },
                    request_id=f"agent-action:{action.id}",
                    idempotency_key=f"agent-action:{action.id}:schedule:{index}",
                )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan.id),
            details={
                "plan_type": plan.plan_type,
                "task_count": len(scheduled_tasks),
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
                        "affected_dates": scheduled_task_dates(scheduled_tasks),
                    },
                ),
            ),
        )


class MilkScheduleRescheduleActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        plan_id = _required_uuid(payload, "plan_id", "missing_plan_id", "invalid_plan_id")
        updates = payload.get("updates")
        if not isinstance(updates, list) or not all(isinstance(item, dict) for item in updates):
            raise PermanentJobError("invalid_milk_schedule_updates")
        try:
            tasks = await self.service.reschedule_milk_tasks(
                owner_user_id=action.actor_user_id,
                plan_id=plan_id,
                updates=[dict(item) for item in updates],
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        affected_dates = sorted(
            {str(value) for item in updates for value in (item.get("expected_task_date"), item.get("new_task_date")) if value}
        )
        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan_id),
            details={
                "plan_type": "milk_management",
                "task_count": len(tasks),
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=(
                AgentApplicationEvent(
                    event_type=MILK_PLAN_CHANGED_EVENT,
                    payload={
                        "operation": "rescheduled",
                        "reason": "schedule_adjustment",
                        "plan_id": str(plan_id),
                        "plan_type": "milk_management",
                        "source": "agent_action",
                        "affected_dates": affected_dates,
                        "task_ids": [str(task.id) for task in tasks],
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


class PregnancyPlanTodoUpdateActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        plan_id = _required_uuid(payload, "plan_id", "missing_plan_id", "invalid_plan_id")
        item_id = _text(payload, "item_id")
        if not item_id:
            raise PermanentJobError("missing_todo_item_id")
        completed = _optional_bool(payload, "completed", default=True, code="invalid_completed")
        expected_version = _required_positive_int(payload, "expected_version", "invalid_expected_version")
        try:
            plan = await self.service.update_plan_todo_completion(
                owner_user_id=action.actor_user_id,
                plan_id=plan_id,
                item_id=item_id,
                completed=completed,
                expected_version=expected_version,
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        if plan.plan_type != "pregnancy":
            raise PermanentJobError("invalid_pregnancy_plan")
        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan.id),
            details={
                "plan_type": "pregnancy",
                "item_id": item_id,
                "completed": completed,
                "version": plan.version,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=(
                AgentApplicationEvent(
                    event_type=PREGNANCY_PLAN_CHANGED_EVENT,
                    payload={
                        "operation": "updated",
                        "reason": "todo_completion_changed",
                        "plan_id": str(plan.id),
                        "plan_type": "pregnancy",
                        "source": "agent_action",
                        "version": plan.version,
                        "item_ids": [item_id],
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

        application_events = await _milk_plan_task_events(
            service=self.service,
            task=task,
            operation="updated",
            reason="task_created",
        )

        return AgentActionApplyResult(
            resource_type="plan_task",
            resource_id=str(task.id),
            details={
                "status": task.status,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=application_events,
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

        application_events = await _milk_plan_task_events(
            service=self.service,
            task=task,
            operation="updated",
            reason="task_completion_changed",
        )

        return AgentActionApplyResult(
            resource_type="plan_task",
            resource_id=str(task.id),
            details={
                "status": task.status,
                "completed": completed,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=application_events,
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
        application_events = await _milk_plan_task_events(
            service=self.service,
            task=task,
            operation="updated",
            reason="task_updated",
        )
        return AgentActionApplyResult(
            resource_type="plan_task",
            resource_id=str(task.id),
            details={
                "status": task.status,
                "fields": sorted(updates),
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=application_events,
        )


class PlanTaskDeleteActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        task_id = _required_uuid(payload, "task_id", "missing_task_id", "invalid_task_id")
        try:
            get_task = getattr(self.service, "get_task", None)
            task = await get_task(owner_user_id=action.actor_user_id, task_id=task_id) if callable(get_task) else None
            await self.service.delete_task(
                owner_user_id=action.actor_user_id,
                task_id=task_id,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        application_events = (
            await _milk_plan_task_events(
                service=self.service,
                task=task,
                operation="deleted",
                reason="task_deleted",
            )
            if task is not None
            else ()
        )
        return AgentActionApplyResult(
            resource_type="plan_task",
            resource_id=str(task_id),
            details={
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=application_events,
        )


class PlanDeleteActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        plan_id = _required_uuid(payload, "plan_id", "missing_plan_id", "invalid_plan_id")
        try:
            get_plan = getattr(self.service, "get_plan", None)
            plan = await get_plan(owner_user_id=action.actor_user_id, plan_id=plan_id) if callable(get_plan) else None
            await self.service.delete_plan(
                owner_user_id=action.actor_user_id,
                plan_id=plan_id,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc
        application_events: tuple[AgentApplicationEvent, ...] = ()
        if plan is not None and plan.plan_type == "milk_management":
            application_events = (
                AgentApplicationEvent(
                    event_type=MILK_PLAN_CHANGED_EVENT,
                    payload={
                        "operation": "deleted",
                        "reason": "plan_deleted",
                        "plan_id": str(plan.id),
                        "plan_type": "milk_management",
                        "source": "agent_action",
                        "affected_dates": _milk_plan_affected_dates(plan),
                    },
                ),
            )
        elif plan is not None and plan.plan_type == "pregnancy":
            application_events = (
                AgentApplicationEvent(
                    event_type=PREGNANCY_PLAN_CHANGED_EVENT,
                    payload={
                        "operation": "deleted",
                        "reason": "plan_deleted",
                        "plan_id": str(plan.id),
                        "plan_type": "pregnancy",
                        "source": "agent_action",
                    },
                ),
            )
        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan_id),
            details={
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=application_events,
        )


async def _milk_plan_task_events(
    *,
    service: PlansService,
    task: Any,
    operation: str,
    reason: str,
) -> tuple[AgentApplicationEvent, ...]:
    plan_id = getattr(task, "plan_id", None)
    get_plan = getattr(service, "get_plan", None)
    if plan_id is None or not callable(get_plan):
        return ()
    try:
        plan = await get_plan(owner_user_id=task.owner_user_id, plan_id=plan_id)
    except ApiError:
        return ()
    if plan.plan_type != "milk_management":
        return ()
    task_date = getattr(task, "task_date", None)
    return (
        AgentApplicationEvent(
            event_type=MILK_PLAN_CHANGED_EVENT,
            payload={
                "operation": operation,
                "reason": reason,
                "plan_id": str(plan.id),
                "plan_type": "milk_management",
                "source": "agent_action",
                "affected_dates": [task_date.isoformat()] if isinstance(task_date, date) else [],
                "task_ids": [str(task.id)],
            },
        ),
    )


def _milk_plan_affected_dates(plan: Any) -> list[str]:
    payload = plan.payload if isinstance(getattr(plan, "payload", None), dict) else {}
    try:
        start = date.fromisoformat(str(payload.get("start_date") or ""))
        days = max(1, min(int(payload.get("days") or 1), 31))
    except (TypeError, ValueError):
        return []
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days)]


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    comparable = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
    return comparable <= datetime.now(timezone.utc)


def _validate_milk_plan_lineage(payload: dict[str, Any]) -> None:
    if _text(payload, "direction") not in {"increase", "maintain", "decrease"}:
        raise PermanentJobError("invalid_milk_analysis_lineage")
    if not _text(payload, "analysis_context_fingerprint"):
        raise PermanentJobError("invalid_milk_analysis_lineage")
    try:
        UUID(_text(payload, "analysis_workflow_state_id"))
    except (TypeError, ValueError) as exc:
        raise PermanentJobError("invalid_milk_analysis_lineage") from exc


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


def _required_positive_int(payload: dict[str, Any], key: str, code: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise PermanentJobError(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PermanentJobError(code) from exc
    if parsed < 1:
        raise PermanentJobError(code)
    return parsed


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
