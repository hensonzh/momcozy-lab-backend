from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    ticket: dict[str, Any] | None = None
    issue_type: str | None = Field(default=None, max_length=120)
    issue_summary: str | None = Field(default=None, max_length=2000)
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

    @model_validator(mode="before")
    @classmethod
    def merge_legacy_ticket_object(cls, value):
        if not isinstance(value, dict):
            return value
        ticket = value.get("ticket")
        if not isinstance(ticket, dict):
            return value
        merged = dict(value)
        for field in (
            "issue_type",
            "issue_summary",
            "product_model",
            "order_number",
            "purchase_channel",
            "user_contact",
            "urgency",
        ):
            if merged.get(field) is None and ticket.get(field) is not None:
                merged[field] = ticket[field]
        return merged


class SupportTicketListResponse(BaseModel):
    items: list[SupportTicketRead]
