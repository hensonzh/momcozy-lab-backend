import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.modules.audit.models import IdempotencyKey
from app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from app.modules.records.service import RecordsService


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


def test_records_service_atomically_links_feeding_and_completes_owner_task() -> None:
    owner_user_id = uuid4()
    task_id = uuid4()
    repository = FakeRecordsRepository(plan_task_owner_ok=True)
    service = RecordsService(repository=repository, idempotency_service=FakeIdempotencyService(status="reserved"))

    record = asyncio.run(
        service.create_feeding(
            owner_user_id=owner_user_id,
            infant_id=None,
            plan_task_id=task_id,
            feed_time=_now(),
            feed_type="bottle",
            volume_ml=90,
            idempotency_key="complete-feed-task",
        )
    )

    assert record.plan_task_id == task_id
    assert repository.completed_plan_task_id == task_id
    assert repository.create_feeding_kwargs["plan_task_id"] == task_id


def test_records_service_rejects_cross_owner_plan_task_before_record_create() -> None:
    repository = FakeRecordsRepository(plan_task_owner_ok=False)
    service = RecordsService(repository=repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.create_pumping(
                owner_user_id=uuid4(),
                plan_task_id=uuid4(),
                pump_start_time=_now(),
                milk_volume_ml=120,
            )
        )

    assert exc_info.value.code == "owner_scope_violation"
    assert repository.create_pumping_kwargs == {}


def test_records_service_rejects_record_kind_that_conflicts_with_typed_task() -> None:
    repository = FakeRecordsRepository(plan_task_kind="pumping")
    service = RecordsService(repository=repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.create_feeding(
                owner_user_id=uuid4(),
                infant_id=None,
                plan_task_id=uuid4(),
                feed_time=_now(),
                feed_type="bottle",
                volume_ml=90,
            )
        )

    assert exc_info.value.code == "task_record_type_mismatch"
    assert repository.create_feeding_kwargs == {}


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


def test_records_service_replays_linked_feeding_without_completing_task_again() -> None:
    owner_user_id = uuid4()
    task_id = uuid4()
    record_id = uuid4()
    existing = _feeding(owner_user_id=owner_user_id, record_id=record_id, plan_task_id=task_id)
    repository = FakeRecordsRepository(feeding=existing, plan_task_kind="feeding")
    service = RecordsService(
        repository=repository,
        idempotency_service=FakeIdempotencyService(status="replay", response_ref=str(record_id)),
    )

    returned = asyncio.run(
        service.create_feeding(
            owner_user_id=owner_user_id,
            infant_id=None,
            plan_task_id=task_id,
            feed_time=_now(),
            feed_type="bottle",
            volume_ml=90,
            idempotency_key="idem-linked-feed",
        )
    )

    assert returned is existing
    assert repository.create_feeding_kwargs == {}
    assert repository.completed_plan_task_id is None


def test_records_service_does_not_complete_task_when_record_create_fails() -> None:
    repository = FakeRecordsRepository(plan_task_kind="feeding", create_feeding_error=RuntimeError("write failed"))
    service = RecordsService(repository=repository)

    with pytest.raises(RuntimeError, match="write failed"):
        asyncio.run(
            service.create_feeding(
                owner_user_id=uuid4(),
                infant_id=None,
                plan_task_id=uuid4(),
                feed_time=_now(),
                feed_type="bottle",
                volume_ml=90,
            )
        )

    assert repository.completed_plan_task_id is None


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


def test_records_service_updates_feeding_and_completes_new_linked_task() -> None:
    owner_user_id = uuid4()
    task_id = uuid4()
    feeding = _feeding(owner_user_id=owner_user_id)
    repository = FakeRecordsRepository(
        feeding=feeding,
        plan_task_kind="feeding",
    )
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service)

    updated = asyncio.run(
        service.update_feeding(
            owner_user_id=owner_user_id,
            record_id=feeding.id,
            updates={
                "plan_task_id": task_id,
                "volume_ml": 110.0,
            },
            request_id="req_feeding_update",
        )
    )

    assert updated.plan_task_id == task_id
    assert updated.volume_ml == 110.0
    assert repository.update_feeding_kwargs["updates"] == {
        "plan_task_id": task_id,
        "volume_ml": 110.0,
    }
    assert repository.completed_plan_task_id == task_id
    assert audit_service.record_kwargs["action"] == "records.feeding.update"


