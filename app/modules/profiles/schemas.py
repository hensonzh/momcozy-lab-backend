from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SexAtBirth = Literal["female", "male", "intersex", "unknown", "undisclosed"]


class UserProfileRead(BaseModel):
    preferred_name: str | None = None
    age: int | None = None
    estimated_due_date: date | None = None

    model_config = ConfigDict(from_attributes=True)


class UserProfileUpdate(BaseModel):
    preferred_name: str | None = Field(default=None, max_length=120)
    age: int | None = Field(default=None, ge=12, le=70)
    estimated_due_date: date | None = None

    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 1})

    @field_validator("preferred_name")
    @classmethod
    def normalize_preferred_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("preferred_name must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> UserProfileUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one profile field is required")
        return self


class InfantProfileRead(BaseModel):
    id: UUID
    name: str
    sex_at_birth: SexAtBirth | None = None
    birth_date: date | None = None

    model_config = ConfigDict(from_attributes=True)


class InfantProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sex_at_birth: SexAtBirth | None = None
    birth_date: date | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("birth_date")
    @classmethod
    def reject_future_birth_date(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("birth_date must not be in the future")
        return value


class InfantProfileListResponse(BaseModel):
    items: list[InfantProfileRead]
