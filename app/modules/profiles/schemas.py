from __future__ import annotations


from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DeliveryMethod = Literal[
    "vaginal",
    "cesarean",
    "assisted_vaginal",
    "other",
    "unknown",
]
FeedingMode = Literal[
    "exclusive_breastfeeding",
    "expressed_milk_feeding",
    "mixed_feeding",
    "formula_feeding",
    "unknown",
]


class UserProfileRead(BaseModel):
    preferred_name: str | None = None
    age: int | None = None

    model_config = ConfigDict(from_attributes=True)


class UserProfileUpdate(BaseModel):
    preferred_name: str | None = Field(default=None, max_length=120)
    age: int | None = Field(default=None, ge=12, le=70)

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








class MaternalLactationInfantLink(BaseModel):
    infant_id: UUID
    birth_order: int = Field(ge=1, le=10)

    model_config = ConfigDict(extra="forbid")


class MaternalLactationProfileRead(BaseModel):
    current_infants: list[MaternalLactationInfantLink] = Field(
        default_factory=list,
        max_length=10,
    )
    delivery_count: int | None = None
    current_delivery_method: DeliveryMethod | None = None
    actual_delivery_date: date | None = None
    has_cesarean_history: bool | None = None
    current_feeding_mode: FeedingMode | None = None

    model_config = ConfigDict(from_attributes=True)


class MaternalLactationProfileUpdate(BaseModel):
    current_infants: list[MaternalLactationInfantLink] = Field(
        default_factory=list,
        max_length=10,
    )
    delivery_count: int | None = Field(default=None, ge=1, le=20)
    current_delivery_method: DeliveryMethod | None = None
    actual_delivery_date: date | None = None
    has_cesarean_history: bool | None = None
    current_feeding_mode: FeedingMode | None = None

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"minProperties": 1},
    )

    @field_validator("actual_delivery_date")
    @classmethod
    def reject_future_delivery_date(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("actual_delivery_date must not be in the future")
        return value

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> MaternalLactationProfileUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one maternal lactation field is required")
        return self
