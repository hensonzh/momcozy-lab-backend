import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.audit.service import request_hash
from app.modules.audit.models import IdempotencyKey
from app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from app.modules.devices.service import DevicesService


def test_devices_service_upserts_device_and_records_audit() -> None:
    owner_user_id = uuid4()
    repository = FakeDevicesRepository()
    audit_service = FakeAuditService()
    service = DevicesService(repository=repository, audit_service=audit_service)

    device = asyncio.run(
        service.upsert_device(owner_user_id=owner_user_id, device_id="pump-1", model="M1", request_id="req_device")
    )

    assert device.owner_user_id == owner_user_id
    assert audit_service.record_kwargs["action"] == "devices.pump.upsert"


def test_devices_service_creates_telemetry_with_idempotency() -> None:
    owner_user_id = uuid4()
    repository = FakeDevicesRepository(device=_device(owner_user_id=owner_user_id))
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = DevicesService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    event = asyncio.run(
        service.create_telemetry_event(
            owner_user_id=owner_user_id,
            device_id="pump-1",
            event_type="workstate",
            occurred_at=_now(),
            payload={"speed": 3},
            idempotency_key="idem-event",
        )
    )

    assert event.event_type == "workstate"
    assert idempotency_service.reserve_kwargs["scope"] == "devices.telemetry.create"
    assert idempotency_service.completed_response_ref == str(event.id)


def test_devices_service_reserves_idempotency_before_auto_upserting_device() -> None:
    owner_user_id = uuid4()
    repository = FakeDevicesRepository()
    service = DevicesService(
        repository=repository,
        idempotency_service=ConflictIdempotencyService(),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.create_telemetry_event(
                owner_user_id=owner_user_id,
                device_id="pump-new",
                event_type="workstate",
                occurred_at=_now(),
                payload={"speed": 3},
                idempotency_key="idem-event",
            )
        )

    assert exc_info.value.code == "idempotency_conflict"
    assert repository.upsert_count == 0
    assert repository.event is None


def test_devices_service_creates_pump_workstate_as_typed_experience_flow() -> None:
    owner_user_id = uuid4()
    repository = FakeDevicesRepository(device=_device(owner_user_id=owner_user_id))
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = DevicesService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    event = asyncio.run(
        service.create_pump_workstate(
            owner_user_id=owner_user_id,
            device_id="pump-1",
            occurred_at=_now(),
            state={"mode": "stimulation", "speed": 3, "side": "left"},
            source="device",
            idempotency_key="idem-workstate",
            request_id="req_workstate",
        )
    )

    assert event.event_type == "workstate"
    assert event.payload == {"source": "device", "state": {"mode": "stimulation", "side": "left", "speed": 3}}
    assert repository.upsert_count == 0
    assert idempotency_service.reserve_kwargs["scope"] == "devices.pump_workstate.create"
    assert idempotency_service.reserve_kwargs["request_hash"] == request_hash(
        {
            "device_id": "pump-1",
            "occurred_at": _now().isoformat(),
            "source": "device",
            "state": {"mode": "stimulation", "side": "left", "speed": 3},
        }
    )
    assert idempotency_service.completed_response_ref == str(event.id)
    assert audit_service.record_kwargs["action"] == "devices.pump_workstate.create"


def test_devices_service_gets_latest_pump_workstate_or_404() -> None:
    owner_user_id = uuid4()
    event = _event(owner_user_id=owner_user_id)
    event.payload = {"source": "device", "state": {"mode": "expression"}}
    repository = FakeDevicesRepository(event=event)
    service = DevicesService(repository=repository)

    latest = asyncio.run(service.get_latest_pump_workstate(owner_user_id=owner_user_id, device_id="pump-1"))

    assert latest is event
    assert repository.latest_event_kwargs == {"owner_user_id": owner_user_id, "device_id": "pump-1", "event_type": "workstate"}


def test_devices_service_latest_pump_workstate_requires_existing_event() -> None:
    service = DevicesService(repository=FakeDevicesRepository())

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(service.get_latest_pump_workstate(owner_user_id=uuid4(), device_id="pump-1"))

    assert exc_info.value.code == "not_found"


def test_devices_service_creates_pump_threshold_as_owner_scoped_fact() -> None:
    owner_user_id = uuid4()
    repository = FakeDevicesRepository(device=_device(owner_user_id=owner_user_id))
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = DevicesService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    event = asyncio.run(
        service.create_pump_threshold(
            owner_user_id=owner_user_id,
            device_id="pump-1",
            occurred_at=_now(),
            stimulate_level_l=1,
            deep_level_l=2,
            stimulate_level_r=3,
            deep_level_r=4,
            source="device",
            idempotency_key="idem-threshold",
            request_id="req_threshold",
        )
    )

    expected_payload = {
        "source": "device",
        "stimulate_level_l": 1,
        "deep_level_l": 2,
        "stimulate_level_r": 3,
        "deep_level_r": 4,
    }
    assert event.event_type == "threshold"
    assert event.payload == expected_payload
    assert idempotency_service.reserve_kwargs["scope"] == "devices.pump_threshold.create"
    assert idempotency_service.reserve_kwargs["request_hash"] == request_hash(
        {"device_id": "pump-1", "occurred_at": _now().isoformat(), "payload": expected_payload}
    )
    assert idempotency_service.completed_response_ref == str(event.id)
    assert audit_service.record_kwargs["action"] == "devices.pump_threshold.create"


