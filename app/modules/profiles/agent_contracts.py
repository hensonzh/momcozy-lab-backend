from __future__ import annotations


from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import DeliveryMethod, FeedingMode
from ..baby.profile_schemas import BabySex, BabyFeedingMode


MotherProfileField = Literal[
    "actual_delivery_date",
    "age",
    "current_delivery_method",
    "current_feeding_mode",
    "delivery_count",
    "has_cesarean_history",
    "preferred_name",
]
BabyProfileField = Literal[
    "birth_date",
    "name",
    "sex",
    "feeding_mode",
]
AgentProfileActionType = Literal[
    "profile.update",
    "profile.current_infants.replace",
]


class AgentMotherProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_name: str | None = Field(default=None, min_length=1, max_length=120)
    age: int | None = Field(default=None, ge=12, le=70)
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


class AgentBabyProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infant_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sex: BabySex | None = None
    feeding_mode: BabyFeedingMode | None = None
    birth_date: date | None = None

    @model_validator(mode="after")
    def require_update(self) -> AgentBabyProfileUpdate:
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
    infants: list[AgentBabyProfileUpdate] | None = Field(default=None, min_length=1, max_length=10)
    current_infants: list[AgentCurrentInfantLink] | None = Field(default=None, max_length=10)
    expected_current_infants: list[AgentCurrentInfantLink] | None = Field(
        default=None,
        max_length=10,
    )
    reference_date: date

    @model_validator(mode="after")
    def require_update(self) -> AgentProfileUpdatePayload:
        if not self.model_fields_set.intersection(
            {"mother", "infants", "current_infants"}
        ):
            raise ValueError("at least one profile update is required")
        if "mother" in self.model_fields_set and self.mother is None:
            raise ValueError("mother must be an object")
        if "infants" in self.model_fields_set and self.infants is None:
            raise ValueError("infants must be an array")
        if "current_infants" in self.model_fields_set and self.current_infants is None:
            raise ValueError("current_infants must be an array")
        if (
            "expected_current_infants" in self.model_fields_set
            and self.expected_current_infants is None
        ):
            raise ValueError("expected_current_infants must be an array")
        return self


class AgentProfileUpdateApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_user_id: UUID
    action_id: UUID
    run_id: UUID
    action_type: AgentProfileActionType
    payload: AgentProfileUpdatePayload

    @model_validator(mode="after")
    def validate_action_payload(self) -> AgentProfileUpdateApply:
        supplied = self.payload.model_fields_set
        if self.action_type == "profile.update":
            if supplied.intersection(
                {"current_infants", "expected_current_infants"}
            ):
                raise ValueError(
                    "profile.update cannot replace current infant relationships"
                )
            if not supplied.intersection({"mother", "infants"}):
                raise ValueError("profile.update requires mother or infants")
            return self
        if supplied.intersection({"mother", "infants"}):
            raise ValueError(
                "profile.current_infants.replace only accepts relationship fields"
            )
        if not {
            "current_infants",
            "expected_current_infants",
        }.issubset(supplied):
            raise ValueError(
                "profile.current_infants.replace requires current and expected relationships"
            )
        return self


class AgentProfileInfantUpdateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infant_id: UUID
    fields: list[BabyProfileField]


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
