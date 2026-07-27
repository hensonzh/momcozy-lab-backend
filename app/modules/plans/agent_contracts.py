from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentPlansActionType = Literal[
    "plans.task.create",
    "plans.task.complete",
    "plans.task.update",
    "plans.task.delete",
    "plans.plan.update",
    "plans.plan.delete",
    "pregnancy.plan.create",
    "plans.milk_schedule.reschedule",
]
_TIME_PATTERN = r"^(?:[01]\d|2[0-3]):[0-5]\d$"


class AgentPlanSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    plan_type: str
    title: str
    summary: str
    status: str
    source: str
    starts_on: Date | None = None
    ends_on: Date | None = None
    version: int = Field(ge=1)
    updated_at: datetime


class AgentPlanDetail(AgentPlanSummary):
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentPlanTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    plan_id: UUID | None = None
    task_date: Date | None = None
    task_time: str
    title: str
    status: str
    completed_at: datetime | None = None


class AgentPlansCurrentReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: list[AgentPlanSummary]
    tasks: list[AgentPlanTaskSummary]
    counts: dict[str, int]


class AgentPlansCalendarReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[AgentPlanTaskSummary]
    count: int = Field(ge=0)
    filters: dict[str, Any]


class AgentPlanTaskCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID | None = None
    task_date: Date | None = None
    task_time: str = Field(default="", max_length=16)
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2_000)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentPlanTaskCompletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    completed: bool = True


class AgentPlanTaskUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    plan_id: UUID | None = None
    task_date: Date | None = None
    task_time: str | None = Field(default=None, min_length=1, max_length=16)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1, max_length=2_000)
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_updates(self) -> AgentPlanTaskUpdatePayload:
        update_fields = self.model_fields_set - {"task_id"}
        if not update_fields:
            raise ValueError("at least one task update field is required")
        for field in ("plan_id", "task_date", "task_time", "title", "description", "payload"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AgentPlanTaskDeletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    reason: str = Field(default="", max_length=500)


class AgentPlanDeletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID
    reason: str = Field(default="", max_length=500)


class AgentPlanUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def require_update(self) -> AgentPlanUpdatePayload:
        if not (self.model_fields_set & {"title", "summary"}):
            raise ValueError("title or summary is required")
        return self


class AgentPregnancyPlanCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(default="", max_length=20_000)
    payload: dict[str, Any]


class AgentMilkScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    expected_plan_id: UUID
    expected_task_date: Date
    expected_task_time: str = Field(pattern=_TIME_PATTERN)
    new_task_date: Date
    new_task_time: str = Field(pattern=_TIME_PATTERN)


class AgentMilkScheduleCalendarEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: Date
    start_time: str = Field(pattern=_TIME_PATTERN)
    end_time: str = Field(pattern=_TIME_PATTERN)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class AgentMilkScheduleReschedulePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID
    updates: list[AgentMilkScheduleUpdate] = Field(default_factory=list, max_length=100)
    calendar_events: list[AgentMilkScheduleCalendarEvent] = Field(
        default_factory=list,
        max_length=21,
    )

    @model_validator(mode="after")
    def require_change(self) -> AgentMilkScheduleReschedulePayload:
        if not self.updates and not self.calendar_events:
            raise ValueError("at least one schedule change is required")
        return self


AgentPlansActionPayload = (
    AgentPlanTaskCreatePayload
    | AgentPlanTaskCompletePayload
    | AgentPlanTaskUpdatePayload
    | AgentPlanTaskDeletePayload
    | AgentPlanUpdatePayload
    | AgentPlanDeletePayload
    | AgentPregnancyPlanCreatePayload
    | AgentMilkScheduleReschedulePayload
)

_ACTION_PAYLOAD_MODELS: dict[AgentPlansActionType, type[BaseModel]] = {
    "plans.task.create": AgentPlanTaskCreatePayload,
    "plans.task.complete": AgentPlanTaskCompletePayload,
    "plans.task.update": AgentPlanTaskUpdatePayload,
    "plans.task.delete": AgentPlanTaskDeletePayload,
    "plans.plan.update": AgentPlanUpdatePayload,
    "plans.plan.delete": AgentPlanDeletePayload,
    "pregnancy.plan.create": AgentPregnancyPlanCreatePayload,
    "plans.milk_schedule.reschedule": AgentMilkScheduleReschedulePayload,
}


class AgentPlansActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    action_type: AgentPlansActionType
    payload: AgentPlansActionPayload

    @model_validator(mode="after")
    def validate_action_payload(self) -> AgentPlansActionRequest:
        payload_model = _ACTION_PAYLOAD_MODELS[self.action_type]
        payload = payload_model.model_validate(
            self.payload.model_dump(mode="json", exclude_unset=True)
        )
        object.__setattr__(self, "payload", payload)
        return self


class AgentPlansApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["plan", "plan_task"]
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)
