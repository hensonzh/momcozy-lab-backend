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


class PumpWorkstateCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    state: dict[str, Any] = Field(default_factory=dict)
    source: str | None = Field(default="device", max_length=64)


class PumpWorkstateRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    device_id: str
    occurred_at: datetime
    source: str
    state: dict[str, Any]

    @classmethod
    def from_event(cls, event: PumpTelemetryEventRead) -> "PumpWorkstateRead":
        source = event.payload.get("source")
        state = event.payload.get("state")
        return cls(
            id=event.id,
            owner_user_id=event.owner_user_id,
            device_id=event.device_id,
            occurred_at=event.occurred_at,
            source=source if isinstance(source, str) and source else "device",
            state=state if isinstance(state, dict) else {},
        )


class PumpThresholdCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    stimulate_level_l: int = Field(gt=0)
    deep_level_l: int = Field(gt=0)
    stimulate_level_r: int = Field(gt=0)
    deep_level_r: int = Field(gt=0)
    source: str | None = Field(default="device", max_length=64)


class PumpThresholdRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    device_id: str
    occurred_at: datetime
    stimulate_level_l: int
    deep_level_l: int
    stimulate_level_r: int
    deep_level_r: int
    source: str

    @classmethod
    def from_event(cls, event: PumpTelemetryEventRead) -> "PumpThresholdRead":
        return cls(
            id=event.id,
            owner_user_id=event.owner_user_id,
            device_id=event.device_id,
            occurred_at=event.occurred_at,
            stimulate_level_l=_payload_int(event.payload, "stimulate_level_l"),
            deep_level_l=_payload_int(event.payload, "deep_level_l"),
            stimulate_level_r=_payload_int(event.payload, "stimulate_level_r"),
            deep_level_r=_payload_int(event.payload, "deep_level_r"),
            source=_payload_source(event.payload),
        )


class PumpHealthCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    health_l: int = Field(ge=0, le=2)
    health_r: int = Field(ge=0, le=2)
    source: str | None = Field(default="device", max_length=64)


class PumpHealthRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    device_id: str
    occurred_at: datetime
    health_l: int
    health_r: int
    source: str

    @classmethod
    def from_event(cls, event: PumpTelemetryEventRead) -> "PumpHealthRead":
        return cls(
            id=event.id,
            owner_user_id=event.owner_user_id,
            device_id=event.device_id,
            occurred_at=event.occurred_at,
            health_l=_payload_int(event.payload, "health_l"),
            health_r=_payload_int(event.payload, "health_r"),
            source=_payload_source(event.payload),
        )


class PumpEnergyTargetRead(BaseModel):
    lower_value: int
    upper_value: int


def _payload_int(payload: dict[str, Any], key: str) -> int:
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _payload_source(payload: dict[str, Any]) -> str:
    source = payload.get("source")
    return source if isinstance(source, str) and source else "device"
