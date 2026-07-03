import asyncio
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.notifications.models import Notification
from production_backend.app.modules.notifications.service import NotificationsService


def test_notification_inbox_main_flow_tracks_read_archive_and_owner_scope() -> None:
    asyncio.run(_run_notification_inbox_main_flow())


async def _run_notification_inbox_main_flow() -> None:
    owner_user_id = uuid4()
    other_user_id = uuid4()
    repository = InMemoryNotificationsRepository()
    audit_service = FlowAuditService()
    service = NotificationsService(repository=repository, audit_service=audit_service)

    feeding_due = await service.create_notification(
        owner_user_id=owner_user_id,
        notification_type="feeding_due",
        title="Feeding due",
        body="Bottle is due",
        source="scheduler",
        payload={"kind": "feeding"},
        request_id="req_notify_feeding",
        actor_service="scheduler",
    )
    pumping_due = await service.create_notification(
        owner_user_id=owner_user_id,
        notification_type="pumping_due",
        title="Pumping due",
        body="Pump session is due",
        source="scheduler",
        payload={"kind": "pumping"},
        request_id="req_notify_pumping",
        actor_service="scheduler",
    )
    await service.create_notification(
        owner_user_id=other_user_id,
        notification_type="feeding_due",
        title="Other feeding due",
        body="Other user reminder",
        source="scheduler",
    )

    unread = await service.list_notifications(owner_user_id=owner_user_id, status="unread", limit=10)
    feeding_only = await service.list_notifications(
        owner_user_id=owner_user_id,
        notification_type="feeding_due",
        limit=10,
    )
    read = await service.set_read_state(
        owner_user_id=owner_user_id,
        notification_id=feeding_due.id,
        read=True,
        request_id="req_read",
    )
    unread_after_read = await service.list_notifications(owner_user_id=owner_user_id, status="unread", limit=10)
    await service.archive_notification(
        owner_user_id=owner_user_id,
        notification_id=pumping_due.id,
        request_id="req_archive",
    )
    visible_after_archive = await service.list_notifications(owner_user_id=owner_user_id, limit=10)

    with pytest.raises(ApiError) as cross_user_read:
        await service.set_read_state(
            owner_user_id=other_user_id,
            notification_id=feeding_due.id,
            read=False,
            request_id="req_cross_read",
        )
    with pytest.raises(ApiError) as cross_user_archive:
        await service.archive_notification(
            owner_user_id=other_user_id,
            notification_id=feeding_due.id,
            request_id="req_cross_archive",
        )

    assert [item.id for item in unread] == [pumping_due.id, feeding_due.id]
    assert [item.id for item in feeding_only] == [feeding_due.id]
    assert read.status == "read"
    assert read.read_at is not None
    assert [item.id for item in unread_after_read] == [pumping_due.id]
    assert [item.id for item in visible_after_archive] == [feeding_due.id]
    assert cross_user_read.value.code == "not_found"
    assert cross_user_archive.value.code == "not_found"
    assert [entry["action"] for entry in audit_service.entries] == [
        "notifications.create",
        "notifications.create",
        "notifications.create",
        "notifications.read",
        "notifications.archive",
    ]
    assert audit_service.entries[0]["actor_type"] == "service"
    assert audit_service.entries[-1]["actor_user_id"] == owner_user_id


class InMemoryNotificationsRepository:
    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    async def create_notification(self, **kwargs):
        notification = Notification(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            notification_type=kwargs["notification_type"],
            title=kwargs["title"],
            body=kwargs["body"],
            status="unread",
            source=kwargs["source"],
            payload=kwargs["payload"],
            delivered_at=kwargs["delivered_at"],
            read_at=None,
        )
        self.notifications.append(notification)
        return notification

    async def get_for_owner(self, *, notification_id: UUID, owner_user_id: UUID):
        return next(
            (
                notification
                for notification in self.notifications
                if notification.id == notification_id
                and notification.owner_user_id == owner_user_id
                and notification.status != "archived"
            ),
            None,
        )

    async def list_for_owner(self, *, owner_user_id: UUID, status: str | None, notification_type: str | None, limit: int):
        notifications = [
            notification
            for notification in self.notifications
            if notification.owner_user_id == owner_user_id
            and (notification.status == status if status else notification.status != "archived")
            and (notification_type is None or notification.notification_type == notification_type)
        ]
        return list(reversed(notifications))[:limit]

    async def set_read_state(self, *, notification_id: UUID, owner_user_id: UUID, read: bool, read_at):
        notification = await self.get_for_owner(notification_id=notification_id, owner_user_id=owner_user_id)
        if notification is None:
            return None
        notification.status = "read" if read else "unread"
        notification.read_at = read_at if read else None
        return notification

    async def archive(self, *, notification_id: UUID, owner_user_id: UUID):
        notification = await self.get_for_owner(notification_id=notification_id, owner_user_id=owner_user_id)
        if notification is None:
            return None
        notification.status = "archived"
        return notification


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
