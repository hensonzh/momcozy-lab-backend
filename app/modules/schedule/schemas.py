from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, field_validator



class PersonalScheduleWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=40)
    date: date
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    note: str = Field(default="", max_length=120)

    @field_validator("title", "note")
    @classmethod
    def no_blank_strings(cls, value: str, info: ValidationInfo) -> str:
        if info.field_name == "title" and not value:
            raise ValueError("title must not be blank")
        return value


class PersonalScheduleUpdate(PersonalScheduleWrite):
    expected_updated_at: AwareDatetime


class PersonalScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    date: date
    start_time: str
    note: str
    updated_at: AwareDatetime


class SchedulePageRead(BaseModel):
    personal: list[PersonalScheduleRead]
    server_time: AwareDatetime
    has_more: bool
