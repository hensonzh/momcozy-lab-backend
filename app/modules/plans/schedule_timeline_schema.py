from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .schedule_domain import ScheduleDomain


ScheduleTimelineState = Literal["pending", "completed", "skipped", "recorded"]
ScheduleExecutionType = Literal["feeding", "pumping", "growth"]


class ScheduleTimelinePlanSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID = Field(description="Stable UUID of the current user’s plan.")
    domain: ScheduleDomain = Field(description="Plan domain: lactation, pregnancy, postpartum recovery, or general.")
    plan_type: str = Field(description="Specific plan type in the product database.")
    title: str = Field(description="Plan title.")
    summary: str = Field(description="Brief description of the plan’s goals and approach; empty when unavailable.")
    status: str = Field(description="Persisted plan status; this list currently returns only active plans.")
    direction: str | None = Field(
        default=None,
        description="Milk supply plan direction, such as increase, maintain, or decrease; null for other plans or when unset.",
    )
    start_date: date | None = Field(default=None, description="Local date when the plan begins; null when unset.")
    end_date: date | None = Field(
        default=None,
        description="End date calculated from the start date and plan duration; null when insufficient information is available.",
    )


class ScheduleTimelineSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID = Field(description="Stable task UUID for later edits, status changes, or deletion.")
    plan_id: UUID | None = Field(default=None, description="Stable UUID of the associated plan; null for a standalone task.")
    task_date: date | None = Field(
        default=None,
        description="Persisted local task date; null when unset.",
    )
    task_time: str = Field(
        description="Persisted local task time, usually HH:MM; empty when unset.",
    )
    scheduled_at: datetime | None = Field(
        default=None,
        description="Scheduled time combining the task date and time in the user’s timezone; null when incomplete.",
    )
    title: str = Field(description="Schedule task title.")
    description: str = Field(description="Schedule task description; empty when unavailable.")
    status: str = Field(description="Persisted task status, such as pending, completed, or skipped.")
    duration_minutes: int = Field(
        ge=1,
        le=240,
        description="Normalized task duration in minutes; defaults to 30 when missing or invalid.",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="System time when the task was marked complete; not the actual time of a care activity.",
    )


class ScheduleTimelineExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: ScheduleExecutionType = Field(description="Type of actual care record: feeding, pumping, or baby growth measurement.")
    record_id: UUID = Field(description="Stable UUID of the actual record for later correction or deletion.")
    plan_task_id: UUID | None = Field(
        default=None,
        description="UUID of the associated schedule task; null for an unplanned or unlinked record.",
    )
    infant_id: UUID | None = Field(
        default=None,
        description="UUID of the baby associated with this record; null for maternal pumping or when unspecified.",
    )
    occurred_at: datetime = Field(description="Actual time of the care activity in the user’s timezone.")
    ended_at: datetime | None = Field(default=None, description="Actual end time of the care activity; null when unset.")
    title: str = Field(description="Actual record title; empty when unavailable.")
    volume_ml: float | None = Field(
        default=None,
        description="Recorded volume of milk consumed by the baby in ml; null when unknown or not a feeding record.",
    )
    milk_volume_ml: float | None = Field(
        default=None,
        description="Recorded volume of milk expressed by the mother in ml; null when unknown or not a pumping record.",
    )
    duration_seconds: int | None = Field(
        default=None,
        description="Feeding or pumping duration in seconds; null when unknown or for growth measurements.",
    )
    feed_type: str | None = Field(default=None, description="Feeding method; null for other record types.")
    feed_action: str | None = Field(default=None, description="Feeding action; null for other record types.")
    pump_type: str | None = Field(default=None, description="Pumping method or device type; null for other record types.")
    source: str | None = Field(default=None, description="Pumping record source; null for other record types.")
    height_cm: float | None = Field(default=None, description="Baby’s measured length in cm; null when not measured.")
    weight_kg: float | None = Field(default=None, description="Baby’s measured weight in kg; null when not measured.")
    head_cm: float | None = Field(default=None, description="Baby’s measured head circumference in cm; null when not measured.")


class ScheduleTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(
        description=(
            "Aggregate timeline identifier for display and deduplication only; edit schedule.task_id or executions[].record_id instead."
        )
    )
    domain: ScheduleDomain = Field(description="Domain of this timeline item.")
    event_type: str = Field(description="Event type within the domain, such as pumping, feeding, appointment, or exercise.")
    state: ScheduleTimelineState = Field(
        description="Normalized state: pending, completed without a record, skipped, or supported by an actual care record.",
    )
    schedule: ScheduleTimelineSchedule | None = Field(
        default=None,
        description="Planned task; null for an actual record without an associated schedule.",
    )
    executions: list[ScheduleTimelineExecution] = Field(
        default_factory=list,
        description="Actual care records associated with this task; empty when not performed or only marked complete manually.",
    )


class ScheduleTimelineCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: int = Field(ge=0, description="Number of returned planned items still pending.")
    completed: int = Field(ge=0, description="Number of returned planned items completed without an actual record.")
    skipped: int = Field(ge=0, description="Number of returned planned items skipped.")
    recorded: int = Field(ge=0, description="Number of returned items with an actual care record.")


class ScheduleTimelineReadOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date = Field(description="Trusted runtime local date used for this read.")
    timezone: str = Field(description="IANA timezone used for planned and actual times.")
    start_date: date = Field(description="First local date included in the timeline.")
    end_date: date = Field(description="Last local date included in the timeline.")
    domains: list[ScheduleDomain] = Field(description="Schedule domains included in this read.")
    plans: list[ScheduleTimelinePlanSummary] = Field(description="Compact summaries of currently active plans in the selected domains.")
    items: list[ScheduleTimelineItem] = Field(description="Timeline items ordered by actual occurrence time or scheduled time, ascending.")
    counts: ScheduleTimelineCounts = Field(description="Counts of returned items by normalized state.")
    truncated: bool = Field(description="Whether additional matching items were omitted because of the response limit.")


class ScheduleTimelineMutateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="Result of the schedule change, such as completed, awaiting confirmation, unchanged, or failed.")
    entry_type: Literal["schedule", "execution"] = Field(
        description="Type of timeline entry changed: planned schedule or actual care record.",
    )
    operation: Literal["create", "update", "delete", "set_status", "reschedule"] = Field(
        description="Generic schedule operation performed.",
    )
    domain: ScheduleDomain = Field(description="Actual schedule domain identified by the backend from the plan, task, or record.")
    record_type: ScheduleExecutionType | None = Field(
        default=None,
        description="Type of actual record; null for a schedule change that created no record.",
    )
    action_id: UUID | None = Field(default=None, description="UUID of the Action for this change; null when nothing changed.")
    action_type: str | None = Field(default=None, description="Internal Action type executed by the backend; null when nothing changed.")
    action_status: str | None = Field(default=None, description="Current Action status; null when nothing changed.")
    requires_confirmation: bool = Field(description="Whether this change still requires structured user confirmation.")
    confirmation_policy: Literal["always", "explicit_intent"] = Field(description="Confirmation policy applied to this change.")
    user_visible: bool = Field(description="Whether the App should show a structured confirmation card.")
    write_succeeded: bool = Field(description="Whether the product write was committed successfully.")
    preview_payload: dict[str, Any] = Field(description="Safe summary of the change for confirmation or result display.")
    error_code: str | None = Field(default=None, description="Stable Action error code when the change fails; otherwise null.")
    artifact_id: UUID | None = Field(default=None, description="UUID of the bulk-rescheduling preview card; null for other operations.")
    artifact_type: str | None = Field(default=None, description="Type of bulk-rescheduling preview card; null for other operations.")
    plan_id: UUID | None = Field(default=None, description="UUID of the plan affected by bulk rescheduling; null for other operations.")
    conflict_count: int | None = Field(default=None, ge=0, description="Number of conflicts detected during bulk rescheduling.")
    updated_count: int | None = Field(default=None, ge=0, description="Number of tasks that bulk rescheduling would change.")
    affected_dates: list[date] | None = Field(default=None, description="Local dates affected by bulk rescheduling.")
