from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ...infrastructure.care_report_contract import ReportGenerationInput, ReportGenerationResult
from ...infrastructure.care_report_sources_contract import ReportDialogueSource

ReportPurpose = Literal['daily', 'preparation']
ReportStatus = Literal['waiting_for_record', 'queued', 'running', 'ready', 'failed', 'cancelled']


class ReportSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: ReportGenerationInput
    dialogues: list[ReportDialogueSource]


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    purpose: ReportPurpose = 'daily'
    report_date: date | None = None


class ReviewWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=0)
    decision: Literal['confirmed', 'feedback']
    feedback: str = Field(default='', max_length=3000)

    @model_validator(mode='after')
    def specific_feedback(self) -> ReviewWrite:
        if self.decision == 'feedback' and len(self.feedback) < 5:
            raise ValueError('Please provide specific feedback of at least five characters.')
        if self.decision == 'confirmed' and self.feedback:
            raise ValueError('Confirmation must not contain correction feedback.')
        return self


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    version: int
    decision: Literal['confirmed', 'feedback']
    feedback: str
    provider_id: UUID
    created_at: AwareDatetime


class ReportRead(BaseModel):
    id: UUID
    episode_id: UUID
    purpose: ReportPurpose
    report_date: date
    timezone: str
    version: int
    status: ReportStatus
    snapshot: ReportSnapshot | None
    result: ReportGenerationResult | None
    created_at: AwareDatetime
    generated_at: AwareDatetime | None
    error_code: str | None
    review: ReviewRead | None
    reviewable: bool


class ReportStateRead(BaseModel):
    report_date: date
    timezone: str
    purpose: ReportPurpose
    state: Literal['not_generated', 'waiting_for_record', 'queued', 'running', 'ready', 'failed', 'cancelled']
    report: ReportRead | None
    server_time: AwareDatetime


class ReportIndexItem(BaseModel):
    id: UUID
    report_date: date
    timezone: str
    version: int
    status: ReportStatus
    review_decision: Literal['confirmed', 'feedback'] | None
    generated_at: AwareDatetime | None


class ReportHistoryRead(BaseModel):
    items: list[ReportIndexItem]
    server_time: AwareDatetime
