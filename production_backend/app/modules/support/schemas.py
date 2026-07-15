from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SupportTicketRead(BaseModel):
    id: UUID
    owner_user_id: UUID
    ticket_number: str
    status: str
    issue_type: str
    issue_summary: str
    product_model: str
    order_number: str
    purchase_channel: str
    user_contact: str
    urgency: str
    source: str
    payload: dict[str, Any]
    submitted_at: datetime | None = None
    resolved_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SupportTicketCreate(BaseModel):
    issue_type: str | None = Field(default=None, max_length=120)
    issue_summary: str = Field(min_length=1, max_length=2000)
    product_model: str | None = Field(default=None, max_length=120)
    order_number: str | None = Field(default=None, max_length=120)
    purchase_channel: str | None = Field(default=None, max_length=120)
    user_contact: str | None = Field(default=None, max_length=255)
    urgency: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] | None = None
    thread_id: str | None = Field(default=None, max_length=120)
    locale: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    message_sent_at: datetime | None = None


class SupportTicketListResponse(BaseModel):
    items: list[SupportTicketRead]
