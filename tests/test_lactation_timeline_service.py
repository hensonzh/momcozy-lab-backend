import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.modules.plans.models import PlanTask
from app.modules.records.lactation_timeline import LactationTimelineService
from app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord


def test_lactation_timeline_merges_linked_records_and_keeps_unscheduled_facts() -> None:
    owner_user_id = uuid4()
    pumping_task_id = uuid4()
    completed_task_id = uuid4()
    pending_task_id = uuid4()
    linked_pumping_id = uuid4()
    unscheduled_feeding_id = uuid4()
    growth_id = uuid4()

    tasks = [
        _task(
            owner_user_id=owner_user_id,
            task_id=pumping_task_id,
            task_date=date(2026, 7, 24),
            task_time="08:00",
            task_type="pumping",
        ),
        _task(
            owner_user_id=owner_user_id,
            task_id=completed_task_id,
            task_date=date(2026, 7, 24),
            task_time="10:00",
            task_type="feeding",
            status="completed",
            completed_at=datetime(2026, 7, 24, 2, 5, tzinfo=timezone.utc),
        ),
        _task(
            owner_user_id=owner_user_id,
            task_id=pending_task_id,
            task_date=date(2026, 7, 25),
            task_time="09:00",
            task_type="pumping",
        ),
    ]
    records_service = FakeRecordsService(
        feedings=[
            FeedingRecord(
                id=unscheduled_feeding_id,
                owner_user_id=owner_user_id,
                plan_task_id=None,
                infant_id=uuid4(),
                feed_time=datetime(2026, 7, 24, 3, 0, tzinfo=timezone.utc),
                feed_type="bottle",
                feed_action="fed",
                volume_ml=90,
                duration_seconds=None,
                title="临时瓶喂",
            )
        ],
        pumpings=[
            PumpingRecord(
                id=linked_pumping_id,
                owner_user_id=owner_user_id,
                plan_task_id=pumping_task_id,
                pump_start_time=datetime(2026, 7, 24, 0, 10, tzinfo=timezone.utc),
                pump_end_time=datetime(2026, 7, 24, 0, 28, tzinfo=timezone.utc),
                milk_volume_ml=120,
                pump_type="electric",
                duration_seconds=1080,
                source="device",
                title="晨间吸奶",
            )
        ],
        growth=[
            GrowthRecord(
                id=growth_id,
                owner_user_id=owner_user_id,
                infant_id=uuid4(),
                measured_at=datetime(2026, 7, 24, 4, 0, tzinfo=timezone.utc),
                height_cm=62,
                weight_kg=6.2,
                head_cm=40,
            )
        ],
    )
    plans_service = FakePlansService(tasks=tasks)
    service = LactationTimelineService(
        records_service=records_service,
        plans_service=plans_service,
    )

    result = asyncio.run(
        service.read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 24),
            start_date=date(2026, 7, 24),
            end_date=date(2026, 7, 25),
            timezone_name="Asia/Shanghai",
            limit=20,
        )
    )

    assert result.as_of_date == date(2026, 7, 24)
    assert result.start_date == date(2026, 7, 24)
    assert result.end_date == date(2026, 7, 25)
    assert result.timezone == "Asia/Shanghai"
    assert result.truncated is False
    assert len(result.items) == 5
    assert result.counts.model_dump() == {
        "pending": 1,
        "completed": 1,
        "skipped": 0,
        "recorded": 3,
    }

    linked = next(item for item in result.items if item.schedule and item.schedule.task_id == pumping_task_id)
    assert linked.state == "recorded"
    assert linked.event_type == "pumping"
    assert linked.schedule.scheduled_at.isoformat() == "2026-07-24T08:00:00+08:00"
    assert [record.record_id for record in linked.records] == [linked_pumping_id]
    assert linked.records[0].occurred_at.isoformat() == "2026-07-24T08:10:00+08:00"
    assert linked.records[0].milk_volume_ml == 120

    completed = next(item for item in result.items if item.schedule and item.schedule.task_id == completed_task_id)
    assert completed.state == "completed"
    assert completed.records == []

    unplanned = next(item for item in result.items if item.item_id == f"feeding_record:{unscheduled_feeding_id}")
    assert unplanned.schedule is None
    assert unplanned.state == "recorded"
    assert unplanned.records[0].volume_ml == 90

    growth = next(item for item in result.items if item.item_id == f"growth_record:{growth_id}")
    assert growth.event_type == "growth"
    assert growth.records[0].weight_kg == 6.2

    expected_start = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
    expected_end = datetime(2026, 7, 25, 16, 0, tzinfo=timezone.utc)
    assert records_service.feeding_query["start_at"] == expected_start
    assert records_service.feeding_query["end_at"] == expected_end
    assert records_service.pumping_query["start_at"] == expected_start
    assert records_service.pumping_query["end_at"] == expected_end
    assert records_service.growth_query["start_at"] == expected_start
    assert records_service.growth_query["end_at"] == expected_end
    assert plans_service.query == {
        "owner_user_id": owner_user_id,
        "start_date": date(2026, 7, 24),
        "end_date": date(2026, 7, 25),
        "limit": 21,
    }


