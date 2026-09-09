from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class CareProvider(Base):
    __tablename__ = "care_providers"
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    regions: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list)
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CareEligibility(Base):
    __tablename__ = "care_eligibility_checks"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    package_id: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareOrder(Base):
    __tablename__ = "care_orders"
    __table_args__ = (CheckConstraint("price_minor >= 0", name="care_order_price"),
        CheckConstraint("status IN ('pending','processing','requires_action','reconciling','paid','failed','cancelled')", name="care_order_status"),
        Index("ix_care_orders_owner_created", "owner_user_id", "created_at"))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    eligibility_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_eligibility_checks.id"), nullable=False)
    package_id: Mapped[str] = mapped_column(String(64), nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    total_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    payment_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="sandbox")
    stripe_session_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    stripe_livemode: Mapped[bool | None] = mapped_column(Boolean)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareEpisode(Base):
    __tablename__ = "care_episodes"
    __table_args__ = (CheckConstraint("remaining_sessions >= 0 AND remaining_sessions <= total_sessions", name="care_episode_sessions"),
        CheckConstraint("status IN ('provisioning_pending','active','paused','completed','cancelled')", name="care_episode_status"),
        Index("ix_care_episodes_owner_status", "owner_user_id", "status"))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    order_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_orders.id"), nullable=False, unique=True)
    package_id: Mapped[str] = mapped_column(String(64), nullable=False)
    baby_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("baby_profiles.id"))
    assigned_ibclc_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_providers.user_id"), index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="preparation")
    total_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
