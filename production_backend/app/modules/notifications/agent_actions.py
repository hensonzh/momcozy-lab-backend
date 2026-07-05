from __future__ import annotations

from typing import Any

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from ..agent_runtime.actions.outbox import AgentActionApplyResult
from ..agent_runtime.models import AgentAction
from .service import NotificationsService


MILK_REMINDER_CREATE_ACTION = "notifications.milk_reminder.create"


class MilkReminderCreateActionHandler:
    def __init__(self, *, service: NotificationsService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        title = _text(payload, "title")
        if not title:
            raise PermanentJobError("missing_reminder_title")

        reminder_payload = payload.get("payload")
        if not isinstance(reminder_payload, dict):
            reminder_payload = {}
        remind_at = _text(payload, "remind_at")
        if remind_at:
            reminder_payload = {**reminder_payload, "remind_at": remind_at}

        try:
            notification = await self.service.create_notification(
                owner_user_id=action.actor_user_id,
                notification_type="milk_reminder",
                title=title,
                body=_text(payload, "body"),
                source="agent_action",
                payload={
                    **reminder_payload,
                    "agent_action_id": str(action.id),
                    "agent_run_id": str(action.run_id),
                },
                request_id=f"agent-action:{action.id}",
                idempotency_key=action.idempotency_key or f"agent-action:{action.id}",
                actor_service="agent_runtime",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return AgentActionApplyResult(
            resource_type="notification",
            resource_id=str(notification.id),
            details={
                "notification_type": notification.notification_type,
                "agent_action_id": str(action.id),
                "agent_run_id": str(action.run_id),
            },
        )


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()
