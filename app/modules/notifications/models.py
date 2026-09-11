from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_owner_status_created", "owner_user_id", "status", "created_at"),
        Index("ix_notifications_owner_type_created", "owner_user_id", "notification_type", "created_at"),
        Index("ix_notifications_send_due", "send_status", "trigger_at"),
        UniqueConstraint("idempotency_key", name="uq_notifications_idempotency_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    body: Mapped[str] = mapped_column(String(2000), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="unread", server_default="unread", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="system", server_default="system", nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="service_updates", server_default="service_updates", nullable=False)
    # Inbox state and channel execution state are independent.
    send_status: Mapped[str] = mapped_column(String(24), default="in_app", server_default="in_app", nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), default="immediate", server_default="immediate", nullable=False)
    trigger_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    related_resource_type: Mapped[str | None] = mapped_column(String(32))
    related_resource_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    resource_version: Mapped[int | None] = mapped_column(Integer)
    route: Mapped[str | None] = mapped_column(String(400))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
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


class PushInstallation(Base):
    """An installation survives logout; its account/session binding does not."""
    __tablename__ = "push_installations"
    __table_args__ = (Index("ix_push_installations_owner_session", "owner_user_id", "session_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"))
    session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("device_sessions.id"))
    binding_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), default=uuid4, nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    permission: Mapped[str] = mapped_column(String(24), nullable=False, default="not_determined", server_default="not_determined")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en", server_default="en")
    encrypted_token: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    client_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("notification_id", "installation_id", "binding_id", name="uq_notification_delivery_binding"),
        Index("ix_notification_deliveries_status_available", "status", "available_at"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    notification_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("notifications.id"), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("push_installations.id"), nullable=False)
    binding_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationEventReceipt(Base):
    """Consumer checkpoint for the existing transactional care event stream."""
    __tablename__ = "notification_event_receipts"
    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_service_events.id"), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
