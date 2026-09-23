import asyncio
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.modules.plans.models import Plan, PlanTask
from app.modules.plans.repository import ScheduleTimelineTaskRow
from app.modules.plans.schedule_timeline import ScheduleTimelineService
from app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord


def test_schedule_timeline_keeps_schedule_and_lactation_facts_distinct() -> None:
    owner_user_id = uuid4()
    plan = _plan(owner_user_id=owner_user_id, plan_type="milk_management")
    pumping_task = _task(
        owner_user_id=owner_user_id,
        plan=plan,
        task_time="08:00",
        event_type="pumping",
    )
    completed_task = _task(
        owner_user_id=owner_user_id,
        plan=plan,
        task_time="10:00",
        event_type="feeding",
        status="completed",
    )
    linked_pumping_id = uuid4()
    unscheduled_feeding_id = uuid4()
    growth_id = uuid4()
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
                plan_task_id=pumping_task.id,
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
    plans_service = FakePlansService(
        rows=[
            ScheduleTimelineTaskRow(task=pumping_task, plan=plan),
            ScheduleTimelineTaskRow(task=completed_task, plan=plan),
        ],
        plans=[plan],
    )

    result = asyncio.run(
        ScheduleTimelineService(
            records_service=records_service,
            plans_service=plans_service,
        ).read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 24),
            start_date=date(2026, 7, 24),
            end_date=date(2026, 7, 24),
            timezone_name="Asia/Shanghai",
            limit=20,
            domains=("lactation",),
        )
    )

    assert result.domains == ["lactation"]
    assert result.plans[0].domain == "lactation"
    assert result.plans[0].plan_type == "milk_management"
    assert result.counts.model_dump() == {
        "pending": 0,
        "completed": 1,
        "skipped": 0,
        "recorded": 3,
    }
    linked = next(item for item in result.items if item.schedule and item.schedule.task_id == pumping_task.id)
    assert linked.domain == "lactation"
    assert linked.state == "recorded"
    assert linked.schedule.scheduled_at.isoformat() == "2026-07-24T08:00:00+08:00"
    assert linked.executions[0].record_id == linked_pumping_id
    assert linked.executions[0].occurred_at.isoformat() == "2026-07-24T08:10:00+08:00"

    completed = next(item for item in result.items if item.schedule and item.schedule.task_id == completed_task.id)
    assert completed.state == "completed"
    assert completed.executions == []

    unplanned = next(item for item in result.items if item.item_id == f"feeding_record:{unscheduled_feeding_id}")
    assert unplanned.schedule is None
    assert unplanned.state == "recorded"
    assert unplanned.executions[0].volume_ml == 90

    growth = next(item for item in result.items if item.item_id == f"growth_record:{growth_id}")
    assert growth.executions[0].weight_kg == 6.2
    assert plans_service.task_query["domains"] == ("lactation",)
    assert records_service.feeding_query["start_at"] == datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
    assert records_service.feeding_query["end_at"] == datetime(2026, 7, 24, 16, 0, tzinfo=timezone.utc)


def test_schedule_timeline_reads_cross_domain_tasks_without_loading_lactation_records() -> None:
    owner_user_id = uuid4()
    postpartum_recovery_plan = _plan(owner_user_id=owner_user_id, plan_type="postpartum_recovery", title="产后康复计划")
    postpartum_recovery_task = _task(
        owner_user_id=owner_user_id,
        plan=postpartum_recovery_plan,
        task_time="09:00",
        event_type="appointment",
    )
    general_task = _task(
        owner_user_id=owner_user_id,
        plan=None,
        task_time="18:30",
        event_type="family",
        domain="general",
    )
    records_service = FakeRecordsService()
    plans_service = FakePlansService(
        rows=[
            ScheduleTimelineTaskRow(task=postpartum_recovery_task, plan=postpartum_recovery_plan),
            ScheduleTimelineTaskRow(task=general_task, plan=None),
        ],
        plans=[postpartum_recovery_plan],
    )

    result = asyncio.run(
        ScheduleTimelineService(
            records_service=records_service,
            plans_service=plans_service,
        ).read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 24),
            start_date=date(2026, 7, 24),
            end_date=date(2026, 7, 24),
            timezone_name="UTC",
            limit=20,
            domains=("postpartum_recovery", "general"),
            states=("pending",),
        )
    )

    assert result.domains == ["postpartum_recovery", "general"]
    assert [(item.domain, item.event_type) for item in result.items] == [
        ("postpartum_recovery", "appointment"),
        ("general", "family"),
    ]
    assert records_service.call_count == 0
    assert plans_service.plan_query["domains"] == ("postpartum_recovery", "general")


