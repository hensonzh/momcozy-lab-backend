from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


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
    created_at: datetime | None = None
    send_status: str | None = None
    sent_at: datetime | None = None
    trigger_at: datetime | None = None
    related_resource_type: str | None = None
    related_resource_id: UUID | None = None
    route: str | None = None

    model_config = ConfigDict(from_attributes=True)


class NotificationReadStateUpdate(BaseModel):
    read: bool = True


class NotificationListResponse(BaseModel):
    items: list[NotificationRead]
    next_cursor: str | None = None
    unread_count: int = 0


NotificationPermission = Literal["not_determined", "authorized", "denied", "provisional", "unavailable"]


class PushInstallationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: UUID
    installation_secret: SecretStr = Field(min_length=32, max_length=256)
    revision: int = Field(ge=1, le=9_000_000_000_000_000)
    platform: Literal["android", "ios"]
    permission: NotificationPermission
    token: SecretStr | None = Field(default=None, min_length=10, max_length=4096)
    locale: str = Field(default="en", min_length=2, max_length=16)


class PushInstallationRead(BaseModel):
    installation_id: UUID
    binding_id: UUID
    permission: str
    revision: int
    token_registered: bool
    push_available: bool = False


class PushInstallationDetach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_secret: SecretStr = Field(min_length=32, max_length=256)


class ReminderWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    installation_id: UUID | None = None


class ReminderRead(BaseModel):
    enabled: bool
    status: str
    trigger_at: datetime | None = None
    notification_id: UUID | None = None


class NotificationOpenRead(BaseModel):
    notification: NotificationRead
    route: str | None
    resource_available: bool
