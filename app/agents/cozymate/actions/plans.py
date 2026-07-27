from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from app.agent_runtime.actions import AgentActionApplyResult, AgentApplicationEvent, PermanentActionError
from app.agent_runtime.runs.models import AgentAction
from app.core.errors import ApiError
from app.modules.plans.milk_schedule_calendar import (
    MilkScheduleCalendarEventError,
    normalize_milk_schedule_calendar_events,
)
from app.modules.plans.pregnancy_plan_tasks import pregnancy_plan_task_drafts
from app.modules.plans.service import PlansService


MILK_PLAN_CHANGED_EVENT = "milk_plan.changed"
MILK_SCHEDULE_RESCHEDULE_ACTION = "plans.milk_schedule.reschedule"
PREGNANCY_PLAN_CREATE_ACTION = "pregnancy.plan.create"
PREGNANCY_PLAN_CHANGED_EVENT = "pregnancy_plan.changed"
PLAN_TASK_CREATE_ACTION = "plans.task.create"
PLAN_TASK_COMPLETE_ACTION = "plans.task.complete"
PLAN_TASK_UPDATE_ACTION = "plans.task.update"
PLAN_TASK_DELETE_ACTION = "plans.task.delete"
PLAN_UPDATE_ACTION = "plans.plan.update"
PLAN_DELETE_ACTION = "plans.plan.delete"


class MilkScheduleRescheduleActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        plan_id = _required_uuid(payload, "plan_id", "missing_plan_id", "invalid_plan_id")
        updates = payload.get("updates")
        if not isinstance(updates, list) or not all(isinstance(item, dict) for item in updates):
            raise PermanentActionError("invalid_milk_schedule_updates")
        try:
            calendar_events = normalize_milk_schedule_calendar_events(payload.get("calendar_events"))
        except MilkScheduleCalendarEventError as exc:
            raise PermanentActionError(str(exc)) from exc
        if not updates and not calendar_events:
            raise PermanentActionError("empty_milk_schedule_adjustment")
        tasks: list[Any] = []
        calendar_event_tasks: list[Any] = []
        try:
            if updates:
                tasks = await self.service.reschedule_milk_tasks(
                    owner_user_id=action.actor_user_id,
                    plan_id=plan_id,
                    updates=[dict(item) for item in updates],
                    request_id=f"agent-action:{action.id}",
                )
            else:
                plan = await self.service.get_plan(owner_user_id=action.actor_user_id, plan_id=plan_id)
                if plan.plan_type != "milk_management":
                    raise ApiError(code="validation_failed", message="Plan is not a milk-management plan.", status=422)
            for index, event in enumerate(calendar_events):
                calendar_event_tasks.append(
                    await self.service.create_task(
                        owner_user_id=action.actor_user_id,
                        plan_id=None,
                        task_date=date.fromisoformat(str(event["date"])),
                        task_time=str(event["start_time"]),
                        title=str(event["title"]),
                        description=str(event.get("description") or ""),
                        payload={
                            "domain": "general",
                            "event_type": "calendar_event",
                            "task_type": "other",
                            "calendar_kind": "custom_event",
                            "end_time": str(event["end_time"]),
                            "duration_minutes": int(event["duration_minutes"]),
                            "source": "agent_action",
                            "agent_action_id": str(action.id),
                            "agent_run_id": str(action.run_id),
                        },
                        request_id=f"agent-action:{action.id}",
                        idempotency_key=f"agent-action:{action.id}:calendar-event:{index}",
                    )
                )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc
        affected_dates = sorted(
            {
                str(value)
                for item in updates
                for value in (item.get("expected_task_date"), item.get("new_task_date"))
                if value
            }
            | {str(event["date"]) for event in calendar_events}
        )
        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan_id),
            details={
                "plan_type": "milk_management",
                "task_count": len(tasks),
                "calendar_event_count": len(calendar_event_tasks),
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
                        "calendar_event_task_ids": [str(task.id) for task in calendar_event_tasks],
                        "created_calendar_event_count": len(calendar_event_tasks),
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
            raise PermanentActionError("missing_plan_title")

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
            task_drafts = pregnancy_plan_task_drafts(plan.payload)
            for index, draft in enumerate(task_drafts):
                await self.service.create_task(
                    owner_user_id=action.actor_user_id,
                    plan_id=plan.id,
                    task_date=draft.task_date,
                    task_time="",
                    title=draft.title,
                    description=draft.description,
                    payload={
                        **draft.payload,
                        "agent_action_id": str(action.id),
                        "agent_run_id": str(action.run_id),
                    },
                    request_id=f"agent-action:{action.id}",
                    idempotency_key=f"agent-action:{action.id}:pregnancy-task:{index}",
                )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan.id),
            details={
                "plan_type": plan.plan_type,
                "status": plan.status,
                "version": plan.version,
                "task_count": len(task_drafts),
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
                        "task_count": len(task_drafts),
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
            raise PermanentActionError("missing_task_title")

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
            raise PermanentActionError(exc.code) from exc

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
            raise PermanentActionError(exc.code) from exc

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
            raise PermanentActionError("missing_task_update")
        try:
            task = await self.service.update_task(
                owner_user_id=action.actor_user_id,
                task_id=task_id,
                updates=updates,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc
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
            raise PermanentActionError(exc.code) from exc
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
                reason=_text(payload, "reason"),
            )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc
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
                **(
                    {
                        "plan_type": plan.plan_type,
                        "status": "deleted",
                        "version": plan.version,
                    }
                    if plan is not None
                    else {}
                ),
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
            application_events=application_events,
        )


