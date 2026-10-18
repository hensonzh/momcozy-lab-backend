from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..profiles.feeding_methods import FeedingMethod, validate_feeding_methods


class OnboardingInfantInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    nickname: str = Field(default="", max_length=120)
    sex: Literal["female", "male"] | None = None


class OnboardingProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    stage: Literal["postpartum"]
    display_name: str = Field(min_length=1, max_length=120)
    age: int = Field(ge=12, le=70)
    delivery_date: date
    client_timezone_offset_minutes: int | None = Field(
        default=None, ge=-720, le=840, exclude=True,
        description="Device UTC offset in minutes, used only to validate its local delivery date.",
    )
    delivery_count: int = Field(ge=1, le=20, description="Number of deliveries including this birth.")
    has_cesarean_history: bool | None = Field(
        default=None,
        description="Cesarean before this birth, not the current delivery method.",
    )
    delivery_type: Literal["vaginal", "cesarean", "assisted", "other"] | None = None
    gestation_weeks: int = Field(ge=20, le=45)
    gestation_days: int = Field(ge=0, le=6)
    feeding_methods: list[FeedingMethod] = Field(min_length=1, max_length=3)

    @field_validator("feeding_methods")
    @classmethod
    def validate_feeding_methods(cls, methods: list[FeedingMethod]) -> list[FeedingMethod]:
        return validate_feeding_methods(methods)
    infant_count: int = Field(ge=1, le=6)
    infants: list[OnboardingInfantInput] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_birth(self) -> OnboardingProfileInput:
        today = (
            datetime.now(timezone(timedelta(minutes=self.client_timezone_offset_minutes))).date()
            if self.client_timezone_offset_minutes is not None else date.today()
        )
        if self.delivery_date > today:
            raise ValueError("delivery_date must not be in the future")
        if self.delivery_count == 1 and self.has_cesarean_history is True:
            raise ValueError("A first delivery cannot have a prior cesarean history")
        if self.infant_count != len(self.infants):
            raise ValueError("infant_count must match the infants list")
        return self


class OnboardingStateOutput(BaseModel):
    status: Literal["required", "completed"]
    profile_confirmed: bool
    primary_infant_id: UUID | None = None


FamilyRole = Literal[
    "mother", "father", "paternal-grandparent", "maternal-grandparent", "other-family"
]
ConcernKey = Literal[
    "milk-supply", "feeding-enough", "crying", "stool", "baby-sleep",
    "growth", "recovery", "mood", "fatigue", "support",
]


class ChatFirstOnboardingInput(BaseModel):
    """Only facts actually collected by the conversation-first flow."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: FamilyRole
    stage: Literal["postpartum"] | None = None
    age: int | None = Field(default=None, ge=18, le=45)
    delivery_count: int | None = Field(default=None, ge=1, le=6)
    delivery_date: date | None = None
    gestation_weeks: int | None = Field(default=None, ge=24, le=42)
    gestation_days: int | None = Field(default=None, ge=0, le=6)
    baby_count: int | None = Field(default=None, ge=1, le=4)
    concerns: list[ConcernKey] = Field(default_factory=list, max_length=10)
    client_timezone_offset_minutes: int | None = Field(default=None, ge=-720, le=840, exclude=True)

    @model_validator(mode="after")
    def validate_collected_facts(self) -> ChatFirstOnboardingInput:
        mother_fields = (
            "stage", "age", "delivery_count", "delivery_date",
            "gestation_weeks", "gestation_days", "baby_count",
        )
        if self.role != "mother":
            if any(field in self.model_fields_set for field in mother_fields) or self.concerns:
                raise ValueError("Maternal facts and concerns require the mother role")
            return self
        if any(getattr(self, field) is None for field in mother_fields):
            raise ValueError("Complete all collected postpartum fields")
        if len(set(self.concerns)) != len(self.concerns):
            raise ValueError("Duplicate concern")
        if self.gestation_weeks is not None and self.gestation_weeks >= 37 and self.gestation_days != 0:
            raise ValueError("Gestation days are only collected below 37 weeks")
        today = (
            datetime.now(timezone(timedelta(minutes=self.client_timezone_offset_minutes))).date()
            if self.client_timezone_offset_minutes is not None else date.today()
        )
        if self.delivery_date is not None and not date(today.year - 2, 1, 1) <= self.delivery_date <= today:
            raise ValueError("Delivery date is outside the supported range")
        return self


class ChatFirstStateOutput(BaseModel):
    status: Literal["required", "completed"]
    can_enter_app: bool
    role: FamilyRole | None = None
    profile_scope: Literal["none", "chat_first", "legacy_full"]
    primary_infant_id: UUID | None = None
