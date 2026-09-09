from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    occurred_at: AwareDatetime
    side: Literal["left", "right"]
    feeling: Literal["comfortable", "full", "painful", "uncertain"] | None = None
    note: str = Field(default="", max_length=2000)

    @field_validator("occurred_at")
    @classmethod
    def reject_future_time(cls, value: datetime) -> datetime:
        if value > datetime.now(timezone.utc):
            raise ValueError("A record cannot occur in the future.")
        return value


class PumpObservation(Observation):
    method: Literal["pump"]
    volume_ml: float | None = Field(default=None, ge=0, le=2000, allow_inf_nan=False)


class NursingObservation(Observation):
    method: Literal["nurse"]
    duration_minutes: int | None = Field(default=None, ge=0, le=240)


LactationObservation = Annotated[PumpObservation | NursingObservation, Field(discriminator="method")]


class LactationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation: LactationObservation


class LactationUpdate(LactationWrite):
    expected_version: int = Field(ge=1)


class LactationRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    version: int
    observation: LactationObservation
    model_config = ConfigDict(from_attributes=True)


class LactationList(BaseModel):
    items: list[LactationRead]


class RecordDeletion(BaseModel):
    id: UUID
    version: int


class RecordVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
