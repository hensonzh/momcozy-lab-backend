from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import ApiError
from app.modules.records.models import FeedingRecord, GrowthRecord, PumpingRecord
from app.modules.records.service import RecordsService

from .models import Plan, PlanTask
from .repository import ScheduleTimelineTaskRow
from .schedule_domain import (
    normalize_schedule_domains,
    schedule_domain_for_plan_type,
    schedule_domain_for_task,
    schedule_event_type,
)
from .schedule_timeline_schema import (
    ScheduleTimelineCounts,
    ScheduleTimelineExecution,
    ScheduleTimelineItem,
    ScheduleTimelinePlanSummary,
    ScheduleTimelineReadOutput,
    ScheduleTimelineSchedule,
    ScheduleTimelineState,
)
from .service import PlansService


MAX_SCHEDULE_TIMELINE_DAYS = 31
MAX_SCHEDULE_TIMELINE_ITEMS = 50
MAX_ACTIVE_SCHEDULE_PLANS = 20
SCHEDULE_TIMELINE_STATES = frozenset({"pending", "completed", "skipped", "recorded"})


class ScheduleTimelineService:
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
        domains: tuple[str, ...],
        states: tuple[str, ...] = (),
    ) -> ScheduleTimelineReadOutput:
        _validate_range(start_date=start_date, end_date=end_date)
        if limit < 1 or limit > MAX_SCHEDULE_TIMELINE_ITEMS:
            raise ApiError(
                code="validation_failed",
                message=f"limit must be between 1 and {MAX_SCHEDULE_TIMELINE_ITEMS}.",
                status=422,
            )
        try:
            normalized_domains = normalize_schedule_domains(domains)
        except ValueError as exc:
            raise ApiError(code="validation_failed", message="A valid schedule domain is required.", status=422) from exc
        normalized_states = tuple(dict.fromkeys(value.strip().lower() for value in states))
        if any(value not in SCHEDULE_TIMELINE_STATES for value in normalized_states):
            raise ApiError(code="validation_failed", message="A valid timeline state is required.", status=422)
        local_timezone = _timezone(timezone_name)
        start_at, end_at = _utc_window(
            start_date=start_date,
            end_date=end_date,
            local_timezone=local_timezone,
        )
        fetch_limit = limit + 1
        active_plans = await self.plans_service.list_schedule_timeline_plans(
            owner_user_id=owner_user_id,
            domains=normalized_domains,
            status="active",
            limit=MAX_ACTIVE_SCHEDULE_PLANS,
        )
        task_rows = await self.plans_service.list_schedule_timeline_tasks(
            owner_user_id=owner_user_id,
            start_date=start_date,
            end_date=end_date,
            domains=normalized_domains,
            limit=fetch_limit,
        )
        feedings: list[FeedingRecord] = []
        pumpings: list[PumpingRecord] = []
        growth: list[GrowthRecord] = []
        if "lactation" in normalized_domains:
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
            task_rows=task_rows,
            feedings=feedings,
            pumpings=pumpings,
            growth=growth,
            local_timezone=local_timezone,
        )
        if normalized_states:
            selected_states = set(normalized_states)
            ordered_items = [pair for pair in ordered_items if pair[1].state in selected_states]
        truncated = len(ordered_items) > limit
        items = [item for _, item in ordered_items[:limit]]
        return ScheduleTimelineReadOutput(
            as_of_date=as_of_date,
            timezone=local_timezone.key,
            start_date=start_date,
            end_date=end_date,
            domains=list(normalized_domains),
            plans=[_plan_summary(plan) for plan in active_plans],
            items=items,
            counts=_counts(items),
            truncated=truncated,
        )


def _plan_summary(plan: Plan) -> ScheduleTimelinePlanSummary:
    payload = plan.payload if isinstance(plan.payload, dict) else {}
    start_date = _payload_date(payload.get("start_date"))
    days = _payload_positive_int(payload.get("days"))
    end_date = start_date + timedelta(days=days - 1) if start_date is not None and days is not None else None
    direction = str(payload.get("direction") or "").strip() or None
    return ScheduleTimelinePlanSummary(
        plan_id=plan.id,
        domain=schedule_domain_for_plan_type(plan.plan_type),
        plan_type=plan.plan_type,
        title=plan.title,
        summary=plan.summary[:2000],
        status=plan.status,
        direction=direction,
        start_date=start_date,
        end_date=end_date,
    )


