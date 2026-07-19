from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class NotificationRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    notification_type: str
    title: str
    body: str
    status: str
    source: str
    payload: dict[str, Any]
    delivered_at: datetime | None = None
    read_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class NotificationServiceCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    owner_user_id: UUID
    notification_type: str = Field(validation_alias=AliasChoices("notification_type", "reminder_type", "type"), min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    body: str | None = Field(default=None, validation_alias=AliasChoices("body", "message"), max_length=2000)
    source: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] | None = Field(default=None, validation_alias=AliasChoices("payload", "data"))
    delivered_at: datetime | None = None


class NotificationReadStateUpdate(BaseModel):
    read: bool = True


class NotificationListResponse(BaseModel):
    items: list[NotificationRead]
