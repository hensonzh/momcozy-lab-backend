import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.modules.devices.models import PumpDevice, PumpTelemetryEvent
from app.modules.devices.service import DevicesService


def test_pump_device_main_flow_tracks_latest_facts_and_owner_scope() -> None:
    asyncio.run(_run_pump_device_main_flow())


async def _run_pump_device_main_flow() -> None:
    owner_user_id = uuid4()
    other_user_id = uuid4()
    now = datetime(2026, 7, 3, 9, tzinfo=timezone.utc)
    repository = InMemoryDevicesRepository()
    audit_service = FlowAuditService()
    service = DevicesService(repository=repository, audit_service=audit_service)

    device = await service.upsert_device(
        owner_user_id=owner_user_id,
        device_id=" pump-1 ",
        model="MomCozy M9",
        firmware_version="1.0.0",
        last_seen_at=now,
        request_id="req_device_pair",
    )
    workstate = await service.create_pump_workstate(
        owner_user_id=owner_user_id,
        device_id="pump-1",
        occurred_at=now + timedelta(minutes=5),
        state={"mode": "stimulation", "speed": 3, "side": "left"},
        source="device",
        request_id="req_workstate",
    )
    threshold = await service.create_pump_threshold(
        owner_user_id=owner_user_id,
        device_id="pump-1",
        occurred_at=now + timedelta(minutes=6),
        stimulate_level_l=1,
        deep_level_l=2,
        stimulate_level_r=3,
        deep_level_r=4,
        source="device",
        request_id="req_threshold",
    )
    health = await service.create_pump_health(
        owner_user_id=owner_user_id,
        device_id="pump-1",
        occurred_at=now + timedelta(minutes=7),
        health_l=1,
        health_r=2,
        source="device",
        request_id="req_health",
    )
    await service.create_pump_health(
        owner_user_id=owner_user_id,
        device_id="pump-1",
        occurred_at=now + timedelta(minutes=2),
        health_l=0,
        health_r=1,
        source="device",
        request_id="req_old_health",
    )
    await service.create_pump_health(
        owner_user_id=other_user_id,
        device_id="pump-1",
        occurred_at=now + timedelta(minutes=30),
        health_l=2,
        health_r=2,
        source="device",
        request_id="req_other_health",
    )

    devices = await service.list_devices(owner_user_id=owner_user_id)
    latest_workstate = await service.get_latest_pump_workstate(owner_user_id=owner_user_id, device_id="pump-1")
    latest_threshold = await service.get_latest_pump_threshold(owner_user_id=owner_user_id, device_id="pump-1")
    latest_health = await service.get_latest_pump_health(owner_user_id=owner_user_id, device_id="pump-1")
    visible_events = await service.list_telemetry_events(owner_user_id=owner_user_id, device_id="pump-1", limit=10)
    other_devices = await service.list_devices(owner_user_id=other_user_id)

    assert device.device_id == "pump-1"
    assert [item.id for item in devices] == [device.id]
    assert devices[0].model == "MomCozy M9"
    assert devices[0].last_seen_at == now + timedelta(minutes=7)
    assert latest_workstate.id == workstate.id
    assert latest_workstate.payload["state"]["mode"] == "stimulation"
    assert latest_threshold.id == threshold.id
    assert latest_threshold.payload["deep_level_r"] == 4
    assert latest_health.id == health.id
    assert latest_health.payload == {"source": "device", "health_l": 1, "health_r": 2}
    assert [event.event_type for event in visible_events] == ["health", "threshold", "workstate", "health"]
    assert [item.owner_user_id for item in other_devices] == [other_user_id]
    assert service.get_pump_energy_target() == {"lower_value": 80, "upper_value": 100}
    assert [entry["action"] for entry in audit_service.entries] == [
        "devices.pump.upsert",
        "devices.pump_workstate.create",
        "devices.pump_threshold.create",
        "devices.pump_health.create",
        "devices.pump_health.create",
        "devices.pump_health.create",
    ]


class InMemoryDevicesRepository:
    def __init__(self) -> None:
        self.devices: list[PumpDevice] = []
        self.events: list[PumpTelemetryEvent] = []

    async def upsert_device(self, *, owner_user_id: UUID, device_id: str, model: str, firmware_version: str, last_seen_at):
        device = await self.get_device_for_owner(owner_user_id=owner_user_id, device_id=device_id)
        if device is None:
            device = PumpDevice(
                id=uuid4(),
                owner_user_id=owner_user_id,
                device_id=device_id,
                status="active",
                deleted_at=None,
            )
            self.devices.append(device)
        device.model = model
        device.firmware_version = firmware_version
        device.last_seen_at = last_seen_at
        device.status = "active"
        return device

    async def touch_device_last_seen(self, *, owner_user_id: UUID, device_id: str, last_seen_at):
        device = await self.get_device_for_owner(owner_user_id=owner_user_id, device_id=device_id)
        if device is None:
            device = PumpDevice(
                id=uuid4(),
                owner_user_id=owner_user_id,
                device_id=device_id,
                model="",
                firmware_version="",
                status="active",
                deleted_at=None,
            )
            self.devices.append(device)
        if device.last_seen_at is None or last_seen_at > device.last_seen_at:
            device.last_seen_at = last_seen_at
        device.status = "active"
        return device

    async def list_devices(self, *, owner_user_id: UUID):
        devices = [
            device
            for device in self.devices
            if device.owner_user_id == owner_user_id and device.status == "active" and device.deleted_at is None
        ]
        return sorted(devices, key=lambda device: device.device_id)

    async def get_device_for_owner(self, *, owner_user_id: UUID, device_id: str):
        return next(
            (
                device
                for device in self.devices
                if device.owner_user_id == owner_user_id
                and device.device_id == device_id
                and device.status == "active"
                and device.deleted_at is None
            ),
            None,
        )

    async def create_telemetry_event(self, *, owner_user_id: UUID, device_id: str, event_type: str, occurred_at, payload):
        event = PumpTelemetryEvent(
            id=uuid4(),
            owner_user_id=owner_user_id,
            device_id=device_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )
        self.events.append(event)
        return event

    async def get_telemetry_event_for_owner(self, *, owner_user_id: UUID, event_id: UUID):
        return next((event for event in self.events if event.owner_user_id == owner_user_id and event.id == event_id), None)

    async def list_telemetry_events(self, *, owner_user_id: UUID, device_id: str | None, event_type: str | None, limit: int):
        events = [
            event
            for event in self.events
            if event.owner_user_id == owner_user_id
            and (device_id is None or event.device_id == device_id)
            and (event_type is None or event.event_type == event_type)
        ]
        return sorted(events, key=lambda event: event.occurred_at, reverse=True)[:limit]

    async def get_latest_telemetry_event(self, *, owner_user_id: UUID, device_id: str, event_type: str):
        events = await self.list_telemetry_events(
            owner_user_id=owner_user_id,
            device_id=device_id,
            event_type=event_type,
            limit=100,
        )
        return events[0] if events else None


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
