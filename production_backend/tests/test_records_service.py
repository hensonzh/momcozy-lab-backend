import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from production_backend.app.modules.records.service import RecordsService


def test_records_service_creates_feeding_with_idempotency_and_audit() -> None:
    owner_user_id = uuid4()
    infant_id = uuid4()
    repository = FakeRecordsRepository(infant_owner_ok=True)
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    record = asyncio.run(
        service.create_feeding(
            owner_user_id=owner_user_id,
            infant_id=infant_id,
            feed_time=_now(),
            feed_type="bottle",
            volume_ml=90,
            request_id="req_feed",
            idempotency_key="idem-feed",
        )
    )

    assert record.owner_user_id == owner_user_id
    assert repository.create_feeding_kwargs["infant_id"] == infant_id
    assert idempotency_service.reserve_kwargs["scope"] == "records.feeding.create"
    assert idempotency_service.completed_response_ref == str(record.id)
    assert audit_service.record_kwargs["action"] == "records.feeding.create"


def test_records_service_rejects_cross_owner_infant() -> None:
    service = RecordsService(repository=FakeRecordsRepository(infant_owner_ok=False))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.create_feeding(
                owner_user_id=uuid4(),
                infant_id=uuid4(),
                feed_time=_now(),
                feed_type="bottle",
                volume_ml=90,
            )
        )

    assert exc_info.value.code == "owner_scope_violation"


def test_records_service_replays_completed_feeding_create() -> None:
    owner_user_id = uuid4()
    record_id = uuid4()
    existing = _feeding(owner_user_id=owner_user_id, record_id=record_id)
    repository = FakeRecordsRepository(feeding=existing)
    service = RecordsService(
        repository=repository,
        idempotency_service=FakeIdempotencyService(status="replay", response_ref=str(record_id)),
    )

    returned = asyncio.run(
        service.create_feeding(
            owner_user_id=owner_user_id,
            infant_id=None,
            feed_time=_now(),
            feed_type="bottle",
            volume_ml=90,
            idempotency_key="idem-feed",
        )
    )

    assert returned is existing
    assert repository.create_feeding_kwargs == {}


def test_records_service_lists_feedings_with_limit() -> None:
    owner_user_id = uuid4()
    repository = FakeRecordsRepository(feedings=[_feeding(owner_user_id=owner_user_id)])
    service = RecordsService(repository=repository)

    records = asyncio.run(service.list_feedings(owner_user_id=owner_user_id, limit=10))

    assert len(records) == 1
    assert repository.list_feedings_kwargs["limit"] == 10


def test_records_service_deletes_feeding_with_audit() -> None:
    owner_user_id = uuid4()
    record_id = uuid4()
    repository = FakeRecordsRepository(feeding=_feeding(owner_user_id=owner_user_id, record_id=record_id))
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service)

    asyncio.run(service.delete_feeding(owner_user_id=owner_user_id, record_id=record_id, request_id="req_delete"))

    assert repository.deleted_feeding.status == "deleted"
    assert audit_service.record_kwargs["action"] == "records.feeding.delete"


def test_records_service_creates_pumping_with_idempotency_and_audit() -> None:
    owner_user_id = uuid4()
    repository = FakeRecordsRepository()
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    record = asyncio.run(
        service.create_pumping(
            owner_user_id=owner_user_id,
            pump_start_time=_now(),
            milk_volume_ml=120,
            source="manual",
            request_id="req_pump",
            idempotency_key="idem-pump",
        )
    )

    assert record.owner_user_id == owner_user_id
    assert repository.create_pumping_kwargs["milk_volume_ml"] == 120
    assert idempotency_service.reserve_kwargs["scope"] == "records.pumping.create"
    assert idempotency_service.completed_response_ref == str(record.id)
    assert audit_service.record_kwargs["action"] == "records.pumping.create"


