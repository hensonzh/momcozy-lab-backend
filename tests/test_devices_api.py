from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser
from app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from app.modules.devices.router import get_devices_service


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


def test_pump_workstate_flow_uses_current_user_idempotency_and_latest_state() -> None:
    user_id = uuid4()
    fake_service = FakeDevicesService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_devices_service] = lambda: fake_service
    client = TestClient(app)

    create_response = client.post(
        "/v1/devices/pump-workstate",
        headers={"Idempotency-Key": " idem-workstate ", "X-Request-ID": "req_workstate"},
        json={
            "device_id": "pump-1",
            "occurred_at": _now_iso(),
            "source": "device",
            "state": {"mode": "stimulation", "speed": 3},
        },
    )
    latest_response = client.get("/v1/devices/pump-workstate/latest?device_id=pump-1")

    assert create_response.status_code == 201
    assert create_response.json()["state"] == {"mode": "stimulation", "speed": 3}
    assert latest_response.status_code == 200
    assert latest_response.json()["state"] == {"mode": "stimulation", "speed": 3}
    assert fake_service.create_workstate_kwargs["owner_user_id"] == user_id
    assert fake_service.create_workstate_kwargs["idempotency_key"] == "idem-workstate"
    assert fake_service.create_workstate_kwargs["request_id"] == "req_workstate"
    assert fake_service.latest_workstate_kwargs == {"owner_user_id": user_id, "device_id": "pump-1"}


def test_pump_device_fact_flow_uses_current_user_and_typed_contracts() -> None:
    user_id = uuid4()
    fake_service = FakeDevicesService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_devices_service] = lambda: fake_service
    client = TestClient(app)

    threshold_response = client.post(
        "/v1/devices/pump-threshold",
        headers={"Idempotency-Key": " idem-threshold "},
        json={
            "device_id": "pump-1",
            "occurred_at": _now_iso(),
            "source": "device",
            "stimulate_level_l": 1,
            "deep_level_l": 2,
            "stimulate_level_r": 3,
            "deep_level_r": 4,
        },
    )
    health_response = client.post(
        "/v1/devices/pump-health",
        headers={"Idempotency-Key": " idem-health "},
        json={"device_id": "pump-1", "occurred_at": _now_iso(), "source": "device", "health_l": 1, "health_r": 2},
    )
    latest_threshold_response = client.get("/v1/devices/pump-threshold/latest?device_id=pump-1")
    latest_health_response = client.get("/v1/devices/pump-health/latest?device_id=pump-1")
    energy_response = client.get("/v1/devices/pump-energy-target")

    assert threshold_response.status_code == 201
    assert threshold_response.json()["deep_level_r"] == 4
    assert health_response.status_code == 201
    assert health_response.json()["health_r"] == 2
    assert latest_threshold_response.status_code == 200
    assert latest_threshold_response.json()["stimulate_level_l"] == 1
    assert latest_health_response.status_code == 200
    assert latest_health_response.json()["health_l"] == 1
    assert energy_response.status_code == 200
    assert energy_response.json() == {"lower_value": 80, "upper_value": 100}
    assert fake_service.create_threshold_kwargs["owner_user_id"] == user_id
    assert fake_service.create_threshold_kwargs["idempotency_key"] == "idem-threshold"
    assert fake_service.create_health_kwargs["owner_user_id"] == user_id
    assert fake_service.create_health_kwargs["idempotency_key"] == "idem-health"
    assert fake_service.latest_threshold_kwargs == {"owner_user_id": user_id, "device_id": "pump-1"}
    assert fake_service.latest_health_kwargs == {"owner_user_id": user_id, "device_id": "pump-1"}


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
        self.create_workstate_kwargs = {}
        self.latest_workstate_kwargs = {}
        self.create_threshold_kwargs = {}
        self.latest_threshold_kwargs = {}
        self.create_health_kwargs = {}
        self.latest_health_kwargs = {}

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

    async def create_pump_workstate(self, **kwargs):
        self.create_workstate_kwargs = kwargs
        event = self._event()
        event.payload = {"source": kwargs["source"], "state": kwargs["state"]}
        return event

    async def get_latest_pump_workstate(self, **kwargs):
        self.latest_workstate_kwargs = kwargs
        event = self._event()
        event.payload = {"source": "device", "state": {"mode": "stimulation", "speed": 3}}
        return event

    async def create_pump_threshold(self, **kwargs):
        self.create_threshold_kwargs = kwargs
        event = self._event()
        event.event_type = "threshold"
        event.payload = {
            "source": kwargs["source"],
            "stimulate_level_l": kwargs["stimulate_level_l"],
            "deep_level_l": kwargs["deep_level_l"],
            "stimulate_level_r": kwargs["stimulate_level_r"],
            "deep_level_r": kwargs["deep_level_r"],
        }
        return event

    async def get_latest_pump_threshold(self, **kwargs):
        self.latest_threshold_kwargs = kwargs
        event = self._event()
        event.event_type = "threshold"
        event.payload = {"source": "device", "stimulate_level_l": 1, "deep_level_l": 2, "stimulate_level_r": 3, "deep_level_r": 4}
        return event

    async def create_pump_health(self, **kwargs):
        self.create_health_kwargs = kwargs
        event = self._event()
        event.event_type = "health"
        event.payload = {"source": kwargs["source"], "health_l": kwargs["health_l"], "health_r": kwargs["health_r"]}
        return event

    async def get_latest_pump_health(self, **kwargs):
        self.latest_health_kwargs = kwargs
        event = self._event()
        event.event_type = "health"
        event.payload = {"source": "device", "health_l": 1, "health_r": 2}
        return event

    def get_pump_energy_target(self):
        return {"lower_value": 80, "upper_value": 100}

    def _device(self) -> PumpDevice:
        return PumpDevice(id=uuid4(), owner_user_id=self.user_id, device_id="pump-1", model="M1", firmware_version="", status="active")

    def _event(self) -> PumpTelemetryEvent:
        return PumpTelemetryEvent(id=uuid4(), owner_user_id=self.user_id, device_id="pump-1", event_type="workstate", occurred_at=_now(), payload={})
