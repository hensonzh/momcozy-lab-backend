import asyncio
from uuid import uuid4

import pytest

from app.modules.agent_runtime.models import AgentAction
from app.modules.notifications.agent_actions import (
    MILK_REMINDER_CREATE_ACTION,
    MilkReminderCreateActionHandler,
)
from app.modules.notifications.models import Notification
from app.workers.errors import PermanentJobError


def test_milk_reminder_create_action_handler_creates_notification_through_service() -> None:
    service = FakeNotificationsService()
    action = _action(
        apply_payload={
            "title": "Time to pump",
            "body": "A short evening pumping session is due.",
            "remind_at": "2026-07-04T20:00:00+08:00",
            "payload": {"routine": "evening"},
        }
    )

    result = asyncio.run(MilkReminderCreateActionHandler(service=service)(action))

    assert result.resource_type == "notification"
    assert result.resource_id == str(service.notification.id)
    assert result.details == {
        "notification_type": "milk_reminder",
        "agent_action_id": str(action.id),
        "agent_run_id": str(action.run_id),
    }
    assert service.create_notification_kwargs["owner_user_id"] == action.actor_user_id
    assert service.create_notification_kwargs["notification_type"] == "milk_reminder"
    assert service.create_notification_kwargs["title"] == "Time to pump"
    assert service.create_notification_kwargs["body"] == "A short evening pumping session is due."
    assert service.create_notification_kwargs["source"] == "agent_action"
    assert service.create_notification_kwargs["payload"]["routine"] == "evening"
    assert service.create_notification_kwargs["payload"]["remind_at"] == "2026-07-04T20:00:00+08:00"
    assert service.create_notification_kwargs["payload"]["agent_action_id"] == str(action.id)
    assert service.create_notification_kwargs["idempotency_key"] == "idem-action"
    assert service.create_notification_kwargs["actor_service"] == "agent_runtime"


def test_milk_reminder_create_action_handler_rejects_missing_title() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(MilkReminderCreateActionHandler(service=FakeNotificationsService())(_action(apply_payload={})))

    assert exc_info.value.code == "missing_reminder_title"


class FakeNotificationsService:
    def __init__(self) -> None:
        self.notification = Notification(
            id=uuid4(),
            owner_user_id=uuid4(),
            notification_type="milk_reminder",
            title="Time to pump",
            body="A short evening pumping session is due.",
            source="agent_action",
            payload={},
        )
        self.create_notification_kwargs = {}

    async def create_notification(self, **kwargs):
        self.create_notification_kwargs = kwargs
        self.notification.owner_user_id = kwargs["owner_user_id"]
        self.notification.notification_type = kwargs["notification_type"]
        self.notification.title = kwargs["title"]
        self.notification.body = kwargs["body"]
        self.notification.source = kwargs["source"]
        self.notification.payload = kwargs["payload"]
        return self.notification


def _action(*, apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=MILK_REMINDER_CREATE_ACTION,
        target_type="notification",
        target_id="",
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
