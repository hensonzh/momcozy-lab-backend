from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import ApiError
from app.modules.plans.models import PlanTask
from app.modules.plans.service import PlansService

from .lactation_timeline_schema import (
    LactationTimelineCounts,
    LactationTimelineEventType,
    LactationTimelineItem,
    LactationTimelineReadOutput,
    LactationTimelineRecord,
    LactationTimelineSchedule,
    LactationTimelineState,
)
from .models import FeedingRecord, GrowthRecord, PumpingRecord
from .service import RecordsService


MAX_LACTATION_TIMELINE_DAYS = 31
MAX_LACTATION_TIMELINE_ITEMS = 50


class LactationTimelineService:
    def __init__(
        self,
        *,
        records_service: RecordsService,
        plans_service: PlansService,
    ) -> None:
        self.records_service = records_service
        self.plans_service = plans_service

    async def read(
        self,
        *,
        owner_user_id: UUID,
        as_of_date: date,
        start_date: date,
        end_date: date,
        timezone_name: str,
        limit: int,
    ) -> LactationTimelineReadOutput:
        _validate_range(start_date=start_date, end_date=end_date)
        if limit < 1 or limit > MAX_LACTATION_TIMELINE_ITEMS:
            raise ApiError(
                code="validation_failed",
                message=f"limit must be between 1 and {MAX_LACTATION_TIMELINE_ITEMS}.",
                status=422,
            )
        local_timezone = _timezone(timezone_name)
        start_at, end_at = _utc_window(
            start_date=start_date,
            end_date=end_date,
            local_timezone=local_timezone,
        )
        fetch_limit = limit + 1
        tasks = await self.plans_service.list_milk_timeline_tasks(
            owner_user_id=owner_user_id,
            start_date=start_date,
            end_date=end_date,
            limit=fetch_limit,
        )
        feedings = await self.records_service.list_feedings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=fetch_limit,
        )
        pumpings = await self.records_service.list_pumpings(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=fetch_limit,
        )
        growth = await self.records_service.list_growth_in_range(
            owner_user_id=owner_user_id,
            start_at=start_at,
            end_at=end_at,
            limit=fetch_limit,
        )

        ordered_items = _timeline_items(
            tasks=tasks,
            feedings=feedings,
            pumpings=pumpings,
            growth=growth,
            local_timezone=local_timezone,
        )
        truncated = len(ordered_items) > limit
        items = [item for _, item in ordered_items[:limit]]
        return LactationTimelineReadOutput(
            as_of_date=as_of_date,
            timezone=local_timezone.key,
            start_date=start_date,
            end_date=end_date,
            items=items,
            counts=_counts(items),
            truncated=truncated,
        )


def _timeline_items(
    *,
    tasks: list[PlanTask],
    feedings: list[FeedingRecord],
    pumpings: list[PumpingRecord],
    growth: list[GrowthRecord],
    local_timezone: ZoneInfo,
) -> list[tuple[datetime, LactationTimelineItem]]:
    records_by_task_id: dict[UUID, list[LactationTimelineRecord]] = {}
    unlinked_records: list[LactationTimelineRecord] = []
    for feeding_record in feedings:
        payload = _feeding_record(record=feeding_record, local_timezone=local_timezone)
        _group_record(
            record=payload,
            plan_task_id=feeding_record.plan_task_id,
            records_by_task_id=records_by_task_id,
            unlinked_records=unlinked_records,
        )
    for pumping_record in pumpings:
        payload = _pumping_record(record=pumping_record, local_timezone=local_timezone)
        _group_record(
            record=payload,
            plan_task_id=pumping_record.plan_task_id,
            records_by_task_id=records_by_task_id,
            unlinked_records=unlinked_records,
        )
    unlinked_records.extend(_growth_record(record=growth_record, local_timezone=local_timezone) for growth_record in growth)

    ordered: list[tuple[datetime, LactationTimelineItem]] = []
    loaded_task_ids = {task.id for task in tasks}
    for task in tasks:
        actual_records = sorted(records_by_task_id.get(task.id, []), key=lambda value: value.occurred_at)
        schedule = _schedule(task=task, local_timezone=local_timezone)
        event_type = _task_event_type(task)
        if actual_records:
            event_type = _record_event_type(actual_records[0])
        item = LactationTimelineItem(
            item_id=f"plan_task:{task.id}",
            event_type=event_type,
            state=_task_state(task=task, records=actual_records),
            schedule=schedule,
            records=actual_records,
        )
        sort_at = actual_records[0].occurred_at if actual_records else schedule.scheduled_at
        ordered.append((sort_at or _date_fallback(task.task_date, local_timezone), item))

    for task_id, actual_records in records_by_task_id.items():
        if task_id in loaded_task_ids:
            continue
        unlinked_records.extend(actual_records)

    for actual_record in unlinked_records:
        item = LactationTimelineItem(
            item_id=f"{actual_record.record_type}_record:{actual_record.record_id}",
            event_type=_record_event_type(actual_record),
            state="recorded",
            schedule=None,
            records=[actual_record],
        )
        ordered.append((actual_record.occurred_at, item))

    ordered.sort(key=lambda pair: (pair[0], pair[1].item_id))
    return ordered


