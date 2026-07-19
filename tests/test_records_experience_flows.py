import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from app.modules.records.service import RecordsService


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


def test_feeding_and_growth_main_flow_tracks_infant_scope_list_and_delete_state() -> None:
    owner_user_id = uuid4()
    other_user_id = uuid4()
    infant_id = uuid4()
    other_infant_id = uuid4()
    repository = InMemoryRecordsRepository()
    repository.add_infant(owner_user_id=owner_user_id, infant_id=infant_id)
    repository.add_infant(owner_user_id=other_user_id, infant_id=other_infant_id)
    audit_service = FlowAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service)

    feeding = asyncio.run(
        service.create_feeding(
            owner_user_id=owner_user_id,
            infant_id=infant_id,
            feed_time=datetime(2026, 7, 3, 7, 15, tzinfo=timezone.utc),
            feed_type="bottle",
            feed_action="left",
            volume_ml=80,
            title="Morning bottle",
            request_id="req_feeding_create",
        )
    )
    asyncio.run(
        service.create_feeding(
            owner_user_id=other_user_id,
            infant_id=other_infant_id,
            feed_time=datetime(2026, 7, 3, 8, 15, tzinfo=timezone.utc),
            feed_type="bottle",
            volume_ml=900,
        )
    )
    growth = asyncio.run(
        service.create_growth(
            owner_user_id=owner_user_id,
            infant_id=infant_id,
            measured_at=datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc),
            height_cm=55,
            weight_kg=4.8,
            request_id="req_growth_create",
        )
    )
    asyncio.run(
        service.create_growth(
            owner_user_id=other_user_id,
            infant_id=other_infant_id,
            measured_at=datetime(2026, 7, 3, 9, 30, tzinfo=timezone.utc),
            weight_kg=9.9,
        )
    )

    with pytest.raises(ApiError) as cross_owner_infant:
        asyncio.run(
            service.create_feeding(
                owner_user_id=owner_user_id,
                infant_id=other_infant_id,
                feed_time=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
                feed_type="bottle",
                volume_ml=80,
            )
        )

    visible_feedings = asyncio.run(
        service.list_feedings(
            owner_user_id=owner_user_id,
            start_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
            end_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
            limit=10,
        )
    )
    visible_growth = asyncio.run(service.list_growth(owner_user_id=owner_user_id, infant_id=infant_id, limit=10))

    assert [record.id for record in visible_feedings] == [feeding.id]
    assert [record.id for record in visible_growth] == [growth.id]
    assert cross_owner_infant.value.code == "owner_scope_violation"

    asyncio.run(service.delete_feeding(owner_user_id=owner_user_id, record_id=feeding.id, request_id="req_feeding_delete"))
    asyncio.run(service.delete_growth(owner_user_id=owner_user_id, record_id=growth.id, request_id="req_growth_delete"))

    assert asyncio.run(service.list_feedings(owner_user_id=owner_user_id, limit=10)) == []
    assert asyncio.run(service.list_growth(owner_user_id=owner_user_id, infant_id=infant_id, limit=10)) == []
    assert [entry["action"] for entry in audit_service.entries] == [
        "records.feeding.create",
        "records.feeding.create",
        "records.growth.create",
        "records.growth.create",
        "records.feeding.delete",
        "records.growth.delete",
    ]


class InMemoryRecordsRepository:
    def __init__(self) -> None:
        self.infant_owner_ids: dict[UUID, set[UUID]] = {}
        self.feedings: list[FeedingRecord] = []
        self.pumpings: list[PumpingRecord] = []
        self.growths: list[GrowthRecord] = []

    def add_infant(self, *, owner_user_id: UUID, infant_id: UUID) -> None:
        self.infant_owner_ids.setdefault(owner_user_id, set()).add(infant_id)

    async def infant_belongs_to_owner(self, *, infant_id: UUID, owner_user_id: UUID):
        return infant_id in self.infant_owner_ids.get(owner_user_id, set())

    async def create_feeding(self, **kwargs):
        record = FeedingRecord(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            infant_id=kwargs["infant_id"],
            feed_time=kwargs["feed_time"],
            feed_type=kwargs["feed_type"],
            feed_action=kwargs["feed_action"],
            volume_ml=kwargs["volume_ml"],
            duration_seconds=kwargs["duration_seconds"],
            title=kwargs["title"],
            status="active",
        )
        self.feedings.append(record)
        return record

    async def list_feedings(self, *, owner_user_id: UUID, start_at=None, end_at=None, limit: int):
        records = [
            record
            for record in self.feedings
            if record.owner_user_id == owner_user_id
            and record.status == "active"
            and record.deleted_at is None
            and (start_at is None or record.feed_time >= start_at)
            and (end_at is None or record.feed_time < end_at)
        ]
        return sorted(records, key=lambda record: record.feed_time, reverse=True)[:limit]

    async def get_feeding_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return next(
            (
                record
                for record in self.feedings
                if record.id == record_id and record.owner_user_id == owner_user_id and record.deleted_at is None
            ),
            None,
        )

    async def soft_delete_feeding(self, *, record_id: UUID, owner_user_id: UUID, deleted_at):
        record = await self.get_feeding_for_owner(record_id=record_id, owner_user_id=owner_user_id)
        if record is None:
            return None
        record.status = "deleted"
        record.deleted_at = deleted_at
        return record

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

    async def create_growth(self, **kwargs):
        record = GrowthRecord(
            id=uuid4(),
            owner_user_id=kwargs["owner_user_id"],
            infant_id=kwargs["infant_id"],
            measured_at=kwargs["measured_at"],
            height_cm=kwargs["height_cm"],
            weight_kg=kwargs["weight_kg"],
            head_cm=kwargs["head_cm"],
            status="active",
        )
        self.growths.append(record)
        return record

    async def list_growth(self, *, owner_user_id: UUID, infant_id: UUID | None, limit: int):
        records = [
            record
            for record in self.growths
            if record.owner_user_id == owner_user_id
            and record.status == "active"
            and record.deleted_at is None
            and (infant_id is None or record.infant_id == infant_id)
        ]
        return sorted(records, key=lambda record: record.measured_at, reverse=True)[:limit]

    async def get_growth_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return next(
            (
                record
                for record in self.growths
                if record.id == record_id and record.owner_user_id == owner_user_id and record.deleted_at is None
            ),
            None,
        )

    async def soft_delete_growth(self, *, record_id: UUID, owner_user_id: UUID, deleted_at):
        record = await self.get_growth_for_owner(record_id=record_id, owner_user_id=owner_user_id)
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
