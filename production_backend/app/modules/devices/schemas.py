from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PumpDeviceRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    device_id: str
    model: str
    firmware_version: str
    status: str
    last_seen_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PumpDeviceUpsert(BaseModel):
    model: str | None = Field(default=None, max_length=120)
    firmware_version: str | None = Field(default=None, max_length=120)
    last_seen_at: datetime | None = None


class PumpDeviceListResponse(BaseModel):
    items: list[PumpDeviceRead]


class PumpTelemetryEventRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    device_id: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class PumpTelemetryEventCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=120)
    event_type: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class PumpTelemetryEventListResponse(BaseModel):
    items: list[PumpTelemetryEventRead]
