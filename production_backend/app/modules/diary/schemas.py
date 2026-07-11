from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PregnancyDiaryEntryRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    entry_date: date
    gestational_week: str
    mood: str
    energy_level: str
    sleep_summary: str
    fetal_movement: str
    symptom_tags: list[Any]
    appointment_note: str
    nutrition_note: str
    content: str
    attachments: list[Any]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PregnancyDiaryEntryValues(BaseModel):
    gestational_week: str | None = Field(default=None, max_length=32)
    mood: str | None = Field(default=None, max_length=64)
    energy_level: str | None = Field(default=None, max_length=64)
    sleep_summary: str | None = None
    fetal_movement: str | None = None
    symptom_tags: list[Any] | None = None
    appointment_note: str | None = None
    nutrition_note: str | None = None
    content: str | None = None
    attachments: list[Any] | None = None


class PregnancyDiaryEntryCreate(PregnancyDiaryEntryValues):
    entry_date: date


class PregnancyDiaryEntryUpdate(PregnancyDiaryEntryValues):
    pass


class PregnancyDiaryEntryListResponse(BaseModel):
    items: list[PregnancyDiaryEntryRead]
