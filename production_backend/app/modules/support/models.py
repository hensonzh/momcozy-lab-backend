from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        UniqueConstraint("ticket_number", name="uq_support_tickets_ticket_number"),
        Index("ix_support_tickets_owner_status_updated", "owner_user_id", "status", "updated_at"),
        Index("ix_support_tickets_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    ticket_number: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="submitted", server_default="submitted", nullable=False)
    issue_type: Mapped[str] = mapped_column(String(120), default="other", server_default="other", nullable=False)
    issue_summary: Mapped[str] = mapped_column(String(2000), default="", server_default="", nullable=False)
    product_model: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    order_number: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    purchase_channel: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    user_contact: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    urgency: Mapped[str] = mapped_column(String(32), default="normal", server_default="normal", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="agent", server_default="agent", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        "payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
