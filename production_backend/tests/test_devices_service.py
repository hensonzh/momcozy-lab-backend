import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from production_backend.app.modules.devices.service import DevicesService


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

    async def upsert_device(self, **kwargs):
        self.device = _device(owner_user_id=kwargs["owner_user_id"])
        return self.device

    async def list_devices(self, **kwargs):
        return [self.device] if self.device else []

    async def get_device_for_owner(self, **kwargs):
        return self.device

    async def create_telemetry_event(self, **kwargs):
        self.event = _event(owner_user_id=kwargs["owner_user_id"])
        return self.event

    async def get_telemetry_event_for_owner(self, **kwargs):
        return self.event

    async def list_telemetry_events(self, **kwargs):
        return [self.event] if self.event else []


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


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None