def _payload_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _payload_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _timeline_items(
    *,
    task_rows: list[ScheduleTimelineTaskRow],
    feedings: list[FeedingRecord],
    pumpings: list[PumpingRecord],
    growth: list[GrowthRecord],
    local_timezone: ZoneInfo,
) -> list[tuple[datetime, ScheduleTimelineItem]]:
    executions_by_task_id: dict[UUID, list[ScheduleTimelineExecution]] = {}
    unlinked_executions: list[ScheduleTimelineExecution] = []
    for feeding_record in feedings:
        execution = _feeding_record(record=feeding_record, local_timezone=local_timezone)
        _group_execution(
            execution=execution,
            plan_task_id=feeding_record.plan_task_id,
            executions_by_task_id=executions_by_task_id,
            unlinked_executions=unlinked_executions,
        )
    for pumping_record in pumpings:
        execution = _pumping_record(record=pumping_record, local_timezone=local_timezone)
        _group_execution(
            execution=execution,
            plan_task_id=pumping_record.plan_task_id,
            executions_by_task_id=executions_by_task_id,
            unlinked_executions=unlinked_executions,
        )
    unlinked_executions.extend(
        _growth_record(record=growth_record, local_timezone=local_timezone)
        for growth_record in growth
    )

    ordered: list[tuple[datetime, ScheduleTimelineItem]] = []
    loaded_execution_task_ids: set[UUID] = set()
    for row in task_rows:
        task = row.task
        payload = task.payload if isinstance(task.payload, dict) else {}
        domain = schedule_domain_for_task(
            plan_type=row.plan.plan_type if row.plan is not None else None,
            payload=payload,
        )
        actual_executions = (
            sorted(executions_by_task_id.get(task.id, []), key=lambda value: value.occurred_at)
            if domain == "lactation"
            else []
        )
        if actual_executions:
            loaded_execution_task_ids.add(task.id)
        schedule = _schedule(task=task, local_timezone=local_timezone)
        event_type = schedule_event_type(payload)
        if actual_executions:
            event_type = actual_executions[0].record_type
        item = ScheduleTimelineItem(
            item_id=f"plan_task:{task.id}",
            domain=domain,
            event_type=event_type,
            state=_task_state(task=task, executions=actual_executions),
            schedule=schedule,
            executions=actual_executions,
        )
        sort_at = actual_executions[0].occurred_at if actual_executions else schedule.scheduled_at
        ordered.append((sort_at or _date_fallback(task.task_date, local_timezone), item))

    for task_id, actual_executions in executions_by_task_id.items():
        if task_id in loaded_execution_task_ids:
            continue
        unlinked_executions.extend(actual_executions)

    for actual_execution in unlinked_executions:
        item = ScheduleTimelineItem(
            item_id=f"{actual_execution.record_type}_record:{actual_execution.record_id}",
            domain="lactation",
            event_type=actual_execution.record_type,
            state="recorded",
            schedule=None,
            executions=[actual_execution],
        )
        ordered.append((actual_execution.occurred_at, item))

    ordered.sort(key=lambda pair: (pair[0], pair[1].item_id))
    return ordered


def _group_execution(
    *,
    execution: ScheduleTimelineExecution,
    plan_task_id: UUID | None,
    executions_by_task_id: dict[UUID, list[ScheduleTimelineExecution]],
    unlinked_executions: list[ScheduleTimelineExecution],
) -> None:
    if plan_task_id is None:
        unlinked_executions.append(execution)
        return
    executions_by_task_id.setdefault(plan_task_id, []).append(execution)


def _schedule(*, task: PlanTask, local_timezone: ZoneInfo) -> ScheduleTimelineSchedule:
    return ScheduleTimelineSchedule(
        task_id=task.id,
        plan_id=task.plan_id,
        scheduled_at=_scheduled_at(task=task, local_timezone=local_timezone),
        title=task.title,
        description=task.description,
        status=task.status,
        completed_at=_local_datetime(task.completed_at, local_timezone),
    )


def _feeding_record(*, record: FeedingRecord, local_timezone: ZoneInfo) -> ScheduleTimelineExecution:
    return ScheduleTimelineExecution(
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


def _pumping_record(*, record: PumpingRecord, local_timezone: ZoneInfo) -> ScheduleTimelineExecution:
    return ScheduleTimelineExecution(
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


def _growth_record(*, record: GrowthRecord, local_timezone: ZoneInfo) -> ScheduleTimelineExecution:
    return ScheduleTimelineExecution(
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


def _task_state(
    *,
    task: PlanTask,
    executions: list[ScheduleTimelineExecution],
) -> ScheduleTimelineState:
    if executions:
        return "recorded"
    if task.status == "completed":
        return "completed"
    if task.status == "skipped":
        return "skipped"
    return "pending"


def _counts(items: list[ScheduleTimelineItem]) -> ScheduleTimelineCounts:
    return ScheduleTimelineCounts(
        pending=sum(item.state == "pending" for item in items),
        completed=sum(item.state == "completed" for item in items),
        skipped=sum(item.state == "skipped" for item in items),
        recorded=sum(item.state == "recorded" for item in items),
    )


def _validate_range(*, start_date: date, end_date: date) -> None:
    window_days = (end_date - start_date).days + 1
    if window_days < 1 or window_days > MAX_SCHEDULE_TIMELINE_DAYS:
        raise ApiError(
            code="validation_failed",
            message=f"Timeline date range must contain 1 to {MAX_SCHEDULE_TIMELINE_DAYS} days.",
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
