from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_owner_status_created", "owner_user_id", "status", "created_at"),
        Index("ix_notifications_owner_type_created", "owner_user_id", "notification_type", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    body: Mapped[str] = mapped_column(String(2000), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="unread", server_default="unread", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="system", server_default="system", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        "payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
