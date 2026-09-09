from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from ..appointments.schemas import AppointmentRead
from ..care.schemas import CareEpisodeRead, CareRead
from ..consultations.room_schemas import ConsultationRead


class NoteContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subjective: str = Field(default="", max_length=8000)
    objective: str = Field(default="", max_length=8000)
    assessment: str = Field(default="", max_length=8000)
    plan: str = Field(default="", max_length=8000)


class NoteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    content: NoteContent


class NoteVersionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    expected_revision: int = Field(ge=1)


class NoteAmendWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class NoteRevisionRead(CareRead):
    id: UUID
    consultation_id: UUID
    author_id: UUID
    revises_id: UUID | None
    amendment_reason: str
    revision: int
    version: int
    status: Literal["draft", "signed"]
    signed_at: AwareDatetime | None
    updated_at: AwareDatetime


class NoteRead(NoteRevisionRead):
    content: NoteContent


class PlanTaskContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    title: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=64)
    due_label: str = Field(default="", max_length=80)
    scheduled_date: date | None = None


class PlanContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=3000)
    goals: list[str] = Field(default_factory=list, max_length=6)
    tasks: list[PlanTaskContent] = Field(default_factory=list, max_length=12)

    @field_validator("goals")
    @classmethod
    def goals_within_limit(cls, values: list[str]) -> list[str]:
        if any(len(value.strip()) > 200 for value in values):
            raise ValueError("Each goal may contain at most 200 characters.")
        return [value.strip() for value in values]

    @field_validator("tasks")
    @classmethod
    def unique_tasks(cls, values: list[PlanTaskContent]) -> list[PlanTaskContent]:
        if len({value.source_key for value in values}) != len(values):
            raise ValueError("Task source keys must be unique.")
        return values

    def ready_to_publish(self) -> bool:
        return bool(self.title and self.summary and self.goals and all(self.goals) and self.tasks
            and all(task.title and task.description and task.category and task.due_label for task in self.tasks))


class PlanWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    content: PlanContent


class PlanDraftRead(CareRead):
    id: UUID
    consultation_id: UUID
    version: int
    published_revision: int
    content: PlanContent
    updated_at: AwareDatetime


TaskStatus = Literal["pending", "in_progress", "completed", "skipped"]


class PublishedTaskRead(PlanTaskContent):
    status: TaskStatus
    progress_version: int


class PlanPublicationRead(BaseModel):
    id: UUID
    plan_id: UUID
    consultation_id: UUID
    revision: int
    title: str
    summary: str
    goals: list[str]
    tasks: list[PublishedTaskRead]
    publisher_name: str
    published_at: AwareDatetime


class TaskProgressWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    status: TaskStatus


class DocumentationRead(BaseModel):
    appointment: AppointmentRead
    consultation: ConsultationRead | None
    note: NoteRead | None
    note_history: list[NoteRevisionRead]
    plan: PlanDraftRead | None
    publication: PlanPublicationRead | None


class PatientPlanRead(BaseModel):
    episode: CareEpisodeRead
    appointment: AppointmentRead
    consultation: ConsultationRead | None
    publication: PlanPublicationRead | None