def test_internal_schedule_preview_reads_many_tasks_without_executions() -> None:
    owner_user_id = uuid4()
    plan = _plan(
        owner_user_id=owner_user_id,
        plan_type="milk_management",
    )
    task = _task(
        owner_user_id=owner_user_id,
        plan=plan,
        task_time="08:00",
        event_type="pumping",
    )
    task.payload["duration_minutes"] = 75
    records_service = FakeRecordsService()
    plans_service = FakePlansService(
        rows=[ScheduleTimelineTaskRow(task=task, plan=plan)],
        plans=[plan],
    )

    result = asyncio.run(
        ScheduleTimelineService(
            records_service=records_service,
            plans_service=plans_service,
        ).read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 24),
            start_date=date(2026, 7, 24),
            end_date=date(2026, 7, 24),
            timezone_name="Asia/Shanghai",
            limit=1_000,
            domains=("lactation",),
            states=("pending",),
            include_executions=False,
        )
    )

    assert records_service.call_count == 0
    assert plans_service.task_query["limit"] == 1_001
    schedule = result.items[0].schedule
    assert schedule is not None
    assert schedule.task_date == date(2026, 7, 24)
    assert schedule.task_time == "08:00"
    assert schedule.duration_minutes == 75


def test_timeline_reports_truncated_before_state_filtering() -> None:
    owner_user_id = uuid4()
    plan = _plan(
        owner_user_id=owner_user_id,
        plan_type="milk_management",
    )
    rows = [
        ScheduleTimelineTaskRow(
            task=_task(
                owner_user_id=owner_user_id,
                plan=plan,
                task_time=f"0{index}:00",
                event_type="pumping",
                status="completed",
            ),
            plan=plan,
        )
        for index in range(3)
    ]
    rows.append(
        ScheduleTimelineTaskRow(
            task=_task(
                owner_user_id=owner_user_id,
                plan=plan,
                task_time="09:00",
                event_type="pumping",
            ),
            plan=plan,
        )
    )

    result = asyncio.run(
        ScheduleTimelineService(
            records_service=FakeRecordsService(),
            plans_service=FakePlansService(
                rows=rows,
                plans=[plan],
            ),
        ).read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 24),
            start_date=date(2026, 7, 24),
            end_date=date(2026, 7, 24),
            timezone_name="UTC",
            limit=2,
            domains=("lactation",),
            states=("pending",),
            include_executions=False,
        )
    )

    assert result.items == []
    assert result.truncated is True


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        (date(2026, 7, 25), date(2026, 7, 24)),
        (date(2026, 7, 1), date(2026, 8, 1)),
    ],
)
def test_schedule_timeline_rejects_invalid_or_unbounded_date_ranges(
    start_date: date,
    end_date: date,
) -> None:
    service = ScheduleTimelineService(
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
                domains=("general",),
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
        self.call_count = 0

    async def list_feedings(self, **kwargs):
        self.call_count += 1
        self.feeding_query = kwargs
        return self.feedings[: kwargs["limit"]]

    async def list_pumpings(self, **kwargs):
        self.call_count += 1
        return self.pumpings[: kwargs["limit"]]

    async def list_growth_in_range(self, **kwargs):
        self.call_count += 1
        return self.growth[: kwargs["limit"]]


class FakePlansService:
    def __init__(
        self,
        *,
        rows: list[ScheduleTimelineTaskRow] | None = None,
        plans: list[Plan] | None = None,
    ) -> None:
        self.rows = rows or []
        self.plans = plans or []
        self.task_query: dict[str, object] = {}
        self.plan_query: dict[str, object] = {}

    async def list_schedule_timeline_plans(self, **kwargs):
        self.plan_query = kwargs
        return self.plans[: kwargs["limit"]]

    async def list_schedule_timeline_tasks(self, **kwargs):
        self.task_query = kwargs
        return self.rows[: kwargs["limit"]]


def _plan(
    *,
    owner_user_id: UUID,
    plan_type: str,
    title: str = "稳奶计划",
) -> Plan:
    return Plan(
        id=uuid4(),
        owner_user_id=owner_user_id,
        plan_type=plan_type,
        title=title,
        summary="保持当前节奏",
        status="active",
        source="agent_action",
        payload={
            "direction": "maintain",
            "start_date": "2026-07-24",
            "days": 7,
        },
    )


def _task(
    *,
    owner_user_id: UUID,
    plan: Plan | None,
    task_time: str,
    event_type: str,
    domain: str | None = None,
    status: str = "pending",
) -> PlanTask:
    payload = {"event_type": event_type}
    if domain is not None:
        payload["domain"] = domain
    return PlanTask(
        id=uuid4(),
        owner_user_id=owner_user_id,
        plan_id=plan.id if plan is not None else None,
        task_date=date(2026, 7, 24),
        task_time=task_time,
        title=event_type,
        description="",
        status=status,
        completed_at=(
            datetime(2026, 7, 24, 2, 5, tzinfo=timezone.utc)
            if status == "completed"
            else None
        ),
        payload=payload,
    )
