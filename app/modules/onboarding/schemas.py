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
