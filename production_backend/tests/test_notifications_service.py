import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.notifications.models import Notification
from production_backend.app.modules.notifications.service import NotificationsService


def test_notifications_service_creates_notification_with_idempotency_and_audit() -> None:
    owner_user_id = uuid4()
    repository = FakeNotificationsRepository()
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = NotificationsService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    notification = asyncio.run(
        service.create_notification(
            owner_user_id=owner_user_id,
            notification_type="feeding_due",
            title="Feeding reminder",
            body="Bottle is due",
            payload={"infant_id": "baby-1"},
            request_id="req_notify",
            idempotency_key="idem-notify",
            actor_service="notification-service",
        )
    )

    assert notification.owner_user_id == owner_user_id
    assert idempotency_service.reserve_kwargs["scope"] == "notifications.create"
    assert idempotency_service.completed_response_ref == str(notification.id)
    assert audit_service.record_kwargs["actor_user_id"] is None
    assert audit_service.record_kwargs["actor_type"] == "service"
    assert audit_service.record_kwargs["actor_service"] == "notification-service"
    assert audit_service.record_kwargs["details"]["owner_user_id"] == str(owner_user_id)


def test_notifications_service_lists_marks_read_and_archives() -> None:
    owner_user_id = uuid4()
    notification = _notification(owner_user_id=owner_user_id)
    repository = FakeNotificationsRepository(notification=notification)
    audit_service = FakeAuditService()
    service = NotificationsService(repository=repository, audit_service=audit_service)

    notifications = asyncio.run(service.list_notifications(owner_user_id=owner_user_id, limit=10))
    read = asyncio.run(service.set_read_state(owner_user_id=owner_user_id, notification_id=notification.id, request_id="req_read"))
    assert read.status == "read"
    assert read.read_at is not None

    asyncio.run(service.archive_notification(owner_user_id=owner_user_id, notification_id=notification.id, request_id="req_archive"))

    assert notifications == [notification]
    assert notification.status == "archived"
    assert audit_service.record_kwargs["action"] == "notifications.archive"


def _notification(*, owner_user_id: UUID) -> Notification:
    return Notification(
        id=uuid4(),
        owner_user_id=owner_user_id,
        notification_type="feeding_due",
        title="Feeding reminder",
        body="Bottle is due",
        status="unread",
        source="system",
        payload={},
    )


class FakeNotificationsRepository:
    def __init__(self, *, notification=None) -> None:
        self.notification = notification

    async def create_notification(self, **kwargs):
        self.notification = _notification(owner_user_id=kwargs["owner_user_id"])
        self.notification.notification_type = kwargs["notification_type"]
        self.notification.title = kwargs["title"]
        self.notification.body = kwargs["body"]
        self.notification.payload = kwargs["payload"]
        return self.notification

    async def get_for_owner(self, **kwargs):
        return self.notification

    async def list_for_owner(self, **kwargs):
        return [self.notification] if self.notification else []

    async def set_read_state(self, **kwargs):
        if self.notification is None:
            return None
        self.notification.status = "read" if kwargs["read"] else "unread"
        self.notification.read_at = kwargs["read_at"]
        return self.notification

    async def archive(self, **kwargs):
        if self.notification is None:
            return None
        self.notification.status = "archived"
        return self.notification


class FakeIdempotencyService:
    def __init__(self, *, status: str, response_ref: str = "") -> None:
        self.status = status
        self.reserve_kwargs = {}
        self.completed_response_ref = ""
        self.record = IdempotencyKey(
            actor_user_id=uuid4(),
            scope="notifications.create",
            key="idem-notify",
            request_hash="hash",
            response_ref=response_ref,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def reserve(self, **kwargs):
        self.reserve_kwargs = kwargs
        return FakeIdempotencyDecision(status=self.status, record=self.record)

    async def mark_completed(self, *, record, response_ref: str):
        self.completed_response_ref = response_ref
        record.response_ref = response_ref
        return record


class FakeIdempotencyDecision:
    def __init__(self, *, status: str, record) -> None:
        self.status = status
        self.record = record


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None
