from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FeedingRecordRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    infant_id: UUID | None = None
    feed_time: datetime
    feed_type: str
    feed_action: str
    volume_ml: float | None = None
    duration_seconds: int | None = None
    title: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class FeedingRecordCreate(BaseModel):
    infant_id: UUID | None = None
    feed_time: datetime
    feed_type: str = Field(min_length=1, max_length=32)
    feed_action: str | None = Field(default=None, max_length=32)
    volume_ml: float | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_quantity(self) -> "FeedingRecordCreate":
        if self.volume_ml is None and self.duration_seconds is None:
            raise ValueError("volume_ml or duration_seconds is required")
        return self


class FeedingRecordListResponse(BaseModel):
    items: list[FeedingRecordRead]


class PumpingRecordRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    pump_start_time: datetime
    pump_end_time: datetime | None = None
    milk_volume_ml: float | None = None
    pump_type: str
    duration_seconds: int | None = None
    source: str
    title: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class PumpingRecordCreate(BaseModel):
    pump_start_time: datetime
    pump_end_time: datetime | None = None
    milk_volume_ml: float | None = Field(default=None, ge=0)
    pump_type: str | None = Field(default=None, max_length=32)
    duration_seconds: int | None = Field(default=None, ge=0)
    source: str | None = Field(default="manual", max_length=32)
    title: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_quantity(self) -> "PumpingRecordCreate":
        if self.milk_volume_ml is None and self.duration_seconds is None:
            raise ValueError("milk_volume_ml or duration_seconds is required")
        return self


class PumpingRecordListResponse(BaseModel):
    items: list[PumpingRecordRead]
