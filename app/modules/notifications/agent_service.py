from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, request_hash
from .agent_contracts import AgentMilkReminderPayload
from .repository import NotificationsRepository


AGENT_MILK_REMINDER_IDEMPOTENCY_SCOPE = (
    "internal.agent.notifications.milk_reminder"
)
_ACTION_TYPES = {
    "create": "notifications.milk_reminder.create",
    "update": "notifications.milk_reminder.update",
    "delete": "notifications.milk_reminder.delete",
    "disable": "notifications.milk_reminder.disable",
}
_APPLIED_OPERATIONS = {
    "create": "created",
    "update": "updated",
    "delete": "deleted",
    "disable": "disabled",
}


@dataclass(frozen=True)
class AgentMilkReminderActionResult:
    resource_type: str
    resource_id: str
    details: dict[str, Any]
    application_events: tuple[dict[str, Any], ...]


class AgentMilkReminderActionService:
    """Product-owned, action-bound milk reminder mutation boundary."""

    def __init__(
        self,
        *,
        repository: NotificationsRepository,
        idempotency_service: IdempotencyService,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.idempotency_service = idempotency_service
        self.audit_service = audit_service

    async def apply_idempotent(
        self,
        *,
        owner_user_id: UUID,
        payload: AgentMilkReminderPayload | dict[str, Any],
        idempotency_key: str,
        action_id: UUID,
        run_id: UUID,
        actor_service: str,
        request_id: str,
    ) -> AgentMilkReminderActionResult:
        command = (
            payload
            if isinstance(payload, AgentMilkReminderPayload)
            else AgentMilkReminderPayload.model_validate(payload)
        )
        expected_key = f"agent-action:{action_id}"
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
        action_type = _ACTION_TYPES[command.operation]
        decision = await self.idempotency_service.reserve(
            actor_user_id=owner_user_id,
            scope=AGENT_MILK_REMINDER_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            request_hash=request_hash(
                {
                    "actor": {
                        "type": "service",
                        "service": normalized_actor_service,
                        "user_id": str(owner_user_id),
                    },
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                    "action_type": action_type,
                    "payload": command.model_dump(
                        mode="json",
                        exclude_unset=True,
                    ),
                }
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        if decision.status == "replay":
            return _replay_result(decision.record.response_ref)

        result = await self._apply(
            owner_user_id=owner_user_id,
            command=command,
            action_id=action_id,
            run_id=run_id,
        )
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=None,
                actor_type="service",
                actor_service=normalized_actor_service,
                action=action_type,
                resource_type=result.resource_type,
                resource_id=result.resource_id,
                request_id=request_id,
                details={
                    **result.details,
                    "owner_user_id": str(owner_user_id),
                    "action_id": str(action_id),
                    "run_id": str(run_id),
                },
            )
        await self.idempotency_service.mark_completed(
            record=decision.record,
            response_ref=_response_ref(result),
        )
        return result

    async def _apply(
        self,
        *,
        owner_user_id: UUID,
        command: AgentMilkReminderPayload,
        action_id: UUID,
        run_id: UUID,
    ) -> AgentMilkReminderActionResult:
        if command.operation == "create":
            return await self._create(
                owner_user_id=owner_user_id,
                command=command,
                action_id=action_id,
                run_id=run_id,
            )

        reminder = await self.repository.get_milk_reminder_for_owner(
            reminder_id=_required_reminder_id(command),
            owner_user_id=owner_user_id,
        )
        if reminder is None:
            raise ApiError(
                code="not_found",
                message="Milk reminder not found.",
                status=404,
            )

        if command.operation == "update":
            supplied = command.model_fields_set
            if "title" in supplied:
                reminder.title = str(command.title)
            if "body" in supplied:
                reminder.body = command.body or ""
            reminder_payload = dict(reminder.payload or {})
            if "remind_at" in supplied:
                reminder_payload["remind_at"] = _iso_datetime(
                    command.remind_at
                )
            if "payload" in supplied:
                custom_payload = command.payload or {}
                reminder_payload = {
                    **reminder_payload,
                    **custom_payload,
                    "remind_at": reminder_payload.get("remind_at", ""),
                    "agent_action_id": str(action_id),
                    "agent_run_id": str(run_id),
                }
            else:
                reminder_payload["agent_action_id"] = str(action_id)
                reminder_payload["agent_run_id"] = str(run_id)
            reminder.payload = reminder_payload
            await self.repository.flush()
            return _result(
                reminder_id=reminder.id,
                operation=command.operation,
                status=reminder.status,
                remind_at=str(reminder.payload.get("remind_at") or ""),
            )

        if command.operation == "disable":
            reminder.status = "disabled"
            await self.repository.flush()
            return _result(
                reminder_id=reminder.id,
                operation=command.operation,
                status=reminder.status,
                remind_at=str(reminder.payload.get("remind_at") or ""),
            )

        await self.repository.delete_milk_reminder(reminder=reminder)
        return _result(
            reminder_id=reminder.id,
            operation=command.operation,
            status="deleted",
            remind_at=str(reminder.payload.get("remind_at") or ""),
        )

    async def _create(
        self,
        *,
        owner_user_id: UUID,
        command: AgentMilkReminderPayload,
        action_id: UUID,
        run_id: UUID,
    ) -> AgentMilkReminderActionResult:
        remind_at = _iso_datetime(command.remind_at)
        notification = await self.repository.create_notification(
            owner_user_id=owner_user_id,
            notification_type="milk_reminder",
            title=str(command.title),
            body=command.body or "",
            source="agent_action",
            payload={
                **(command.payload or {}),
                "remind_at": remind_at,
                "agent_action_id": str(action_id),
                "agent_run_id": str(run_id),
            },
            delivered_at=None,
        )
        notification.status = "scheduled"
        await self.repository.flush()
        return _result(
            reminder_id=notification.id,
            operation=command.operation,
            status=notification.status,
            remind_at=remind_at,
        )


def _required_reminder_id(command: AgentMilkReminderPayload) -> UUID:
    if command.reminder_id is None:
        raise ApiError(
            code="validation_failed",
            message="reminder_id is required.",
            status=422,
        )
    return command.reminder_id


def _iso_datetime(value: datetime | None) -> str:
    if value is None:
        raise ApiError(
            code="validation_failed",
            message="remind_at is required.",
            status=422,
        )
    return value.isoformat()


def _result(
    *,
    reminder_id: UUID,
    operation: str,
    status: str,
    remind_at: str,
) -> AgentMilkReminderActionResult:
    applied_operation = _APPLIED_OPERATIONS[operation]
    details = {
        "operation": applied_operation,
        "status": status,
    }
    if remind_at:
        details["remind_at"] = remind_at
    return AgentMilkReminderActionResult(
        resource_type="milk_reminder",
        resource_id=str(reminder_id),
        details=details,
        application_events=(
            {
                "type": "notifications.milk_reminder.changed",
                "payload": {
                    "reminder_id": str(reminder_id),
                    **details,
                    "source": "agent_action",
                },
            },
        ),
    )


def _response_ref(result: AgentMilkReminderActionResult) -> str:
    return json.dumps(
        {
            "resource_type": result.resource_type,
            "resource_id": result.resource_id,
            "details": result.details,
            "application_events": list(result.application_events),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _replay_result(response_ref: str) -> AgentMilkReminderActionResult:
    if not response_ref:
        raise ApiError(
            code="idempotency_in_progress",
            message="Milk reminder action is still in progress.",
            status=409,
        )
    try:
        parsed = json.loads(response_ref)
        resource_id = str(parsed["resource_id"])
        UUID(resource_id)
        if parsed["resource_type"] != "milk_reminder":
            raise ValueError
        details = dict(parsed["details"])
        application_events = tuple(
            dict(event) for event in parsed["application_events"]
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ApiError(
            code="conflict",
            message="Milk reminder action replay identity is invalid.",
            status=409,
        ) from exc
    return AgentMilkReminderActionResult(
        resource_type="milk_reminder",
        resource_id=resource_id,
        details=details,
        application_events=application_events,
    )
