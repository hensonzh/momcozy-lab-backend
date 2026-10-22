"""Strict Product-to-specialty synthetic wire contract; no medical fields approved."""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


class SpecialtyBabyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    baby_context_snapshot_id: UUID
    profile_version: int = Field(ge=1)
    birth_date: date | None
    feeding_mode: str = Field(min_length=1, max_length=32)
    captured_at: datetime


class SpecialtyPhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    photo_id: UUID
    position: int = Field(ge=0, le=2)
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    jpeg_b64: str = Field(max_length=3_000_000)

    @model_validator(mode="after")
    def validate_jpeg(self) -> SpecialtyPhotoInput:
        try:
            image = base64.b64decode(self.jpeg_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid JPEG encoding") from exc
        if not image.startswith(b"\xff\xd8\xff") or len(image) > 2_000_000:
            raise ValueError("Invalid or oversized derived JPEG")
        return self


class SpecialtyAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["stool.analyze.synthetic.v1"] = "stool.analyze.synthetic.v1"
    analysis_id: UUID
    baby_context: SpecialtyBabyContext
    market: Literal["CN", "US"]
    language: Literal["zh", "en"]
    photos: tuple[SpecialtyPhotoInput, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def verify_photos(self) -> SpecialtyAnalysisInput:
        if (len({photo.photo_id for photo in self.photos}) != len(self.photos)
                or [photo.position for photo in self.photos] != list(range(len(self.photos)))):
            raise ValueError("Photos must be unique and ordered from zero")
        if self.baby_context.captured_at.tzinfo is None:
            raise ValueError("Context timestamp must be timezone aware")
        return self


class SpecialtyTransportEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["stool.events.v1"]
    analysis_id: UUID
    sequence: PositiveInt
    event_type: Literal["stool.card.created", "stool.analysis.started", "stool.analysis.failed", "stool.analysis.cancelled"]
