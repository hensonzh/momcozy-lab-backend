import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.records.models import PumpingRecord
from production_backend.app.modules.records.service import RecordsService


def test_pumping_record_main_flow_updates_list_trends_and_delete_state() -> None:
    owner_user_id = uuid4()
    other_user_id = uuid4()
    repository = InMemoryRecordsRepository()
    audit_service = FlowAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service)

    created = asyncio.run(
        service.create_pumping(
            owner_user_id=owner_user_id,
            pump_start_time=datetime(2026, 7, 3, 8, 30, tzinfo=timezone.utc),
            pump_end_time=datetime(2026, 7, 3, 8, 50, tzinfo=timezone.utc),
            milk_volume_ml=95,
            pump_type="electric",
            duration_seconds=1200,
            source="manual",
            title="Morning pumping",
            request_id="req_pump_create",
        )
    )
    asyncio.run(
        service.create_pumping(
            owner_user_id=other_user_id,
            pump_start_time=datetime(2026, 7, 3, 9, 30, tzinfo=timezone.utc),
            milk_volume_ml=400,
            source="manual",
        )
    )

    visible_records = asyncio.run(service.list_pumpings(owner_user_id=owner_user_id, limit=10))
    trends = asyncio.run(
        service.get_milk_trends(
            owner_user_id=owner_user_id,
            start_date=date(2026, 7, 3),
            days=1,
            include_today=True,
        )
    )

    assert [record.id for record in visible_records] == [created.id]
    assert trends.items[0].pumped_milk_volume_ml == 95
    assert trends.items[0].pumping_count == 1
    assert trends.items[0].measured_only is True

    asyncio.run(service.delete_pumping(owner_user_id=owner_user_id, record_id=created.id, request_id="req_pump_delete"))

    visible_after_delete = asyncio.run(service.list_pumpings(owner_user_id=owner_user_id, limit=10))
    trends_after_delete = asyncio.run(
        service.get_milk_trends(
            owner_user_id=owner_user_id,
            start_date=date(2026, 7, 3),
            days=1,
            include_today=True,
        )
    )

    assert visible_after_delete == []
    assert trends_after_delete.items[0].pumped_milk_volume_ml == 0
    assert trends_after_delete.items[0].pumping_count == 0
    assert [entry["action"] for entry in audit_service.entries] == [
        "records.pumping.create",
        "records.pumping.create",
        "records.pumping.delete",
    ]
    assert audit_service.entries[-1]["request_id"] == "req_pump_delete"


class InMemoryRecordsRepository:
    def __init__(self) -> None:
        self.pumpings: list[PumpingRecord] = []

    async def create_pumping(self, **kwargs):
        record = PumpingRecord(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            pump_start_time=kwargs["pump_start_time"],
            pump_end_time=kwargs["pump_end_time"],
            milk_volume_ml=kwargs["milk_volume_ml"],
            pump_type=kwargs["pump_type"],
            duration_seconds=kwargs["duration_seconds"],
            source=kwargs["source"],
            title=kwargs["title"],
            status="active",
        )
        self.pumpings.append(record)
        return record

    async def list_pumpings(self, *, owner_user_id: UUID, start_at, end_at, limit: int):
        records = [
            record
            for record in self.pumpings
            if record.owner_user_id == owner_user_id
            and record.status == "active"
            and record.deleted_at is None
            and (start_at is None or record.pump_start_time >= start_at)
            and (end_at is None or record.pump_start_time < end_at)
        ]
        return sorted(records, key=lambda record: record.pump_start_time, reverse=True)[:limit]

    async def get_pumping_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return next(
            (
                record
                for record in self.pumpings
                if record.id == record_id and record.owner_user_id == owner_user_id and record.deleted_at is None
            ),
            None,
        )

    async def soft_delete_pumping(self, *, record_id: UUID, owner_user_id: UUID, deleted_at):
        record = await self.get_pumping_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            return None
        record.status = "deleted"
        record.deleted_at = deleted_at
        return record


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
