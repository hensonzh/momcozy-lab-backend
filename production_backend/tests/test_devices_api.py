from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from production_backend.app.modules.devices.router import get_devices_service


def test_pump_device_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).get("/v1/devices/pumps")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_upsert_pump_device_uses_current_user_and_request_id() -> None:
    user_id = uuid4()
    fake_service = FakeDevicesService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_devices_service] = lambda: fake_service

    response = TestClient(app).put(
        "/v1/devices/pumps/pump-1",
        headers={"X-Request-ID": "req_device"},
        json={"model": "M1"},
    )

    assert response.status_code == 200
    assert fake_service.upsert_kwargs["owner_user_id"] == user_id
    assert fake_service.upsert_kwargs["request_id"] == "req_device"


def test_telemetry_create_and_list_use_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeDevicesService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_devices_service] = lambda: fake_service

    create_response = TestClient(app).post(
        "/v1/devices/pump-telemetry",
        headers={"Idempotency-Key": " idem-event "},
        json={"device_id": "pump-1", "event_type": "workstate", "occurred_at": _now_iso(), "payload": {"speed": 3}},
    )
    list_response = TestClient(app).get("/v1/devices/pump-telemetry?device_id=pump-1&limit=10")

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert fake_service.create_event_kwargs["owner_user_id"] == user_id
    assert fake_service.create_event_kwargs["idempotency_key"] == "idem-event"
    assert fake_service.list_events_kwargs["limit"] == 10


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
    return datetime(2026, 7, 2, tzinfo=timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class FakeDevicesService:
    def __init__(self, *, user_id: UUID) -> None:
        self.user_id = user_id
        self.upsert_kwargs = {}
        self.create_event_kwargs = {}
        self.list_events_kwargs = {}

    async def upsert_device(self, **kwargs):
        self.upsert_kwargs = kwargs
        return self._device()

    async def list_devices(self, **kwargs):
        return [self._device()]

    async def create_telemetry_event(self, **kwargs):
        self.create_event_kwargs = kwargs
        return self._event()

    async def list_telemetry_events(self, **kwargs):
        self.list_events_kwargs = kwargs
        return [self._event()]

    def _device(self) -> PumpDevice:
        return PumpDevice(id=uuid4(), owner_user_id=self.user_id, device_id="pump-1", model="M1", firmware_version="", status="active")

    def _event(self) -> PumpTelemetryEvent:
        return PumpTelemetryEvent(id=uuid4(), owner_user_id=self.user_id, device_id="pump-1", event_type="workstate", occurred_at=_now(), payload={})