def test_records_service_lists_and_deletes_pumpings() -> None:
    owner_user_id = uuid4()
    pumping = _pumping(owner_user_id=owner_user_id)
    repository = FakeRecordsRepository(pumpings=[pumping], pumping=pumping)
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service)

    records = asyncio.run(service.list_pumpings(owner_user_id=owner_user_id, limit=10))
    asyncio.run(service.delete_pumping(owner_user_id=owner_user_id, record_id=pumping.id, request_id="req_delete"))

    assert records == [pumping]
    assert repository.list_pumpings_kwargs["limit"] == 10
    assert repository.deleted_pumping.status == "deleted"
    assert audit_service.record_kwargs["action"] == "records.pumping.delete"


def test_records_service_builds_measured_milk_trends_from_pumping_records() -> None:
    owner_user_id = uuid4()
    first = _pumping(owner_user_id=owner_user_id)
    first.pump_start_time = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    first.milk_volume_ml = 90
    second = _pumping(owner_user_id=owner_user_id)
    second.pump_start_time = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
    second.milk_volume_ml = 35.5
    repository = FakeRecordsRepository(pumpings=[first, second])
    service = RecordsService(repository=repository)

    trends = asyncio.run(
        service.get_milk_trends(
            owner_user_id=owner_user_id,
            start_date=date(2026, 7, 1),
            days=2,
            include_today=True,
        )
    )

    assert trends.items[0].date == date(2026, 7, 1)
    assert trends.items[0].pumped_milk_volume_ml == 125.5
    assert trends.items[0].pumping_count == 2
    assert trends.items[0].measured_only is True
    assert trends.items[1].date == date(2026, 7, 2)
    assert trends.items[1].pumped_milk_volume_ml == 0
    assert repository.list_pumpings_kwargs["owner_user_id"] == owner_user_id
    assert repository.list_pumpings_kwargs["limit"] == 1000


def test_records_service_creates_growth_with_infant_scope_idempotency_and_audit() -> None:
    owner_user_id = uuid4()
    infant_id = uuid4()
    repository = FakeRecordsRepository(infant_owner_ok=True)
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service, idempotency_service=idempotency_service)

    record = asyncio.run(
        service.create_growth(
            owner_user_id=owner_user_id,
            infant_id=infant_id,
            measured_at=_now(),
            weight_kg=4.2,
            request_id="req_growth",
            idempotency_key="idem-growth",
        )
    )

    assert record.owner_user_id == owner_user_id
    assert repository.create_growth_kwargs["infant_id"] == infant_id
    assert idempotency_service.reserve_kwargs["scope"] == "records.growth.create"
    assert idempotency_service.completed_response_ref == str(record.id)
    assert audit_service.record_kwargs["action"] == "records.growth.create"


def test_records_service_lists_and_deletes_growth() -> None:
    owner_user_id = uuid4()
    growth = _growth(owner_user_id=owner_user_id)
    repository = FakeRecordsRepository(growths=[growth], growth=growth)
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service)

    records = asyncio.run(service.list_growth(owner_user_id=owner_user_id, limit=10))
    asyncio.run(service.delete_growth(owner_user_id=owner_user_id, record_id=growth.id, request_id="req_delete"))

    assert records == [growth]
    assert repository.list_growth_kwargs["limit"] == 10
    assert repository.deleted_growth.status == "deleted"
    assert audit_service.record_kwargs["action"] == "records.growth.delete"


def _now() -> datetime:
    return datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)


def _feeding(*, owner_user_id: UUID, record_id: UUID | None = None) -> FeedingRecord:
    return FeedingRecord(
        id=record_id or uuid4(),
        owner_user_id=owner_user_id,
        infant_id=None,
        feed_time=_now(),
        feed_type="bottle",
        feed_action="",
        volume_ml=90,
        duration_seconds=None,
        title="",
        status="active",
    )


