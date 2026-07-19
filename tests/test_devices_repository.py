import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.modules.devices.repository import DevicesRepository


def test_devices_repository_touch_last_seen_creates_or_advances_device() -> None:
    owner_user_id = uuid4()
    session = FakeDevicesSession()
    repository = DevicesRepository(session)
    first_seen = datetime(2026, 7, 3, 9, tzinfo=timezone.utc)
    older_seen = datetime(2026, 7, 3, 8, tzinfo=timezone.utc)
    later_seen = datetime(2026, 7, 3, 10, tzinfo=timezone.utc)

    created = asyncio.run(
        repository.touch_device_last_seen(
            owner_user_id=owner_user_id,
            device_id="pump-1",
            last_seen_at=first_seen,
        )
    )
    touched_with_older_event = asyncio.run(
        repository.touch_device_last_seen(
            owner_user_id=owner_user_id,
            device_id="pump-1",
            last_seen_at=older_seen,
        )
    )
    last_seen_after_older_event = touched_with_older_event.last_seen_at
    touched_with_later_event = asyncio.run(
        repository.touch_device_last_seen(
            owner_user_id=owner_user_id,
            device_id="pump-1",
            last_seen_at=later_seen,
        )
    )

    assert created.owner_user_id == owner_user_id
    assert created.device_id == "pump-1"
    assert last_seen_after_older_event == first_seen
    assert touched_with_later_event.last_seen_at == later_seen
    assert session.added == [created]
    assert session.flush_count == 3


class FakeDevicesSession:
    def __init__(self) -> None:
        self.device = None
        self.added = []
        self.flush_count = 0

    async def scalar(self, statement):
        return self.device

    def add(self, instance) -> None:
        self.device = instance
        self.added.append(instance)

    async def flush(self) -> None:
        self.flush_count += 1
