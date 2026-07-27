from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .milk_analysis_schema import (
    MilkAnalysisFeedingRecord,
    MilkAnalysisGrowthRecord,
    MilkAnalysisInterpretation,
    MilkAnalysisPumpingRecord,
    MilkAnalysisPumpingRhythm,
    MilkAnalysisReview,
    MilkAnalysisTrendDay,
)


LactationRecordOperation = Literal["create", "update", "delete"]
LactationRecordItemType = Literal["feeding", "pumping", "growth"]

_COMMON_FIELDS = {"operation", "item_type"}
_RECORD_FIELDS = {
    "feeding": {
        "plan_task_id",
        "infant_id",
        "occurred_at",
        "feed_type",
        "feed_action",
        "volume_ml",
        "duration_seconds",
        "title",
    },
    "pumping": {
        "plan_task_id",
        "occurred_at",
        "ended_at",
        "milk_volume_ml",
        "duration_seconds",
        "pump_type",
        "source",
        "title",
    },
    "growth": {
        "infant_id",
        "occurred_at",
        "height_cm",
        "weight_kg",
        "head_cm",
    },
}


class AgentLactationRecordApplyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: LactationRecordOperation
    item_type: LactationRecordItemType
    record_id: UUID | None = None
    plan_task_id: UUID | None = None
    infant_id: UUID | None = None
    occurred_at: datetime | None = None
    ended_at: datetime | None = None
    title: str | None = Field(default=None, max_length=255)
    feed_type: str | None = Field(default=None, max_length=32)
    feed_action: str | None = Field(default=None, max_length=32)
    volume_ml: float | None = Field(default=None, ge=0)
    milk_volume_ml: float | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    pump_type: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=32)
    height_cm: float | None = Field(default=None, gt=0)
    weight_kg: float | None = Field(default=None, gt=0)
    head_cm: float | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_operation_fields(self) -> AgentLactationRecordApplyPayload:
        supplied = set(self.model_fields_set)
        allowed = set(_COMMON_FIELDS)
        if self.operation == "delete":
            allowed.update({"record_id", "reason"})
        else:
            allowed.update(_RECORD_FIELDS[self.item_type])
            if self.operation == "update":
                allowed.add("record_id")
        if supplied - allowed:
            raise ValueError("payload contains fields unsupported by this operation and item_type")

        if self.operation in {"update", "delete"} and self.record_id is None:
            raise ValueError("record_id is required for update and delete")
        if self.operation == "create" and self.occurred_at is None:
            raise ValueError("occurred_at is required for create")
        if self.operation == "update" and not (
            supplied & _RECORD_FIELDS[self.item_type]
        ):
            raise ValueError("at least one record update field is required")

        if self.operation == "create" and self.item_type == "feeding":
            if not str(self.feed_type or "").strip():
                raise ValueError("feed_type is required for feeding create")
            if self.volume_ml is None and self.duration_seconds is None:
                raise ValueError(
                    "volume_ml or duration_seconds is required for feeding create"
                )
        if (
            self.item_type == "feeding"
            and "feed_type" in supplied
            and not str(self.feed_type or "").strip()
        ):
            raise ValueError("feed_type must not be empty")
        if (
            self.operation == "create"
            and self.item_type == "pumping"
            and self.milk_volume_ml is None
            and self.duration_seconds is None
        ):
            raise ValueError(
                "milk_volume_ml or duration_seconds is required for pumping create"
            )
        if (
            self.operation == "create"
            and self.item_type == "growth"
            and self.height_cm is None
            and self.weight_kg is None
            and self.head_cm is None
        ):
            raise ValueError(
                "height_cm, weight_kg, or head_cm is required for growth create"
            )
        return self


class AgentLactationRecordApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    payload: AgentLactationRecordApplyPayload


class AgentLactationRecordApplyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal[
        "feeding_record",
        "pumping_record",
        "growth_record",
    ]
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    application_events: list[dict[str, Any]] = Field(default_factory=list)


class AgentMilkAnalysisSnapshot(MilkAnalysisReview):
    model_config = ConfigDict(extra="forbid")

    as_of_date: date
    timezone: str
    detail_level: Literal["detailed"]
    recent_feedings: list[MilkAnalysisFeedingRecord]
    recent_pumpings: list[MilkAnalysisPumpingRecord]
    pumping_rhythm: MilkAnalysisPumpingRhythm
    recent_growth: list[MilkAnalysisGrowthRecord]
    pumping_trends: list[MilkAnalysisTrendDay]
    analysis: MilkAnalysisInterpretation
