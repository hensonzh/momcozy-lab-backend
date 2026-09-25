from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .schemas import UserProfileUpdate

Metric = Literal["feed", "energy", "sleep", "mood", "pump", "pain", "latch", "bottle", "diaper", "weight", "storage"]
Issue = Literal["comfort", "feeding", "intake", "supply", "work", "other"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class MeProfilePatch(UserProfileUpdate):
    actual_delivery_date: date | None = None
    delivery_count: int | None = Field(None, ge=1, le=20)
    baby_count: int | None = Field(None, ge=1, le=3)
    current_delivery_method: Literal["vaginal", "cesarean"] | None = None
    gestation_weeks: int | None = Field(None, ge=20, le=45)
    gestation_days: int | None = Field(None, ge=0, le=6)
    feeding_methods: list[Literal["direct", "expressed", "formula"]] | None = Field(None, max_length=3)
    feeding_preference: Literal["breast", "formula", "mixed", "undecided"] | None = None
    caregivers: list[Literal["partner", "family", "professional", "self"]] | None = Field(None, max_length=4)
    return_to_work_date: date | None = None
    additional_context: str | None = Field(None, max_length=500)

    @field_validator("actual_delivery_date")
    @classmethod
    def past_delivery(cls, value: date | None) -> date | None:
        if value and value > date.today():
            raise ValueError("delivery date must not be in the future")
        return value


class Concern(StrictModel):
    id: UUID
    issues: list[Issue] = Field(min_length=1, max_length=6)
    note: str = Field("", max_length=200)
    reminder: bool = True
    ended: bool = False

    @field_validator("issues")
    @classmethod
    def unique_issues(cls, value: list[Issue]) -> list[Issue]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate issue")
        return value


class RecordOrder(StrictModel):
    order: list[Metric] = Field(min_length=4, max_length=11)

    @field_validator("order")
    @classmethod
    def unique_order(cls, value: list[Metric]) -> list[Metric]:
        if len(set(value)) != len(value) or not {"feed", "energy", "sleep", "mood"}.issubset(value):
            raise ValueError("order must contain each daily metric exactly once")
        return value


class ObservationFields(StrictModel):
    feeding_record_id: UUID | None = None
    canonical_record_id: str | None = None
    side: Literal["左侧", "右侧", "两侧", "Left side", "Right side", "Both sides"] | None = None
    phase: Literal["刚开始含奶时", "喂奶过程中", "喂奶后", "泵奶时", "When latching", "During feeding", "After feeding", "While pumping"] | None = None
    impact: Literal["可以继续喂", "需要暂停", "无法继续", "Could continue", "Needed a break", "Could not continue"] | None = None
    carer: str | None = Field(default=None, max_length=80)
    swallow: Literal["有", "没有", "不确定", "Yes", "No", "Not sure"] | None = None
    pain: int | None = Field(default=None, ge=0, le=10)
    note: str = Field(default="", max_length=500)
    action: Literal["添加一袋", "取用一袋", "Add a bag", "Use a bag"] | None = None
    volume_ml: float | None = Field(default=None, gt=0, le=3000, allow_inf_nan=False)
    duration_minutes: int | None = Field(default=None, ge=1, le=240)


class Observation(StrictModel):
    id: UUID
    kind: Literal["energy", "sleep", "mood", "pain", "latch", "storage", "pump", "bottle"]
    occurred_at: datetime
    value: str = Field(min_length=1, max_length=120)
    fields: ObservationFields = Field(default_factory=lambda: ObservationFields())

    @field_validator("occurred_at")
    @classmethod
    def valid_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value > datetime.now(timezone.utc):
            raise ValueError("a past timezone-aware time is required")
        return value

    @model_validator(mode="after")
    def value_matches_kind(self) -> "Observation":
        options = {
            "energy": ["有力气", "还撑得住", "很疲惫", "Energized", "Managing", "Exhausted"],
            "sleep": ["少于 3 小时", "3–4 小时", "4–5 小时", "5–6 小时", "6 小时以上", "不确定", "Less than 3 hours", "3–4 hours", "4–5 hours", "5–6 hours", "Over 6 hours", "Not sure"],
            "mood": ["不太好", "一般", "不错", "Having a hard day", "Okay", "Good"],
            "latch": ["含得稳", "容易松开", "含不住", "Stayed latched", "Came off easily", "Could not latch"],
            "bottle": ["愿意吃", "愿意吃一些", "不太愿意", "不愿意吃", "Fed willingly", "Took some", "Reluctant", "Refused"],
        }
        if self.kind in options and self.value not in options[self.kind]:
            raise ValueError("invalid observation value")
        if self.kind == "pain" and (
            self.fields.pain is None or self.fields.side is None or self.fields.phase is None or self.fields.impact is None
        ):
            raise ValueError("pain fields are required")
        if self.kind == "pump" and (self.fields.volume_ml is None or self.fields.side is None):
            raise ValueError("pump volume and side are required")
        if self.kind == "storage" and (self.fields.volume_ml is None or self.fields.action is None):
            raise ValueError("storage volume and action are required")
        return self