def _pumping(*, owner_user_id: UUID, record_id: UUID | None = None) -> PumpingRecord:
    return PumpingRecord(
        id=record_id or uuid4(),
        owner_user_id=owner_user_id,
        pump_start_time=_now(),
        pump_end_time=None,
        milk_volume_ml=120,
        pump_type="",
        duration_seconds=None,
        source="manual",
        title="",
        status="active",
    )


def _growth(*, owner_user_id: UUID, record_id: UUID | None = None) -> GrowthRecord:
    return GrowthRecord(
        id=record_id or uuid4(),
        owner_user_id=owner_user_id,
        infant_id=None,
        measured_at=_now(),
        height_cm=None,
        weight_kg=4.2,
        head_cm=None,
        status="active",
    )


class FakeRecordsRepository:
    def __init__(
        self,
        *,
        infant_owner_ok=True,
        feeding=None,
        feedings=None,
        pumping=None,
        pumpings=None,
        growth=None,
        growths=None,
    ) -> None:
        self.infant_owner_ok = infant_owner_ok
        self.feeding = feeding
        self.feedings = feedings or []
        self.pumping = pumping
        self.pumpings = pumpings or []
        self.growth = growth
        self.growths = growths or []
        self.create_feeding_kwargs = {}
        self.create_pumping_kwargs = {}
        self.create_growth_kwargs = {}
        self.list_feedings_kwargs = {}
        self.list_pumpings_kwargs = {}
        self.list_growth_kwargs = {}
        self.deleted_feeding = None
        self.deleted_pumping = None
        self.deleted_growth = None

    async def infant_belongs_to_owner(self, *, infant_id: UUID, owner_user_id: UUID):
        return self.infant_owner_ok

    async def create_feeding(self, **kwargs):
        self.create_feeding_kwargs = kwargs
        self.feeding = _feeding(owner_user_id=kwargs["owner_user_id"])
        self.feeding.infant_id = kwargs["infant_id"]
        return self.feeding

    async def get_feeding_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return self.feeding

    async def list_feedings(self, **kwargs):
        self.list_feedings_kwargs = kwargs
        return self.feedings

    async def soft_delete_feeding(self, **kwargs):
        if self.feeding is None:
            return None
        self.feeding.status = "deleted"
        self.feeding.deleted_at = kwargs["deleted_at"]
        self.deleted_feeding = self.feeding
        return self.feeding

    async def create_pumping(self, **kwargs):
        self.create_pumping_kwargs = kwargs
        self.pumping = _pumping(owner_user_id=kwargs["owner_user_id"])
        return self.pumping

    async def get_pumping_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return self.pumping

    async def list_pumpings(self, **kwargs):
        self.list_pumpings_kwargs = kwargs
        return self.pumpings

    async def soft_delete_pumping(self, **kwargs):
        if self.pumping is None:
            return None
        self.pumping.status = "deleted"
        self.pumping.deleted_at = kwargs["deleted_at"]
        self.deleted_pumping = self.pumping
        return self.pumping

    async def create_growth(self, **kwargs):
        self.create_growth_kwargs = kwargs
        self.growth = _growth(owner_user_id=kwargs["owner_user_id"])
        self.growth.infant_id = kwargs["infant_id"]
        return self.growth

    async def get_growth_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return self.growth

    async def list_growth(self, **kwargs):
        self.list_growth_kwargs = kwargs
        return self.growths

    async def soft_delete_growth(self, **kwargs):
        if self.growth is None:
            return None
        self.growth.status = "deleted"
        self.growth.deleted_at = kwargs["deleted_at"]
        self.deleted_growth = self.growth
        return self.growth


class FakeIdempotencyService:
    def __init__(self, *, status: str, response_ref: str = "") -> None:
        self.status = status
        self.reserve_kwargs = {}
        self.completed_response_ref = ""
        self.record = IdempotencyKey(
            actor_user_id=uuid4(),
            scope="records.feeding.create",
            key="idem-feed",
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
