from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserProfileRead(BaseModel):
    user_id: UUID
    display_name: str = ""
    age: int | None = None
    delivery_date: date | None = None
    lactation_advice: str = ""
    feeding_advice: str = ""
    profile_onboarding_complete: bool = False
    profile_onboarding_skipped: bool = False

    model_config = ConfigDict(from_attributes=True)


class UserProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    age: int | None = Field(default=None, ge=12, le=70)
    delivery_date: date | None = None
    lactation_advice: str | None = None
    feeding_advice: str | None = None
    profile_onboarding_skipped_at: datetime | None = None
    profile_onboarding_completed_at: datetime | None = None


class InfantProfileRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    infant_name: str
    sex: str
    birth_date: date | None = None
    status: str

    model_config = ConfigDict(from_attributes=True)


class InfantProfileCreate(BaseModel):
    infant_name: str = Field(min_length=1, max_length=120)
    sex: str | None = Field(default=None, max_length=32)
    birth_date: date | None = None


class InfantProfileListResponse(BaseModel):
    items: list[InfantProfileRead]
