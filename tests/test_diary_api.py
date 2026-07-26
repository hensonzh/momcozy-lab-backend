from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser
from app.modules.diary.models import DiaryEntry
from app.modules.diary.router import get_diary_service


def test_diary_entries_require_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).get("/v1/pregnancy-diary/entries")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_create_and_update_entry_use_current_user_and_request_id() -> None:
    user_id = uuid4()
    fake_service = FakeDiaryService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_diary_service] = lambda: fake_service

    create_response = TestClient(app).post(
        "/v1/pregnancy-diary/entries",
        headers={"X-Request-ID": "req_diary"},
        json={"entry_date": "2026-07-02", "mood": "calm"},
    )
    update_response = TestClient(app).patch(
        "/v1/pregnancy-diary/entries/2026-07-02",
        headers={"X-Request-ID": "req_update"},
        json={"content": "updated"},
    )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    assert fake_service.create_kwargs["owner_user_id"] == user_id
    assert "diary_type" not in fake_service.create_kwargs
    assert fake_service.create_kwargs["entry_date"] == date(2026, 7, 2)
    assert fake_service.create_kwargs["request_id"] == "req_diary"
    assert fake_service.create_kwargs["values"] == {"attributes": {"mood": "calm"}}
    assert fake_service.update_kwargs["owner_user_id"] == user_id
    assert "diary_type" not in fake_service.update_kwargs
    assert fake_service.update_kwargs["entry_date"] == date(2026, 7, 2)
    assert fake_service.update_kwargs["request_id"] == "req_update"


def test_list_and_delete_entries_use_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeDiaryService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_diary_service] = lambda: fake_service

    list_response = TestClient(app).get("/v1/pregnancy-diary/entries?limit=10")
    delete_response = TestClient(app).delete(
        "/v1/pregnancy-diary/entries/2026-07-02",
        headers={"X-Request-ID": "req_delete"},
    )

    assert list_response.status_code == 200
    assert delete_response.status_code == 204
    assert fake_service.list_kwargs["owner_user_id"] == user_id
    assert "diary_type" not in fake_service.list_kwargs
    assert fake_service.list_kwargs["limit"] == 10
    assert "diary_type" not in fake_service.delete_kwargs
    assert fake_service.delete_kwargs["request_id"] == "req_delete"


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


class FakeDiaryService:
    def __init__(self, *, user_id: UUID) -> None:
        self.user_id = user_id
        self.create_kwargs = {}
        self.update_kwargs = {}
        self.list_kwargs = {}
        self.delete_kwargs = {}

    async def get_entry(self, **kwargs):
        return self._entry()

    async def list_entries(self, **kwargs):
        self.list_kwargs = kwargs
        return [self._entry()]

    async def create_entry(self, **kwargs):
        self.create_kwargs = kwargs
        return self._entry()

    async def update_entry(self, **kwargs):
        self.update_kwargs = kwargs
        return self._entry()

    async def delete_entry(self, **kwargs):
        self.delete_kwargs = kwargs

    def _entry(self) -> DiaryEntry:
        return DiaryEntry(
            id=uuid4(),
            owner_user_id=self.user_id,
            entry_date=date(2026, 7, 2),
            attributes={"mood": "calm"},
            status="active",
            attachments=[],
            created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
