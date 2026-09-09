from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiaryValue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MotherRest(DiaryValue):
    total: Literal["under-3h", "3-4h", "4-5h", "5-6h", "6h-plus", "unknown"] | None = None
    interruptions: Literal["none", "1-2", "3-4", "5-plus", "unknown"] | None = None
    longest_stretch: Literal["under-1h", "1-2h", "2-3h", "3h-plus", "unknown"] | None = None
    recovery: Literal["restored", "managing", "exhausted"] | None = None
    day_rest: Literal["none", "under-30m", "30-60m", "60m-plus"] | None = None
    resleep_difficulty: Literal["easy", "somewhat-hard", "hard"] | None = None
    disruptions: list[Literal["feeding", "baby", "discomfort", "cannot-sleep", "environment", "other"]] = Field(default_factory=list, max_length=6)


class MotherBody(DiaryValue):
    energy: Literal["energized", "managing", "depleted"] | None = None
    discomfort_sites: list[Literal["lower-abdomen", "perineum", "c-section", "back", "head-chest", "other"]] = Field(default_factory=list, max_length=6)
    severity: Literal["mild", "noticeable", "hard-to-ignore"] | None = None
    impact: Literal["none", "some", "care-limited"] | None = None
    trend: Literal["better", "same", "worse"] | None = None
    urination: Literal["normal", "leaking-urgency", "painful-difficult"] | None = None
    bowel: Literal["smooth", "difficult", "painful-piles"] | None = None
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_discomfort_site(self) -> MotherBody:
        if not self.discomfort_sites and (self.severity is not None or self.impact is not None):
            raise ValueError("Discomfort details require a recorded site.")
        return self


class MotherMood(DiaryValue):
    tone: Literal["steady", "tense", "low", "reactive", "unclear"] | None = None
    pressures: list[Literal["baby-worry", "feeding-pressure", "body-recovery", "sleep-loss", "family-friction", "self-doubt", "no-time", "unclear"]] = Field(default_factory=list, max_length=8)
    impact: Literal["none", "some", "hard"] | None = None
    support: Literal["supported", "carrying-most", "alone"] | None = None

    @model_validator(mode="after")
    def exclusive_unclear(self) -> MotherMood:
        if "unclear" in self.pressures and len(set(self.pressures)) > 1:
            raise ValueError("Unclear cannot be combined with other pressures.")
        return self


class MotherDiary(DiaryValue):
    rest: MotherRest = Field(default_factory=MotherRest)
    body: MotherBody = Field(default_factory=MotherBody)
    mood: MotherMood = Field(default_factory=MotherMood)

    @property
    def is_empty(self) -> bool:
        # Defaults describe absent self-reports, never a clinical assessment.
        return not self.model_dump(exclude_defaults=True)


class MotherDiaryWrite(DiaryValue):
    expected_version: int = Field(ge=0)
    diary: MotherDiary

    @model_validator(mode="after")
    def require_observation(self) -> MotherDiaryWrite:
        if self.diary.is_empty:
            raise ValueError("Record at least one observation.")
        return self


class MotherDiaryRead(DiaryValue):
    id: UUID
    owner_user_id: UUID
    entry_date: date
    diary: MotherDiary
    version: int
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class MotherDiaryList(DiaryValue):
    items: list[MotherDiaryRead]