def test_devices_service_creates_and_reads_pump_health_fact() -> None:
    owner_user_id = uuid4()
    repository = FakeDevicesRepository(device=_device(owner_user_id=owner_user_id))
    service = DevicesService(repository=repository, audit_service=FakeAuditService(), idempotency_service=FakeIdempotencyService(status="reserved"))

    event = asyncio.run(
        service.create_pump_health(
            owner_user_id=owner_user_id,
            device_id="pump-1",
            occurred_at=_now(),
            health_l=1,
            health_r=2,
            source="device",
            idempotency_key="idem-health",
        )
    )
    latest = asyncio.run(service.get_latest_pump_health(owner_user_id=owner_user_id, device_id="pump-1"))

    assert event.event_type == "health"
    assert latest is event
    assert repository.latest_event_kwargs == {"owner_user_id": owner_user_id, "device_id": "pump-1", "event_type": "health"}


def test_devices_service_rejects_invalid_pump_fact_values() -> None:
    service = DevicesService(repository=FakeDevicesRepository())

    with pytest.raises(ApiError) as threshold_exc:
        asyncio.run(
            service.create_pump_threshold(
                owner_user_id=uuid4(),
                device_id="pump-1",
                occurred_at=_now(),
                stimulate_level_l=0,
                deep_level_l=2,
                stimulate_level_r=3,
                deep_level_r=4,
            )
        )

    with pytest.raises(ApiError) as health_exc:
        asyncio.run(
            service.create_pump_health(
                owner_user_id=uuid4(),
                device_id="pump-1",
                occurred_at=_now(),
                health_l=3,
                health_r=1,
            )
        )

    assert threshold_exc.value.code == "validation_failed"
    assert health_exc.value.code == "validation_failed"


def _now() -> datetime:
    return datetime(2026, 7, 2, tzinfo=timezone.utc)


def _device(*, owner_user_id) -> PumpDevice:
    return PumpDevice(id=uuid4(), owner_user_id=owner_user_id, device_id="pump-1", model="M1", firmware_version="", status="active")


def _event(*, owner_user_id) -> PumpTelemetryEvent:
    return PumpTelemetryEvent(id=uuid4(), owner_user_id=owner_user_id, device_id="pump-1", event_type="workstate", occurred_at=_now(), payload={})


class FakeDevicesRepository:
    def __init__(self, *, device=None, event=None) -> None:
        self.device = device
        self.event = event
        self.upsert_count = 0
        self.latest_event_kwargs = {}

    async def upsert_device(self, **kwargs):
        self.upsert_count += 1
        self.device = _device(owner_user_id=kwargs["owner_user_id"])
        return self.device

    async def list_devices(self, **kwargs):
        return [self.device] if self.device else []

    async def get_device_for_owner(self, **kwargs):
        return self.device

    async def touch_device_last_seen(self, **kwargs):
        if self.device is None:
            self.device = _device(owner_user_id=kwargs["owner_user_id"])
            self.device.device_id = kwargs["device_id"]
        if self.device.last_seen_at is None or kwargs["last_seen_at"] > self.device.last_seen_at:
            self.device.last_seen_at = kwargs["last_seen_at"]
        self.device.status = "active"
        return self.device

    async def create_telemetry_event(self, **kwargs):
        self.event = _event(owner_user_id=kwargs["owner_user_id"])
        self.event.device_id = kwargs["device_id"]
        self.event.event_type = kwargs["event_type"]
        self.event.occurred_at = kwargs["occurred_at"]
        self.event.payload = kwargs["payload"]
        return self.event

    async def get_telemetry_event_for_owner(self, **kwargs):
        return self.event

    async def list_telemetry_events(self, **kwargs):
        return [self.event] if self.event else []

    async def get_latest_telemetry_event(self, **kwargs):
        self.latest_event_kwargs = kwargs
        return self.event


class FakeIdempotencyService:
    def __init__(self, *, status: str, response_ref: str = "") -> None:
        self.status = status
        self.reserve_kwargs = {}
        self.completed_response_ref = ""
        self.record = IdempotencyKey(
            actor_user_id=uuid4(),
            scope="devices.telemetry.create",
            key="idem-event",
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


class ConflictIdempotencyService:
    async def reserve(self, **kwargs):
        raise ApiError(code="idempotency_conflict", message="Idempotency key was reused with a different request.", status=409)


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None
