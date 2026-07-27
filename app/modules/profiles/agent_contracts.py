from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import DeliveryMethod, FeedingMode, SexAtBirth


MotherProfileField = Literal[
    "actual_delivery_date",
    "age",
    "current_delivery_method",
    "current_feeding_mode",
    "delivery_count",
    "estimated_due_date",
    "has_cesarean_history",
    "preferred_name",
]
InfantProfileField = Literal[
    "birth_date",
    "birth_weight_kg",
    "gestational_age_at_birth_days",
    "name",
    "sex_at_birth",
]


class AgentMotherProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_name: str | None = Field(default=None, min_length=1, max_length=120)
    age: int | None = Field(default=None, ge=12, le=70)
    estimated_due_date: date | None = None
    delivery_count: int | None = Field(default=None, ge=1, le=20)
    current_delivery_method: DeliveryMethod | None = None
    actual_delivery_date: date | None = None
    has_cesarean_history: bool | None = None
    current_feeding_mode: FeedingMode | None = None

    @model_validator(mode="after")
    def require_update(self) -> AgentMotherProfileUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one mother field is required")
        return self


class AgentInfantProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infant_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sex_at_birth: SexAtBirth | None = None
    birth_date: date | None = None
    birth_weight_kg: float | None = Field(default=None, ge=0.2, le=10)
    gestational_age_at_birth_days: int | None = Field(default=None, ge=140, le=315)

    @model_validator(mode="after")
    def require_update(self) -> AgentInfantProfileUpdate:
        if self.model_fields_set == {"infant_id"}:
            raise ValueError("at least one infant field is required")
        return self


class AgentCurrentInfantLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infant_id: UUID
    birth_order: int = Field(ge=1, le=10)


class AgentProfileUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mother: AgentMotherProfileUpdate | None = None
    infants: list[AgentInfantProfileUpdate] | None = Field(default=None, min_length=1, max_length=10)
    current_infants: list[AgentCurrentInfantLink] | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def require_update(self) -> AgentProfileUpdatePayload:
        if not self.model_fields_set:
            raise ValueError("at least one profile update is required")
        if "mother" in self.model_fields_set and self.mother is None:
            raise ValueError("mother must be an object")
        if "infants" in self.model_fields_set and self.infants is None:
            raise ValueError("infants must be an array")
        if "current_infants" in self.model_fields_set and self.current_infants is None:
            raise ValueError("current_infants must be an array")
        return self


class AgentProfileUpdateApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    payload: AgentProfileUpdatePayload


class AgentProfileInfantUpdateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infant_id: UUID
    fields: list[InfantProfileField]


class AgentProfileUpdateDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mother_fields: list[MotherProfileField] = Field(default_factory=list)
    infants: list[AgentProfileInfantUpdateSummary] = Field(default_factory=list)
    current_infants_updated: bool = False


class AgentBusinessActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["applied"]
    action_id: UUID
    resource_type: Literal["profile"]
    resource_id: UUID
    details: AgentProfileUpdateDetails
    application_events: list[dict[str, Any]] = Field(default_factory=list)
