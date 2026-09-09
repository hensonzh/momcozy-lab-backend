from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..appointments.schemas import AppointmentRead
from ..care.schemas import CareRead
from .schemas import CONSENT_POLICY_VERSION


ParticipantRole = Literal["mom", "ibclc"]
VideoProviderName = Literal["disabled", "sandbox", "livekit"]
Presence = Literal["not_joined", "joining", "joined", "reconnecting", "left"]
EndReason = Literal["completed", "technical_failure", "user_no_show", "safety_escalation"]


class LocationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region: str = Field(pattern=r"^[A-Z]{2}$")


class LocationRead(CareRead):
    id: UUID
    region: str
    decision: Literal["passed", "blocked"]
    expires_at: AwareDatetime
    created_at: AwareDatetime


class ConsultationRead(CareRead):
    id: UUID
    appointment_id: UUID
    episode_id: UUID
    version: int
    status: Literal["waiting_room", "in_progress", "note_pending", "no_show", "failed", "cancelled", "closed"]
    room_status: Literal["creating", "ready", "closing", "closed", "failed"]
    video_provider: VideoProviderName
    started_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    end_reason: EndReason | None


class ParticipantRead(CareRead):
    role: ParticipantRole
    presence: Presence
    connection_version: int
    joined_at: AwareDatetime | None
    last_seen_at: AwareDatetime | None


class RoomContextRead(BaseModel):
    appointment: AppointmentRead
    viewer_role: ParticipantRole
    consultation: ConsultationRead | None
    participants: list[ParticipantRead]
    intake_ready: bool
    case_consent: bool
    video_consent: bool
    location: LocationRead | None
    opens_at: AwareDatetime
    closes_at: AwareDatetime
    server_time: AwareDatetime
    demo_early_join: bool
    video_provider: VideoProviderName
    consent_policy_version: str = CONSENT_POLICY_VERSION


class RoomCredentials(BaseModel):
    server_url: str | None
    token: str | None = Field(repr=False)
    expires_at: AwareDatetime


class JoinRead(BaseModel):
    context: RoomContextRead
    connection_id: UUID
    credentials: RoomCredentials | None


class PresenceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    presence: Literal["joined", "reconnecting", "left"]


class EndWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    reason: EndReason
