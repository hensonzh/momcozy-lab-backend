from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field


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

    @computed_field  # type: ignore[prop-decorator]  # Pydantic decorator wraps a property.
    @property
    def is_bound(self) -> bool:
        return bool(str(self.bound_device_id or "").strip() or self.bound_user_id)

    @computed_field  # type: ignore[prop-decorator]  # Pydantic decorator wraps a property.
    @property
    def is_available(self) -> bool:
        if self.status != "active":
            return False
        if self.expires_at is None:
            return True
        expires_at = self.expires_at if self.expires_at.tzinfo is not None else self.expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

    model_config = ConfigDict(from_attributes=True)


class InviteCodeListResponse(BaseModel):
    items: list[InviteCodeRead]
    total: int
    limit: int
    offset: int
    has_more: bool
