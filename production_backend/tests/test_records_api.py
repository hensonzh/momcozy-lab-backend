from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from production_backend.app.modules.records.router import get_records_service


def test_create_feeding_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).post(
        "/v1/records/feeding",
        json={"feed_time": _now_iso(), "feed_type": "bottle", "volume_ml": 90},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_create_feeding_uses_current_user_request_id_and_idempotency() -> None:
    user_id = uuid4()
    fake_service = FakeRecordsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_records_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/records/feeding",
        headers={"X-Request-ID": "req_feed", "Idempotency-Key": " idem-feed "},
        json={"feed_time": _now_iso(), "feed_type": "bottle", "volume_ml": 90},
    )

    assert response.status_code == 201
    assert fake_service.create_feeding_kwargs["owner_user_id"] == user_id
    assert fake_service.create_feeding_kwargs["request_id"] == "req_feed"
    assert fake_service.create_feeding_kwargs["idempotency_key"] == "idem-feed"


def test_list_feedings_uses_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeRecordsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_records_service] = lambda: fake_service

    response = TestClient(app).get("/v1/records/feeding?limit=10")

    assert response.status_code == 200
    assert response.json()["items"][0]["owner_user_id"] == str(user_id)
    assert fake_service.list_feedings_kwargs["owner_user_id"] == user_id
    assert fake_service.list_feedings_kwargs["limit"] == 10


def test_delete_feeding_uses_current_user_and_request_id() -> None:
    user_id = uuid4()
    fake_service = FakeRecordsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_records_service] = lambda: fake_service

    response = TestClient(app).delete(
        f"/v1/records/feeding/{fake_service.record_id}",
        headers={"X-Request-ID": "req_delete"},
    )

    assert response.status_code == 204
    assert fake_service.delete_feeding_kwargs["owner_user_id"] == user_id
    assert fake_service.delete_feeding_kwargs["request_id"] == "req_delete"


def test_create_pumping_uses_current_user_request_id_and_idempotency() -> None:
    user_id = uuid4()
    fake_service = FakeRecordsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_records_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/records/pumping",
        headers={"X-Request-ID": "req_pump", "Idempotency-Key": " idem-pump "},
        json={"pump_start_time": _now_iso(), "milk_volume_ml": 120},
    )

    assert response.status_code == 201
    assert fake_service.create_pumping_kwargs["owner_user_id"] == user_id
    assert fake_service.create_pumping_kwargs["request_id"] == "req_pump"
    assert fake_service.create_pumping_kwargs["idempotency_key"] == "idem-pump"


def test_list_and_delete_pumping_use_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeRecordsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_records_service] = lambda: fake_service

    list_response = TestClient(app).get("/v1/records/pumping?limit=10")
    delete_response = TestClient(app).delete(
        f"/v1/records/pumping/{fake_service.record_id}",
        headers={"X-Request-ID": "req_delete"},
    )

    assert list_response.status_code == 200
    assert delete_response.status_code == 204
    assert fake_service.list_pumpings_kwargs["owner_user_id"] == user_id
    assert fake_service.list_pumpings_kwargs["limit"] == 10
    assert fake_service.delete_pumping_kwargs["request_id"] == "req_delete"


def test_create_growth_uses_current_user_request_id_and_idempotency() -> None:
    user_id = uuid4()
    fake_service = FakeRecordsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_records_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/records/growth",
        headers={"X-Request-ID": "req_growth", "Idempotency-Key": " idem-growth "},
        json={"measured_at": _now_iso(), "weight_kg": 4.2},
    )

    assert response.status_code == 201
    assert fake_service.create_growth_kwargs["owner_user_id"] == user_id
    assert fake_service.create_growth_kwargs["request_id"] == "req_growth"
    assert fake_service.create_growth_kwargs["idempotency_key"] == "idem-growth"


def test_list_and_delete_growth_use_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeRecordsService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_records_service] = lambda: fake_service

    list_response = TestClient(app).get("/v1/records/growth?limit=10")
    delete_response = TestClient(app).delete(
        f"/v1/records/growth/{fake_service.record_id}",
        headers={"X-Request-ID": "req_delete"},
    )

    assert list_response.status_code == 200
    assert delete_response.status_code == 204
    assert fake_service.list_growth_kwargs["owner_user_id"] == user_id
    assert fake_service.list_growth_kwargs["limit"] == 10
    assert fake_service.delete_growth_kwargs["request_id"] == "req_delete"


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


def _now() -> datetime:
    return datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class FakeRecordsService:
    def __init__(self, *, user_id: UUID) -> None:
        self.user_id = user_id
        self.record_id = uuid4()
        self.create_feeding_kwargs = {}
        self.create_pumping_kwargs = {}
        self.create_growth_kwargs = {}
        self.list_feedings_kwargs = {}
        self.list_pumpings_kwargs = {}
        self.list_growth_kwargs = {}
        self.delete_feeding_kwargs = {}
        self.delete_pumping_kwargs = {}
        self.delete_growth_kwargs = {}

    async def create_feeding(self, **kwargs):
        self.create_feeding_kwargs = kwargs
        return self._feeding()

    async def list_feedings(self, **kwargs):
        self.list_feedings_kwargs = kwargs
        return [self._feeding()]

    async def delete_feeding(self, **kwargs):
        self.delete_feeding_kwargs = kwargs

    async def create_pumping(self, **kwargs):
        self.create_pumping_kwargs = kwargs
        return self._pumping()

    async def list_pumpings(self, **kwargs):
        self.list_pumpings_kwargs = kwargs
        return [self._pumping()]

    async def delete_pumping(self, **kwargs):
        self.delete_pumping_kwargs = kwargs

    async def create_growth(self, **kwargs):
        self.create_growth_kwargs = kwargs
        return self._growth()

    async def list_growth(self, **kwargs):
        self.list_growth_kwargs = kwargs
        return [self._growth()]

    async def delete_growth(self, **kwargs):
        self.delete_growth_kwargs = kwargs

    def _feeding(self) -> FeedingRecord:
        return FeedingRecord(
            id=self.record_id,
            owner_user_id=self.user_id,
            infant_id=None,
            feed_time=_now(),
            feed_type="bottle",
            feed_action="",
            volume_ml=90,
            duration_seconds=None,
            title="",
            status="active",
        )

    def _growth(self) -> GrowthRecord:
        return GrowthRecord(
            id=self.record_id,
            owner_user_id=self.user_id,
            infant_id=None,
            measured_at=_now(),
            height_cm=None,
            weight_kg=4.2,
            head_cm=None,
            status="active",
        )

    def _pumping(self) -> PumpingRecord:
        return PumpingRecord(
            id=self.record_id,
            owner_user_id=self.user_id,
            pump_start_time=_now(),
            pump_end_time=None,
            milk_volume_ml=120,
            pump_type="",
            duration_seconds=None,
            source="manual",
            title="",
            status="active",
        )
