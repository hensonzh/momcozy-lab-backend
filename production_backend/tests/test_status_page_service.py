import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from production_backend.app.modules.status_page.service import StatusPageService


def test_status_page_service_builds_today_snapshot_from_domain_services() -> None:
    owner_user_id = uuid4()
    day = date(2026, 7, 3)
    profile_service = FakeProfileService()
    records_service = FakeRecordsService()
    plans_service = FakePlansService()
    notifications_service = FakeNotificationsService()
    service = StatusPageService(
        profile_service=profile_service,
        records_service=records_service,
        plans_service=plans_service,
        notifications_service=notifications_service,
    )

    snapshot = asyncio.run(service.get_today_status(owner_user_id=owner_user_id, day=day))

    assert snapshot.date == day
    assert snapshot.profile.display_name == "Lute"
    assert snapshot.infant_count == 1
    assert snapshot.feeding_count == 2
    assert snapshot.pumping_count == 2
    assert snapshot.pumped_milk_volume_ml == 125.5
    assert snapshot.task_count == 3
    assert snapshot.completed_task_count == 1
    assert snapshot.pending_task_count == 2
    assert snapshot.unread_notification_count == 2
    assert records_service.feedings_kwargs["owner_user_id"] == owner_user_id
    assert records_service.feedings_kwargs["start_at"] == datetime(2026, 7, 3, tzinfo=timezone.utc)
    assert records_service.feedings_kwargs["end_at"] == datetime(2026, 7, 4, tzinfo=timezone.utc)
    assert plans_service.tasks_kwargs == {"owner_user_id": owner_user_id, "task_date": day, "limit": 100}
    assert notifications_service.notifications_kwargs == {"owner_user_id": owner_user_id, "status": "unread", "limit": 50}


class FakeProfileService:
    async def get_user_profile(self, **kwargs):
        return SimpleNamespace(
            display_name="Lute",
            daily_summary="Steady day",
            lactation_advice="Keep it gentle",
            feeding_advice="Watch cues",
        )

    async def list_infants(self, **kwargs):
        return [SimpleNamespace(id=uuid4())]


class FakeRecordsService:
    def __init__(self) -> None:
        self.feedings_kwargs = {}
        self.pumpings_kwargs = {}

    async def list_feedings(self, **kwargs):
        self.feedings_kwargs = kwargs
        return [SimpleNamespace(), SimpleNamespace()]

    async def list_pumpings(self, **kwargs):
        self.pumpings_kwargs = kwargs
        return [SimpleNamespace(milk_volume_ml=90.0), SimpleNamespace(milk_volume_ml=35.5)]


class FakePlansService:
    def __init__(self) -> None:
        self.tasks_kwargs = {}

    async def list_tasks(self, **kwargs):
        self.tasks_kwargs = kwargs
        return [
            SimpleNamespace(status="completed", completed_at=datetime(2026, 7, 3, tzinfo=timezone.utc)),
            SimpleNamespace(status="pending", completed_at=None),
            SimpleNamespace(status="pending", completed_at=None),
        ]


class FakeNotificationsService:
    def __init__(self) -> None:
        self.notifications_kwargs = {}

    async def list_notifications(self, **kwargs):
        self.notifications_kwargs = kwargs
        return [SimpleNamespace(), SimpleNamespace()]
