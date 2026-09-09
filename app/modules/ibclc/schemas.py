from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from ..appointments.schemas import AppointmentRead
from ..care.schemas import CareEpisodeRead, CareProviderRead, ServicePackageRead
from ..care.event_models import CareEventKind
from ..consultations.room_schemas import ConsultationRead
from ..reports.schemas import ReportPurpose, ReportStatus


class WorkbenchIdentityRead(BaseModel):
    provider: CareProviderRead
    email: str
    mfa_expires_at: AwareDatetime
    server_time: AwareDatetime


class WorkbenchReminderRead(BaseModel):
    id: UUID
    kind: CareEventKind
    episode_id: UUID
    appointment_id: UUID | None
    patient_ref: UUID
    patient_name: str | None
    package_name: str
    starts_at: AwareDatetime | None
    timezone: str | None
    occurred_at: AwareDatetime
    read_at: AwareDatetime | None
    report_date: date | None


class WorkbenchRemindersRead(BaseModel):
    items: list[WorkbenchReminderRead]
    total: int
    unread_count: int
    offset: int
    limit: int
    server_time: AwareDatetime


class WorkbenchAppointmentRead(BaseModel):
    appointment: AppointmentRead
    episode: CareEpisodeRead
    package: ServicePackageRead
    patient_ref: UUID
    patient_name: str | None
    delivery_date: date | None
    case_consent: bool
    consultation: ConsultationRead | None
    note_status: Literal["draft", "signed"] | None
    published_revision: int


class WorkbenchAppointmentsRead(BaseModel):
    date: date
    timezone: str
    items: list[WorkbenchAppointmentRead]
    total: int
    offset: int
    limit: int
    server_time: AwareDatetime


class WorkbenchCalendarRead(BaseModel):
    week_start: date
    timezone: str
    items: list[WorkbenchAppointmentRead]
    total: int
    offset: int
    limit: int
    server_time: AwareDatetime


class ClientServiceRead(BaseModel):
    episode: CareEpisodeRead
    package: ServicePackageRead
    case_consent: bool


class WorkbenchClientRead(BaseModel):
    patient_ref: UUID
    name: str | None
    delivery_date: date | None
    services: list[ClientServiceRead]


class WorkbenchClientsRead(BaseModel):
    items: list[WorkbenchClientRead]
    total: int
    offset: int
    limit: int
    server_time: AwareDatetime


class WorkbenchClientDetailRead(BaseModel):
    client: WorkbenchClientRead
    appointments: list[WorkbenchAppointmentRead]
    appointment_total: int
    offset: int
    limit: int
    server_time: AwareDatetime


class FollowupServiceRead(BaseModel):
    service: ClientServiceRead
    ai_consent: bool
    purpose: ReportPurpose
    report_id: UUID | None
    report_status: ReportStatus | Literal['not_generated', 'case_consent_required', 'ai_consent_required']
    review_decision: Literal['confirmed', 'feedback'] | None


class FollowupClientRead(BaseModel):
    patient_ref: UUID
    name: str | None
    services: list[FollowupServiceRead]
    status: Literal['pending', 'completed']


class WorkbenchFollowupsRead(BaseModel):
    date: date
    timezone: str
    items: list[FollowupClientRead]
    total: int
    all_count: int
    pending_count: int
    completed_count: int
    offset: int
    limit: int
    server_time: AwareDatetime