def _group_record(
    *,
    record: LactationTimelineRecord,
    plan_task_id: UUID | None,
    records_by_task_id: dict[UUID, list[LactationTimelineRecord]],
    unlinked_records: list[LactationTimelineRecord],
) -> None:
    if plan_task_id is None:
        unlinked_records.append(record)
        return
    records_by_task_id.setdefault(plan_task_id, []).append(record)


def _schedule(*, task: PlanTask, local_timezone: ZoneInfo) -> LactationTimelineSchedule:
    return LactationTimelineSchedule(
        task_id=task.id,
        plan_id=task.plan_id,
        scheduled_at=_scheduled_at(task=task, local_timezone=local_timezone),
        title=task.title,
        description=task.description,
        status=task.status,
        completed_at=_local_datetime(task.completed_at, local_timezone),
    )


def _feeding_record(*, record: FeedingRecord, local_timezone: ZoneInfo) -> LactationTimelineRecord:
    return LactationTimelineRecord(
        record_type="feeding",
        record_id=record.id,
        plan_task_id=record.plan_task_id,
        infant_id=record.infant_id,
        occurred_at=_required_local_datetime(record.feed_time, local_timezone),
        ended_at=None,
        title=record.title,
        volume_ml=record.volume_ml,
        duration_seconds=record.duration_seconds,
        feed_type=record.feed_type,
        feed_action=record.feed_action,
    )


def _pumping_record(*, record: PumpingRecord, local_timezone: ZoneInfo) -> LactationTimelineRecord:
    return LactationTimelineRecord(
        record_type="pumping",
        record_id=record.id,
        plan_task_id=record.plan_task_id,
        infant_id=None,
        occurred_at=_required_local_datetime(record.pump_start_time, local_timezone),
        ended_at=_local_datetime(record.pump_end_time, local_timezone),
        title=record.title,
        milk_volume_ml=record.milk_volume_ml,
        duration_seconds=record.duration_seconds,
        pump_type=record.pump_type,
        source=record.source,
    )


def _growth_record(*, record: GrowthRecord, local_timezone: ZoneInfo) -> LactationTimelineRecord:
    return LactationTimelineRecord(
        record_type="growth",
        record_id=record.id,
        plan_task_id=None,
        infant_id=record.infant_id,
        occurred_at=_required_local_datetime(record.measured_at, local_timezone),
        ended_at=None,
        title="",
        height_cm=record.height_cm,
        weight_kg=record.weight_kg,
        head_cm=record.head_cm,
    )


def _task_event_type(task: PlanTask) -> LactationTimelineEventType:
    payload: dict[str, Any] = task.payload if isinstance(task.payload, dict) else {}
    task_type = str(payload.get("task_type") or "").strip().lower()
    if task_type in {"feeding", "breastfeeding", "bottle_feeding"}:
        return "feeding"
    if task_type == "pumping":
        return "pumping"
    return "other"


def _record_event_type(record: LactationTimelineRecord) -> LactationTimelineEventType:
    return record.record_type


def _task_state(
    *,
    task: PlanTask,
    records: list[LactationTimelineRecord],
) -> LactationTimelineState:
    if records:
        return "recorded"
    if task.status == "completed":
        return "completed"
    if task.status == "skipped":
        return "skipped"
    return "pending"


def _counts(items: list[LactationTimelineItem]) -> LactationTimelineCounts:
    return LactationTimelineCounts(
        pending=sum(item.state == "pending" for item in items),
        completed=sum(item.state == "completed" for item in items),
        skipped=sum(item.state == "skipped" for item in items),
        recorded=sum(item.state == "recorded" for item in items),
    )


def _validate_range(*, start_date: date, end_date: date) -> None:
    window_days = (end_date - start_date).days + 1
    if window_days < 1 or window_days > MAX_LACTATION_TIMELINE_DAYS:
        raise ApiError(
            code="validation_failed",
            message=f"Timeline date range must contain 1 to {MAX_LACTATION_TIMELINE_DAYS} days.",
            status=422,
        )


def _timezone(value: str) -> ZoneInfo:
    normalized = value.strip() or "UTC"
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ApiError(code="validation_failed", message="A valid IANA timezone is required.", status=422) from exc


def _utc_window(
    *,
    start_date: date,
    end_date: date,
    local_timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    start_at = datetime.combine(start_date, time.min, tzinfo=local_timezone).astimezone(timezone.utc)
    end_at = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=local_timezone).astimezone(timezone.utc)
    return start_at, end_at


def _scheduled_at(*, task: PlanTask, local_timezone: ZoneInfo) -> datetime | None:
    if task.task_date is None or not task.task_time:
        return None
    try:
        task_time = time.fromisoformat(task.task_time)
    except ValueError:
        return None
    return datetime.combine(task.task_date, task_time, tzinfo=local_timezone)


def _date_fallback(value: date | None, local_timezone: ZoneInfo) -> datetime:
    return datetime.combine(value or date.min, time.min, tzinfo=local_timezone)


def _required_local_datetime(value: datetime, local_timezone: ZoneInfo) -> datetime:
    return _local_datetime(value, local_timezone) or datetime.min.replace(tzinfo=local_timezone)


def _local_datetime(value: datetime | None, local_timezone: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(local_timezone)
