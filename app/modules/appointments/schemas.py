from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..care.schemas import CareEpisodeRead, CareProviderRead, CareRead


class BookingPrecheckWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    region: str = Field(pattern=r"^[A-Z]{2}$")
    service_suitable: bool
    emergency_status: Literal["clear", "needs_help"]


class BookingEligibilityRead(CareRead):
    id: UUID
    episode_id: UUID
    region: str
    service_suitable: bool
    emergency_status: Literal["clear", "needs_help"]
    eligible: bool
    reason: Literal["", "emergency_help", "service_unsuitable", "region_unavailable"]
    expires_at: AwareDatetime


class HoldWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligibility_id: UUID
    provider_id: UUID
    starts_at: AwareDatetime


class VersionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class AppointmentRead(CareRead):
    id: UUID
    episode_id: UUID
    provider_id: UUID
    provider_name: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    timezone: str
    region: str
    status: Literal["held", "confirmed", "in_progress", "completed", "cancelled", "expired"]
    hold_expires_at: AwareDatetime
    version: int
    intake_version: int
    confirmed_at: AwareDatetime | None
    cancelled_at: AwareDatetime | None


class BookingContextRead(BaseModel):
    episode: CareEpisodeRead
    eligibility: BookingEligibilityRead | None
    providers: list[CareProviderRead]
    appointments: list[AppointmentRead]
    server_time: AwareDatetime


class AvailabilitySlot(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    available: bool


class AvailabilityRead(BaseModel):
    provider_id: UUID
    date: date
    timezone: str
    slots: list[AvailabilitySlot]
    server_time: AwareDatetime
