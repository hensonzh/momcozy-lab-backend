from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel

from ...core.errors import ApiError
from ..audit import request_hash
from .agent_contracts import (
    AgentMilkScheduleReschedulePayload,
    AgentPlanDeletePayload,
    AgentPlanUpdatePayload,
    AgentPlansActionRequest,
    AgentPlanTaskCompletePayload,
    AgentPlanTaskCreatePayload,
    AgentPlanTaskDeletePayload,
    AgentPlanTaskUpdatePayload,
    AgentPregnancyPlanCreatePayload,
)
from .milk_schedule_calendar import (
    MilkScheduleCalendarEventError,
    normalize_milk_schedule_calendar_events,
)
from .pregnancy_plan_tasks import pregnancy_plan_task_drafts


AGENT_PLANS_IDEMPOTENCY_SCOPE = "internal.agent.plans.apply"
MILK_PLAN_CHANGED_EVENT = "milk_plan.changed"
PREGNANCY_PLAN_CHANGED_EVENT = "pregnancy_plan.changed"
_PayloadT = TypeVar("_PayloadT", bound=BaseModel)


@dataclass(frozen=True)
class AgentPlansActionResult:
    resource_type: Literal["plan", "plan_task"]
    resource_id: str
    details: dict[str, Any]
    application_events: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _ActionReceipt:
    resource_type: Literal["plan", "plan_task"]
    resource_id: str
    plan_type: str = ""
    plan_id: str = ""
    task_date: str = ""
    start_date: str = ""
    days: int = 0
    status: str = ""
    version: int = 0
    task_count: int = 0
    calendar_event_count: int = 0


