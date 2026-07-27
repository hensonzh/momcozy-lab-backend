from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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


class NotificationReadStateUpdate(BaseModel):
    read: bool = True


class NotificationListResponse(BaseModel):
    items: list[NotificationRead]
