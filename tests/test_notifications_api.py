from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser
from app.modules.notifications.models import Notification
from app.modules.notifications.router import get_notifications_service


def test_notifications_require_current_user_for_inbox() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).get("/v1/notifications")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_notification_inbox_and_state_changes_use_current_user_scope() -> None:
    user_id = uuid4()
    notification_id = uuid4()
    fake_service = FakeNotificationsService(user_id=user_id, notification_id=notification_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_notifications_service] = lambda: fake_service

    list_response = TestClient(app).get("/v1/notifications?status=unread&limit=10")
    read_response = TestClient(app).patch(
        f"/v1/notifications/{notification_id}/read",
        headers={"X-Request-ID": "req_read"},
        json={"read": True},
    )
    delete_response = TestClient(app).delete(
        f"/v1/notifications/{notification_id}",
        headers={"X-Request-ID": "req_archive"},
    )

    assert list_response.status_code == 200
    assert read_response.status_code == 200
    assert delete_response.status_code == 204
    assert fake_service.list_kwargs["owner_user_id"] == user_id
    assert fake_service.list_kwargs["limit"] == 10
    assert fake_service.read_kwargs["owner_user_id"] == user_id
    assert fake_service.read_kwargs["request_id"] == "req_read"
    assert fake_service.archive_kwargs["request_id"] == "req_archive"


def _override_current_user(app, user_id: UUID) -> None:
    from app.api.dependencies import require_current_user

    async def fake_current_user() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            subject=str(user_id),
            session_id="session",
            token_id="token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_current_user


class FakeNotificationsService:
    def __init__(self, *, user_id: UUID, notification_id: UUID | None = None) -> None:
        self.user_id = user_id
        self.notification_id = notification_id or uuid4()
        self.create_kwargs = {}
        self.list_kwargs = {}
        self.read_kwargs = {}
        self.archive_kwargs = {}

    async def create_notification(self, **kwargs):
        self.create_kwargs = kwargs
        return self._notification()

    async def list_notifications(self, **kwargs):
        self.list_kwargs = kwargs
        return [self._notification()]

    async def list_page(self, **kwargs):
        from app.modules.notifications.schemas import NotificationListResponse, NotificationRead
        self.list_kwargs = kwargs
        return NotificationListResponse(items=[NotificationRead.model_validate(self._notification())], unread_count=1)

    async def set_read_state(self, **kwargs):
        self.read_kwargs = kwargs
        notification = self._notification()
        notification.status = "read"
        return notification

    async def archive_notification(self, **kwargs):
        self.archive_kwargs = kwargs

    def _notification(self) -> Notification:
        return Notification(
            id=self.notification_id,
            owner_user_id=self.user_id,
            notification_type="feeding_due",
            title="Feeding reminder",
            body="Bottle is due",
            status="unread",
            source="system",
            payload={},
        )