class AgentPlansActionService:
    """Product-owned, action-bound plan mutation boundary."""

    def __init__(
        self,
        *,
        plans_service: Any,
        idempotency_service: Any,
        audit_service: Any | None = None,
    ) -> None:
        self.plans_service = plans_service
        self.idempotency_service = idempotency_service
        self.audit_service = audit_service

    async def apply_idempotent(
        self,
        *,
        command: AgentPlansActionRequest,
        idempotency_key: str,
        actor_service: str,
        request_id: str,
    ) -> AgentPlansActionResult:
        expected_key = f"agent-action:{command.action_id}"
        if idempotency_key != expected_key:
            raise ApiError(
                code="validation_failed",
                message="Idempotency-Key must be bound to action_id.",
                status=422,
            )
        normalized_actor_service = actor_service.strip()
        if not normalized_actor_service:
            raise ApiError(
                code="validation_failed",
                message="Service actor is required.",
                status=422,
            )
        decision = await self.idempotency_service.reserve(
            actor_user_id=command.actor_user_id,
            scope=AGENT_PLANS_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            request_hash=request_hash(
                {
                    "actor": {
                        "type": "service",
                        "service": normalized_actor_service,
                        "user_id": str(command.actor_user_id),
                    },
                    "action_id": str(command.action_id),
                    "run_id": str(command.run_id),
                    "action_type": command.action_type,
                    "payload": command.payload.model_dump(
                        mode="json",
                        exclude_unset=True,
                    ),
                }
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        if decision.status == "replay":
            return _result_from_receipt(
                command=command,
                receipt=_decode_receipt(decision.record.response_ref),
            )

        receipt = await self._apply(command)
        result = _result_from_receipt(command=command, receipt=receipt)
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=None,
                actor_type="service",
                actor_service=normalized_actor_service,
                action=command.action_type,
                resource_type=result.resource_type,
                resource_id=result.resource_id,
                request_id=request_id,
                details={
                    **result.details,
                    "owner_user_id": str(command.actor_user_id),
                    "action_id": str(command.action_id),
                    "run_id": str(command.run_id),
                    "action_type": command.action_type,
                },
            )
        await self.idempotency_service.mark_completed(
            record=decision.record,
            response_ref=_encode_receipt(receipt),
        )
        return result

    async def _apply(self, command: AgentPlansActionRequest) -> _ActionReceipt:
        if command.action_type == "plans.task.create":
            return await self._create_task(command)
        if command.action_type == "plans.task.complete":
            return await self._complete_task(command)
        if command.action_type == "plans.task.update":
            return await self._update_task(command)
        if command.action_type == "plans.task.delete":
            return await self._delete_task(command)
        if command.action_type == "plans.plan.update":
            return await self._update_plan(command)
        if command.action_type == "plans.plan.delete":
            return await self._delete_plan(command)
        if command.action_type == "pregnancy.plan.create":
            return await self._create_pregnancy_plan(command)
        if command.action_type == "plans.milk_schedule.reschedule":
            return await self._reschedule_milk_plan(command)
        raise ApiError(
            code="unsupported_operation",
            message="Agent plan action is not supported.",
            status=422,
        )

    async def _create_task(
        self,
        command: AgentPlansActionRequest,
    ) -> _ActionReceipt:
        payload = _payload(command, AgentPlanTaskCreatePayload)
        task_payload = {
            **payload.payload,
            "agent_action_id": str(command.action_id),
            "agent_run_id": str(command.run_id),
        }
        task = await self.plans_service.create_task(
            owner_user_id=command.actor_user_id,
            plan_id=payload.plan_id,
            task_date=payload.task_date,
            task_time=payload.task_time,
            title=payload.title,
            description=payload.description,
            payload=task_payload,
            request_id=f"agent-action:{command.action_id}",
        )
        plan_type = await self._task_plan_type(
            owner_user_id=command.actor_user_id,
            task=task,
        )
        return _task_receipt(task=task, plan_type=plan_type)

    async def _complete_task(
        self,
        command: AgentPlansActionRequest,
    ) -> _ActionReceipt:
        payload = _payload(command, AgentPlanTaskCompletePayload)
        task = await self.plans_service.set_task_completed(
            owner_user_id=command.actor_user_id,
            task_id=payload.task_id,
            completed=payload.completed,
            request_id=f"agent-action:{command.action_id}",
        )
        plan_type = await self._task_plan_type(
            owner_user_id=command.actor_user_id,
            task=task,
        )
        return _task_receipt(task=task, plan_type=plan_type)

    async def _update_task(
        self,
        command: AgentPlansActionRequest,
    ) -> _ActionReceipt:
        payload = _payload(command, AgentPlanTaskUpdatePayload)
        updates = payload.model_dump(
            mode="python",
            exclude={"task_id"},
            exclude_unset=True,
        )
        task = await self.plans_service.update_task(
            owner_user_id=command.actor_user_id,
            task_id=payload.task_id,
            updates=updates,
            request_id=f"agent-action:{command.action_id}",
        )
        plan_type = await self._task_plan_type(
            owner_user_id=command.actor_user_id,
            task=task,
        )
        return _task_receipt(task=task, plan_type=plan_type)

    async def _delete_task(
        self,
        command: AgentPlansActionRequest,
    ) -> _ActionReceipt:
        payload = _payload(command, AgentPlanTaskDeletePayload)
        task = await self.plans_service.get_task(
            owner_user_id=command.actor_user_id,
            task_id=payload.task_id,
        )
        plan_type = await self._task_plan_type(
            owner_user_id=command.actor_user_id,
            task=task,
        )
        receipt = _task_receipt(task=task, plan_type=plan_type)
        await self.plans_service.delete_task(
            owner_user_id=command.actor_user_id,
            task_id=payload.task_id,
            request_id=f"agent-action:{command.action_id}",
        )
        return receipt

    async def _delete_plan(
        self,
        command: AgentPlansActionRequest,
    ) -> _ActionReceipt:
        payload = _payload(command, AgentPlanDeletePayload)
        plan = await self.plans_service.get_plan(
            owner_user_id=command.actor_user_id,
            plan_id=payload.plan_id,
        )
        receipt = _plan_receipt(plan)
        await self.plans_service.delete_plan(
            owner_user_id=command.actor_user_id,
            plan_id=payload.plan_id,
            request_id=f"agent-action:{command.action_id}",
        )
        return receipt

    async def _update_plan(
        self,
        command: AgentPlansActionRequest,
    ) -> _ActionReceipt:
        payload = _payload(command, AgentPlanUpdatePayload)
        updates = payload.model_dump(
            mode="python",
            include={"title", "summary"},
            exclude_unset=True,
        )
        plan = await self.plans_service.update_plan_metadata(
            owner_user_id=command.actor_user_id,
            plan_id=payload.plan_id,
            expected_version=payload.expected_version,
            updates=updates,
            request_id=f"agent-action:{command.action_id}",
        )
        return _plan_receipt(plan)

    async def _create_pregnancy_plan(
        self,
        command: AgentPlansActionRequest,
    ) -> _ActionReceipt:
        payload = _payload(command, AgentPregnancyPlanCreatePayload)
        plan = await self.plans_service.create_plan(
            owner_user_id=command.actor_user_id,
            plan_type="pregnancy",
            title=payload.title,
            summary=payload.summary,
            source="agent_action",
            payload={
                **payload.payload,
                "agent_action_id": str(command.action_id),
                "agent_run_id": str(command.run_id),
            },
            request_id=f"agent-action:{command.action_id}",
            idempotency_key=f"agent-action:{command.action_id}",
        )
        task_drafts = pregnancy_plan_task_drafts(plan.payload)
        for index, draft in enumerate(task_drafts):
            await self.plans_service.create_task(
                owner_user_id=command.actor_user_id,
                plan_id=plan.id,
                task_date=draft.task_date,
                task_time="",
                title=draft.title,
                description=draft.description,
                payload={
                    **draft.payload,
                    "agent_action_id": str(command.action_id),
                    "agent_run_id": str(command.run_id),
                },
                request_id=f"agent-action:{command.action_id}",
                idempotency_key=(
                    f"agent-action:{command.action_id}:pregnancy-task:{index}"
                ),
            )
        return _plan_receipt(plan, task_count=len(task_drafts))

    async def _reschedule_milk_plan(
        self,
        command: AgentPlansActionRequest,
    ) -> _ActionReceipt:
        payload = _payload(command, AgentMilkScheduleReschedulePayload)
        updates = [
            update.model_dump(mode="json")
            for update in payload.updates
        ]
        try:
            calendar_events = normalize_milk_schedule_calendar_events(
                [
                    event.model_dump(
                        mode="json",
                        exclude_defaults=True,
                    )
                    for event in payload.calendar_events
                ]
            )
        except MilkScheduleCalendarEventError as exc:
            raise ApiError(
                code=str(exc),
                message="A calendar event could not be applied.",
                status=422,
            ) from exc

        tasks: list[Any] = []
        if updates:
            tasks = await self.plans_service.reschedule_milk_tasks(
                owner_user_id=command.actor_user_id,
                plan_id=payload.plan_id,
                updates=updates,
                request_id=f"agent-action:{command.action_id}",
            )
        else:
            plan = await self.plans_service.get_plan(
                owner_user_id=command.actor_user_id,
                plan_id=payload.plan_id,
            )
            if plan.plan_type != "milk_management":
                raise ApiError(
                    code="validation_failed",
                    message="Plan is not a milk-management plan.",
                    status=422,
                )
        for event in calendar_events:
            await self.plans_service.create_task(
                owner_user_id=command.actor_user_id,
                plan_id=None,
                task_date=date.fromisoformat(str(event["date"])),
                task_time=str(event["start_time"]),
                title=str(event["title"]),
                description=str(event.get("description") or ""),
                payload={
                    "task_type": "other",
                    "calendar_kind": "custom_event",
                    "end_time": str(event["end_time"]),
                    "duration_minutes": int(event["duration_minutes"]),
                    "source": "agent_action",
                    "agent_action_id": str(command.action_id),
                    "agent_run_id": str(command.run_id),
                },
                request_id=f"agent-action:{command.action_id}",
            )
        return _ActionReceipt(
            resource_type="plan",
            resource_id=str(payload.plan_id),
            plan_type="milk_management",
            task_count=len(tasks),
            calendar_event_count=len(calendar_events),
        )

    async def _task_plan_type(
        self,
        *,
        owner_user_id: UUID,
        task: Any,
    ) -> str:
        plan_id = getattr(task, "plan_id", None)
        if not isinstance(plan_id, UUID):
            return ""
        plan = await self.plans_service.get_plan(
            owner_user_id=owner_user_id,
            plan_id=plan_id,
        )
        return str(getattr(plan, "plan_type", "") or "")


def _payload(
    command: AgentPlansActionRequest,
    payload_type: type[_PayloadT],
) -> _PayloadT:
    if not isinstance(command.payload, payload_type):
        raise ApiError(
            code="internal_error",
            message="Agent plan action payload was not normalized.",
            status=500,
        )
    return command.payload


def _task_receipt(*, task: Any, plan_type: str) -> _ActionReceipt:
    task_date = getattr(task, "task_date", None)
    return _ActionReceipt(
        resource_type="plan_task",
        resource_id=str(task.id),
        plan_type=plan_type,
        plan_id=str(getattr(task, "plan_id", "") or ""),
        task_date=task_date.isoformat() if isinstance(task_date, date) else "",
        status=str(getattr(task, "status", "") or ""),
    )


def _plan_receipt(
    plan: Any,
    *,
    task_count: int = 0,
) -> _ActionReceipt:
    payload = (
        dict(plan.payload)
        if isinstance(getattr(plan, "payload", None), dict)
        else {}
    )
    starts_on = getattr(plan, "starts_on", None)
    ends_on = getattr(plan, "ends_on", None)
    start_date = (
        starts_on.isoformat()
        if isinstance(starts_on, date)
        else str(payload.get("start_date") or "")
    )
    if isinstance(starts_on, date) and isinstance(ends_on, date):
        days = max(1, (ends_on - starts_on).days + 1)
    else:
        try:
            days = max(1, min(int(payload.get("days") or 1), 31))
        except (TypeError, ValueError):
            days = 0
    return _ActionReceipt(
        resource_type="plan",
        resource_id=str(plan.id),
        plan_type=str(getattr(plan, "plan_type", "") or ""),
        start_date=start_date,
        days=days,
        status=str(getattr(plan, "status", "") or ""),
        version=int(getattr(plan, "version", 0) or 0),
        task_count=task_count,
    )


def _result_from_receipt(
    *,
    command: AgentPlansActionRequest,
    receipt: _ActionReceipt,
) -> AgentPlansActionResult:
    details: dict[str, Any] = {}
    events: tuple[dict[str, Any], ...] = ()
    if command.action_type == "plans.task.create":
        details = {"status": receipt.status or "pending"}
        events = _task_events(
            receipt=receipt,
            operation="updated",
            reason="task_created",
        )
    elif command.action_type == "plans.task.complete":
        complete_payload = _payload(command, AgentPlanTaskCompletePayload)
        details = {
            "status": receipt.status,
            "completed": complete_payload.completed,
        }
        events = _task_events(
            receipt=receipt,
            operation="updated",
            reason="task_completion_changed",
        )
    elif command.action_type == "plans.task.update":
        update_payload = _payload(command, AgentPlanTaskUpdatePayload)
        details = {
            "status": receipt.status,
            "fields": sorted(
                update_payload.model_fields_set - {"task_id"}
            ),
        }
        events = _task_events(
            receipt=receipt,
            operation="updated",
            reason="task_updated",
        )
    elif command.action_type == "plans.task.delete":
        events = _task_events(
            receipt=receipt,
            operation="deleted",
            reason="task_deleted",
        )
    elif command.action_type == "plans.plan.delete":
        events = _plan_delete_events(receipt)
    elif command.action_type == "plans.plan.update":
        plan_update_payload = _payload(command, AgentPlanUpdatePayload)
        fields = sorted(
            plan_update_payload.model_fields_set
            - {"plan_id", "expected_version"}
        )
        details = {
            "plan_type": receipt.plan_type,
            "status": receipt.status,
            "version": receipt.version,
            "fields": fields,
        }
        events = _plan_update_events(receipt)
    elif command.action_type == "pregnancy.plan.create":
        details = {
            "plan_type": "pregnancy",
            "status": receipt.status,
            "version": receipt.version,
            "task_count": receipt.task_count,
        }
        events = (
            {
                "type": PREGNANCY_PLAN_CHANGED_EVENT,
                "payload": {
                    "operation": "created",
                    "reason": "plan_created",
                    "plan_id": receipt.resource_id,
                    "plan_type": "pregnancy",
                    "source": "agent_action",
                    "task_count": receipt.task_count,
                },
            },
        )
    elif command.action_type == "plans.milk_schedule.reschedule":
        reschedule_payload = _payload(
            command,
            AgentMilkScheduleReschedulePayload,
        )
        affected_dates = sorted(
            {
                value.isoformat()
                for update in reschedule_payload.updates
                for value in (
                    update.expected_task_date,
                    update.new_task_date,
                )
            }
            | {
                event.date.isoformat()
                for event in reschedule_payload.calendar_events
            }
        )
        details = {
            "plan_type": "milk_management",
            "task_count": receipt.task_count,
            "calendar_event_count": receipt.calendar_event_count,
        }
        events = (
            {
                "type": MILK_PLAN_CHANGED_EVENT,
                "payload": {
                    "operation": "rescheduled",
                    "reason": "schedule_adjustment",
                    "plan_id": receipt.resource_id,
                    "plan_type": "milk_management",
                    "source": "agent_action",
                    "affected_dates": affected_dates,
                    "task_ids": [
                        str(update.task_id)
                        for update in reschedule_payload.updates
                    ],
                    "created_calendar_event_count": (
                        receipt.calendar_event_count
                    ),
                },
            },
        )
    return AgentPlansActionResult(
        resource_type=receipt.resource_type,
        resource_id=receipt.resource_id,
        details=details,
        application_events=events,
    )


def _task_events(
    *,
    receipt: _ActionReceipt,
    operation: str,
    reason: str,
) -> tuple[dict[str, Any], ...]:
    if receipt.plan_type != "milk_management":
        return ()
    return (
        {
            "type": MILK_PLAN_CHANGED_EVENT,
            "payload": {
                "operation": operation,
                "reason": reason,
                "plan_id": receipt.plan_id,
                "plan_type": "milk_management",
                "source": "agent_action",
                "affected_dates": (
                    [receipt.task_date]
                    if receipt.task_date
                    else []
                ),
                "task_ids": [receipt.resource_id],
            },
        },
    )


def _plan_delete_events(
    receipt: _ActionReceipt,
) -> tuple[dict[str, Any], ...]:
    if receipt.plan_type == "pregnancy":
        return (
            {
                "type": PREGNANCY_PLAN_CHANGED_EVENT,
                "payload": {
                    "operation": "deleted",
                    "reason": "plan_deleted",
                    "plan_id": receipt.resource_id,
                    "plan_type": "pregnancy",
                    "source": "agent_action",
                },
            },
        )
    if receipt.plan_type != "milk_management":
        return ()
    affected_dates: list[str] = []
    try:
        start_date = date.fromisoformat(receipt.start_date)
    except ValueError:
        start_date = None
    if start_date is not None and receipt.days > 0:
        affected_dates = [
            (start_date + timedelta(days=offset)).isoformat()
            for offset in range(receipt.days)
        ]
    return (
        {
            "type": MILK_PLAN_CHANGED_EVENT,
            "payload": {
                "operation": "deleted",
                "reason": "plan_deleted",
                "plan_id": receipt.resource_id,
                "plan_type": "milk_management",
                "source": "agent_action",
                "affected_dates": affected_dates,
            },
        },
    )


def _plan_update_events(
    receipt: _ActionReceipt,
) -> tuple[dict[str, Any], ...]:
    event_type = {
        "pregnancy": PREGNANCY_PLAN_CHANGED_EVENT,
        "milk_management": MILK_PLAN_CHANGED_EVENT,
    }.get(receipt.plan_type)
    if event_type is None:
        return ()
    return (
        {
            "type": event_type,
            "payload": {
                "operation": "updated",
                "reason": "plan_updated",
                "plan_id": receipt.resource_id,
                "plan_type": receipt.plan_type,
                "source": "agent_action",
                "version": receipt.version,
            },
        },
    )


def _encode_receipt(receipt: _ActionReceipt) -> str:
    response_ref = json.dumps(
        {
            key: value
            for key, value in {
                "resource_type": receipt.resource_type,
                "resource_id": receipt.resource_id,
                "plan_type": receipt.plan_type,
                "plan_id": receipt.plan_id,
                "task_date": receipt.task_date,
                "start_date": receipt.start_date,
                "days": receipt.days,
                "status": receipt.status,
                "version": receipt.version,
                "task_count": receipt.task_count,
                "calendar_event_count": receipt.calendar_event_count,
            }.items()
            if value not in ("", 0)
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(response_ref) > 512:
        raise ApiError(
            code="internal_error",
            message="Agent plan action receipt is too large.",
            status=500,
        )
    return response_ref


def _decode_receipt(response_ref: str) -> _ActionReceipt:
    if not response_ref:
        raise ApiError(
            code="idempotency_in_progress",
            message="Plan action is still in progress.",
            status=409,
        )
    try:
        payload = json.loads(response_ref)
        resource_type = str(payload["resource_type"])
        resource_id = str(payload["resource_id"])
        UUID(resource_id)
        if resource_type not in {"plan", "plan_task"}:
            raise ValueError("invalid resource type")
        normalized_resource_type: Literal["plan", "plan_task"] = (
            "plan" if resource_type == "plan" else "plan_task"
        )
        return _ActionReceipt(
            resource_type=normalized_resource_type,
            resource_id=resource_id,
            plan_type=str(payload.get("plan_type") or ""),
            plan_id=str(payload.get("plan_id") or ""),
            task_date=str(payload.get("task_date") or ""),
            start_date=str(payload.get("start_date") or ""),
            days=int(payload.get("days") or 0),
            status=str(payload.get("status") or ""),
            version=int(payload.get("version") or 0),
            task_count=int(payload.get("task_count") or 0),
            calendar_event_count=int(
                payload.get("calendar_event_count") or 0
            ),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ApiError(
            code="conflict",
            message="Plan action replay identity is invalid.",
            status=409,
        ) from exc