def test_records_service_rejects_feeding_update_that_removes_all_measurements() -> None:
    owner_user_id = uuid4()
    feeding = _feeding(owner_user_id=owner_user_id)
    repository = FakeRecordsRepository(feeding=feeding)
    service = RecordsService(repository=repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_feeding(
                owner_user_id=owner_user_id,
                record_id=feeding.id,
                updates={"volume_ml": None},
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert repository.update_feeding_kwargs == {}


def test_records_service_updates_pumping_preserving_omitted_fields() -> None:
    owner_user_id = uuid4()
    pumping = _pumping(owner_user_id=owner_user_id)
    repository = FakeRecordsRepository(pumping=pumping)
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service)

    updated = asyncio.run(
        service.update_pumping(
            owner_user_id=owner_user_id,
            record_id=pumping.id,
            updates={"duration_seconds": 900},
            request_id="req_pumping_update",
        )
    )

    assert updated.duration_seconds == 900
    assert updated.milk_volume_ml == 120
    assert repository.update_pumping_kwargs["updates"] == {"duration_seconds": 900}
    assert audit_service.record_kwargs["action"] == "records.pumping.update"


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


def test_records_service_lists_growth_inside_timeline_window() -> None:
    owner_user_id = uuid4()
    growth = _growth(owner_user_id=owner_user_id)
    repository = FakeRecordsRepository(growths=[growth])
    service = RecordsService(repository=repository)
    start_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end_at = datetime(2026, 7, 3, tzinfo=timezone.utc)

    records = asyncio.run(
        service.list_growth_in_range(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=20,
        )
    )

    assert records == [growth]
    assert repository.list_growth_in_range_kwargs == {
        "owner_user_id": owner_user_id,
        "start_at": start_at,
        "end_at": end_at,
        "limit": 20,
    }


def test_records_service_updates_growth_preserving_omitted_fields_and_audit() -> None:
    owner_user_id = uuid4()
    infant_id = uuid4()
    growth = _growth(owner_user_id=owner_user_id)
    growth.infant_id = infant_id
    growth.height_cm = 52.0
    repository = FakeRecordsRepository(growth=growth, infant_owner_ok=True)
    audit_service = FakeAuditService()
    service = RecordsService(repository=repository, audit_service=audit_service)

    updated = asyncio.run(
        service.update_growth(
            owner_user_id=owner_user_id,
            record_id=growth.id,
            updates={"weight_kg": 4.8},
            request_id="req_growth_update",
        )
    )

    assert updated.weight_kg == 4.8
    assert updated.height_cm == 52.0
    assert repository.update_growth_kwargs["updates"] == {"weight_kg": 4.8}
    assert audit_service.record_kwargs["action"] == "records.growth.update"
    assert audit_service.record_kwargs["details"] == {"fields": ["weight_kg"]}


def test_records_service_rejects_growth_update_that_clears_all_measurements() -> None:
    owner_user_id = uuid4()
    growth = _growth(owner_user_id=owner_user_id)
    repository = FakeRecordsRepository(growth=growth)
    service = RecordsService(repository=repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_growth(
                owner_user_id=owner_user_id,
                record_id=growth.id,
                updates={"weight_kg": None},
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert repository.update_growth_kwargs == {}


def _now() -> datetime:
    return datetime(2026, 7, 2, 8, 0, tzinfo=timezone.utc)


def _feeding(*, owner_user_id: UUID, record_id: UUID | None = None, plan_task_id: UUID | None = None) -> FeedingRecord:
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
        plan_task_id=plan_task_id,
    )


def _pumping(*, owner_user_id: UUID, record_id: UUID | None = None, plan_task_id: UUID | None = None) -> PumpingRecord:
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
        plan_task_id=plan_task_id,
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
        plan_task_owner_ok=True,
        plan_task_kind=None,
        create_feeding_error=None,
        feeding=None,
        feedings=None,
        pumping=None,
        pumpings=None,
        growth=None,
        growths=None,
    ) -> None:
        self.infant_owner_ok = infant_owner_ok
        self.plan_task_owner_ok = plan_task_owner_ok
        self.plan_task_kind = plan_task_kind
        self.create_feeding_error = create_feeding_error
        self.completed_plan_task_id = None
        self.feeding = feeding
        self.feedings = feedings or []
        self.pumping = pumping
        self.pumpings = pumpings or []
        self.growth = growth
        self.growths = growths or []
        self.create_feeding_kwargs = {}
        self.create_pumping_kwargs = {}
        self.create_growth_kwargs = {}
        self.update_feeding_kwargs = {}
        self.update_pumping_kwargs = {}
        self.update_growth_kwargs = {}
        self.list_feedings_kwargs = {}
        self.list_pumpings_kwargs = {}
        self.list_growth_kwargs = {}
        self.list_growth_in_range_kwargs = {}
        self.deleted_feeding = None
        self.deleted_pumping = None
        self.deleted_growth = None

    async def infant_belongs_to_owner(self, *, infant_id: UUID, owner_user_id: UUID):
        return self.infant_owner_ok

    async def get_plan_task_for_owner(self, *, plan_task_id: UUID, owner_user_id: UUID):
        if not self.plan_task_owner_ok:
            return None
        payload = {} if self.plan_task_kind is None else {"task_type": self.plan_task_kind}
        return SimpleNamespace(id=plan_task_id, payload=payload)

    async def complete_plan_task(self, *, plan_task_id: UUID, owner_user_id: UUID):
        self.completed_plan_task_id = plan_task_id
        return True

    async def create_feeding(self, **kwargs):
        if self.create_feeding_error is not None:
            raise self.create_feeding_error
        self.create_feeding_kwargs = kwargs
        self.feeding = _feeding(owner_user_id=kwargs["owner_user_id"], plan_task_id=kwargs["plan_task_id"])
        self.feeding.infant_id = kwargs["infant_id"]
        return self.feeding

    async def get_feeding_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return self.feeding

    async def list_feedings(self, **kwargs):
        self.list_feedings_kwargs = kwargs
        return self.feedings

    async def update_feeding(self, **kwargs):
        self.update_feeding_kwargs = kwargs
        if self.feeding is None:
            return None
        for field, value in kwargs["updates"].items():
            setattr(self.feeding, field, value)
        return self.feeding

    async def soft_delete_feeding(self, **kwargs):
        if self.feeding is None:
            return None
        self.feeding.status = "deleted"
        self.feeding.deleted_at = kwargs["deleted_at"]
        self.deleted_feeding = self.feeding
        return self.feeding

    async def create_pumping(self, **kwargs):
        self.create_pumping_kwargs = kwargs
        self.pumping = _pumping(owner_user_id=kwargs["owner_user_id"], plan_task_id=kwargs["plan_task_id"])
        return self.pumping

    async def get_pumping_for_owner(self, *, record_id: UUID, owner_user_id: UUID):
        return self.pumping

    async def list_pumpings(self, **kwargs):
        self.list_pumpings_kwargs = kwargs
        return self.pumpings

    async def update_pumping(self, **kwargs):
        self.update_pumping_kwargs = kwargs
        if self.pumping is None:
            return None
        for field, value in kwargs["updates"].items():
            setattr(self.pumping, field, value)
        return self.pumping

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

    async def update_growth(self, **kwargs):
        self.update_growth_kwargs = kwargs
        if self.growth is None:
            return None
        for field, value in kwargs["updates"].items():
            setattr(self.growth, field, value)
        return self.growth

    async def list_growth(self, **kwargs):
        self.list_growth_kwargs = kwargs
        return self.growths

    async def list_growth_in_range(self, **kwargs):
        self.list_growth_in_range_kwargs = kwargs
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
