"""Bounded, stateless analysis input and the currently approved nonmedical event."""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BabyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    baby_context_snapshot_id: UUID
    profile_version: int = Field(ge=1)
    birth_date: date | None
    feeding_mode: str = Field(min_length=1, max_length=32)
    captured_at: datetime


class PhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    photo_id: UUID
    position: int = Field(ge=0, le=2)
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    jpeg_b64: str = Field(max_length=3_000_000)

    @model_validator(mode="after")
    def validate_jpeg(self) -> PhotoInput:
        try:
            body = base64.b64decode(self.jpeg_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid JPEG encoding") from exc
        if not body.startswith(b"\xff\xd8\xff") or len(body) > 2_000_000:
            raise ValueError("Invalid or oversized derived JPEG")
        return self


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["stool.analyze.synthetic.v1"]
    analysis_id: UUID
    market: Literal["CN", "US"]
    language: Literal["zh", "en"]
    baby_context: BabyContext
    photos: tuple[PhotoInput, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def verify_photos(self) -> AnalysisInput:
        if (len({photo.photo_id for photo in self.photos}) != len(self.photos)
                or [photo.position for photo in self.photos] != list(range(len(self.photos)))):
            raise ValueError("Photos must be distinct and ordered from zero")
        if self.baby_context.captured_at.tzinfo is None:
            raise ValueError("Context timestamp must be timezone aware")
        return self


class AnalysisEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["stool.events.v1"] = "stool.events.v1"
    analysis_id: UUID
    sequence: Literal[2, 3]
    event_type: Literal["stool.analysis.started", "stool.analysis.failed"]

    @model_validator(mode="after")
    def sequence_matches_type(self) -> AnalysisEvent:
        if (self.sequence, self.event_type) not in (
            (2, "stool.analysis.started"), (3, "stool.analysis.failed"),
        ):
            raise ValueError("Invalid synthetic event order")
        return self
