from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    delivery_count: int = Field(ge=1, le=20, description="Number of deliveries including this birth.")
    has_cesarean_history: bool | None = Field(
        default=None,
        description="Cesarean before this birth, not the current delivery method.",
    )
    delivery_type: Literal["vaginal", "cesarean", "assisted", "other"] | None = None
    infant_count: int = Field(ge=1, le=6)
    infants: list[OnboardingInfantInput] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_birth(self) -> OnboardingProfileInput:
        if self.delivery_date > date.today():
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
