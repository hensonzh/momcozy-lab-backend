from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InviteCodeCreate(BaseModel):
    code: str | None = Field(default=None, max_length=64)
    label: str = Field(default="", max_length=120)
    assigned_to: str = Field(default="", max_length=320)
    expires_at: datetime | None = None


class InviteCodeRead(BaseModel):
    id: UUID
    code: str
    status: str
    label: str
    assigned_to: str
    bound_device_id: str
    bound_user_id: UUID | None
    created_by_service: str
    used_count: int
    expires_at: datetime | None
    disabled_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InviteCodeListResponse(BaseModel):
    items: list[InviteCodeRead]