def test_lactation_timeline_groups_multiple_records_linked_to_one_task() -> None:
    owner_user_id = uuid4()
    task_id = uuid4()
    task = _task(
        owner_user_id=owner_user_id,
        task_id=task_id,
        task_date=date(2026, 7, 24),
        task_time="08:00",
        task_type="feeding",
    )
    records = [
        FeedingRecord(
            id=uuid4(),
            owner_user_id=owner_user_id,
            plan_task_id=task_id,
            infant_id=uuid4(),
            feed_time=datetime(2026, 7, 24, hour, tzinfo=timezone.utc),
            feed_type="breast",
            feed_action="fed",
            volume_ml=None,
            duration_seconds=600,
            title="",
        )
        for hour in (0, 1)
    ]
    service = LactationTimelineService(
        records_service=FakeRecordsService(feedings=records),
        plans_service=FakePlansService(tasks=[task]),
    )

    result = asyncio.run(
        service.read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 24),
            start_date=date(2026, 7, 24),
            end_date=date(2026, 7, 24),
            timezone_name="UTC",
            limit=20,
        )
    )

    assert len(result.items) == 1
    assert result.items[0].schedule is not None
    assert result.items[0].schedule.task_id == task_id
    assert len(result.items[0].records) == 2
    assert result.counts.recorded == 1


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        (date(2026, 7, 25), date(2026, 7, 24)),
        (date(2026, 7, 1), date(2026, 8, 1)),
    ],
)
def test_lactation_timeline_rejects_invalid_or_unbounded_date_ranges(
    start_date: date,
    end_date: date,
) -> None:
    service = LactationTimelineService(
        records_service=FakeRecordsService(),
        plans_service=FakePlansService(),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.read(
                owner_user_id=uuid4(),
                as_of_date=date(2026, 7, 24),
                start_date=start_date,
                end_date=end_date,
                timezone_name="UTC",
                limit=20,
            )
        )

    assert exc_info.value.code == "validation_failed"


class FakeRecordsService:
    def __init__(
        self,
        *,
        feedings: list[FeedingRecord] | None = None,
        pumpings: list[PumpingRecord] | None = None,
        growth: list[GrowthRecord] | None = None,
    ) -> None:
        self.feedings = feedings or []
        self.pumpings = pumpings or []
        self.growth = growth or []
        self.feeding_query: dict[str, object] = {}
        self.pumping_query: dict[str, object] = {}
        self.growth_query: dict[str, object] = {}

    async def list_feedings(self, **kwargs):
        self.feeding_query = kwargs
        return self.feedings[: kwargs["limit"]]

    async def list_pumpings(self, **kwargs):
        self.pumping_query = kwargs
        return self.pumpings[: kwargs["limit"]]

    async def list_growth_in_range(self, **kwargs):
        self.growth_query = kwargs
        return self.growth[: kwargs["limit"]]


class FakePlansService:
    def __init__(self, *, tasks: list[PlanTask] | None = None) -> None:
        self.tasks = tasks or []
        self.query: dict[str, object] = {}

    async def list_milk_timeline_tasks(self, **kwargs):
        self.query = kwargs
        return self.tasks[: kwargs["limit"]]


def _task(
    *,
    owner_user_id: UUID,
    task_id: UUID,
    task_date: date,
    task_time: str,
    task_type: str,
    status: str = "pending",
    completed_at: datetime | None = None,
) -> PlanTask:
    return PlanTask(
        id=task_id,
        owner_user_id=owner_user_id,
        plan_id=uuid4(),
        task_date=task_date,
        task_time=task_time,
        title="吸奶" if task_type == "pumping" else "喂养",
        description="",
        status=status,
        completed_at=completed_at,
        payload={"task_type": task_type},
    )
