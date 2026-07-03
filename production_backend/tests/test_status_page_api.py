from datetime import date
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.status_page.router import get_status_page_service
from production_backend.app.modules.status_page.schemas import StatusPageTodayRead, StatusProfileSummary


def test_status_page_today_uses_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeStatusPageService()
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_status_page_service] = lambda: fake_service

    response = TestClient(app).get("/v1/status-page/today?day=2026-07-03")

    assert response.status_code == 200
    assert response.json()["date"] == "2026-07-03"
    assert response.json()["pumped_milk_volume_ml"] == 125.5
    assert fake_service.today_kwargs == {"owner_user_id": user_id, "day": date(2026, 7, 3)}


def _override_current_user(app, user_id: UUID) -> None:
    from production_backend.app.api.dependencies import require_current_user

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


class FakeStatusPageService:
    def __init__(self) -> None:
        self.today_kwargs = {}

    async def get_today_status(self, **kwargs):
        self.today_kwargs = kwargs
        return StatusPageTodayRead(
            date=kwargs["day"],
            profile=StatusProfileSummary(display_name="Lute", daily_summary="Steady day"),
            infant_count=1,
            feeding_count=2,
            pumping_count=2,
            pumped_milk_volume_ml=125.5,
            task_count=3,
            completed_task_count=1,
            pending_task_count=2,
            unread_notification_count=2,
        )