class PlanUpdateActionHandler:
    def __init__(self, *, service: PlansService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        plan_id = _required_uuid(payload, "plan_id", "missing_plan_id", "invalid_plan_id")
        expected_version = payload.get("expected_version")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
            raise PermanentActionError("invalid_expected_version")
        updates = {
            key: payload[key]
            for key in ("title", "summary")
            if key in payload
        }
        if not updates:
            raise PermanentActionError("missing_plan_update")
        try:
            plan = await self.service.update_plan_metadata(
                owner_user_id=action.actor_user_id,
                plan_id=plan_id,
                expected_version=expected_version,
                updates=updates,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc
        application_events: tuple[AgentApplicationEvent, ...] = ()
        if plan.plan_type == "milk_management":
            application_events = (
                AgentApplicationEvent(
                    event_type=MILK_PLAN_CHANGED_EVENT,
                    payload={
                        "operation": "updated",
                        "reason": "plan_metadata_updated",
                        "plan_id": str(plan.id),
                        "plan_type": plan.plan_type,
                        "source": "agent_action",
                        "affected_dates": _milk_plan_affected_dates(plan),
                    },
                ),
            )
        elif plan.plan_type == "pregnancy":
            application_events = (
                AgentApplicationEvent(
                    event_type=PREGNANCY_PLAN_CHANGED_EVENT,
                    payload={
                        "operation": "updated",
                        "plan_id": str(plan.id),
                        "plan_type": plan.plan_type,
                        "source": "agent_action",
                    },
                ),
            )
        return AgentActionApplyResult(
            resource_type="plan",
            resource_id=str(plan.id),
            details={
                "plan_type": plan.plan_type,
                "version": plan.version,
                "fields": sorted(updates),
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


def _required_uuid(payload: dict[str, Any], key: str, missing_code: str, invalid_code: str) -> UUID:
    value = _text(payload, key)
    if not value:
        raise PermanentActionError(missing_code)
    try:
        return UUID(value)
    except ValueError as exc:
        raise PermanentActionError(invalid_code) from exc


def _optional_uuid(payload: dict[str, Any], key: str, code: str) -> UUID | None:
    value = _text(payload, key)
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise PermanentActionError(code) from exc


def _optional_date(payload: dict[str, Any], key: str, code: str) -> date | None:
    value = payload.get(key)
    if value in ("", None):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise PermanentActionError(code) from exc


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool, code: str) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    raise PermanentActionError(code)


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
