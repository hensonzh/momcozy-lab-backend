from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from ..appointments.schemas import AppointmentRead
from ..care.schemas import CareRead
from ..baby.profile_schemas import BabyProfileRead

CONSENT_POLICY_VERSION = "2026-09-08"
IntakeSymptom = Literal["latch_difficulty", "feeding_pain", "supply_concern", "frequent_waking", "pumping_schedule", "other"]
FeedingMode = Literal["exclusive_breastfeeding", "expressed_milk_feeding", "mixed_feeding", "formula_feeding"]
ConsentScope = Literal["ibclc_case", "video", "ai_context", "notifications"]


class IntakeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    baby_id: UUID
    baby_name: str = Field(min_length=1, max_length=120)
    baby_birth_date: date
    baby_sex: Literal["female", "male"]
    feeding_mode: FeedingMode
    delivery_date: date
    region: str = Field(pattern=r"^[A-Z]{2}$")


class IntakeContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    symptoms: list[IntakeSymptom] = Field(min_length=1, max_length=6)
    feeding_goal: str = Field(min_length=1, max_length=1000)
    support_needed: str = Field(default="", max_length=3000)
    profile: IntakeProfile

    @field_validator("symptoms")
    @classmethod
    def distinct_symptoms(cls, values: list[IntakeSymptom]) -> list[IntakeSymptom]:
        if len(set(values)) != len(values):
            raise ValueError("Select each concern once.")
        return sorted(values)


class IntakeWrite(IntakeContent):
    expected_version: int = Field(ge=0)
    expected_consent_version: int = Field(ge=0)
    consent_to_share: Literal[True]
    consent_policy_version: Literal["2026-09-08"]

    def content(self) -> IntakeContent:
        return IntakeContent.model_validate(self.model_dump(include=set(IntakeContent.model_fields)))


class IntakeRead(CareRead):
    id: UUID
    appointment_id: UUID
    episode_id: UUID
    version: int
    symptoms: list[IntakeSymptom]
    feeding_goal: str
    support_needed: str
    profile: IntakeProfile
    submitted_at: AwareDatetime


class ConsentRead(CareRead):
    id: UUID
    episode_id: UUID
    scope: ConsentScope
    version: int
    active: bool
    policy_version: str
    recorded_at: AwareDatetime


class ConsentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    scope: ConsentScope
    active: bool
    policy_version: Literal["2026-09-08"]


class ConsentListRead(BaseModel):
    items: list[ConsentRead]




class IntakeContextRead(BaseModel):
    appointment: AppointmentRead
    intake: IntakeRead | None
    previous_intake: IntakeRead | None
    consents: list[ConsentRead]
    babies: list[BabyProfileRead]
    delivery_date: date | None
    consent_policy_version: str = CONSENT_POLICY_VERSION
