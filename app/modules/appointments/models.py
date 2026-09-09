from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class ProviderAvailability(Base):
    __tablename__ = "care_provider_availability"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="availability_weekday"),
        CheckConstraint("start_minute >= 0 AND end_minute <= 1440 AND start_minute < end_minute", name="availability_range"),
        CheckConstraint("slot_minutes IN (30,60)", name="availability_duration"),
        Index("ix_care_availability_provider_weekday", "provider_id", "weekday"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_providers.user_id"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)


class ProviderCalendarBlock(Base):
    __tablename__ = "care_provider_calendar_blocks"
    __table_args__ = (CheckConstraint("starts_at < ends_at", name="calendar_block_range"), Index("ix_care_calendar_blocks_provider_start", "provider_id", "starts_at"))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_providers.user_id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class BookingEligibility(Base):
    __tablename__ = "care_booking_eligibility"
    __table_args__ = (Index("ix_care_booking_eligibility_episode_created", "episode_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_episodes.id"), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    service_suitable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    emergency_status: Mapped[str] = mapped_column(String(16), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareAppointment(Base):
    __tablename__ = "care_appointments"
    __table_args__ = (
        CheckConstraint("starts_at < ends_at", name="appointment_range"),
        CheckConstraint("status IN ('held','confirmed','in_progress','completed','cancelled','expired')", name="appointment_status"),
        CheckConstraint("version >= 1", name="appointment_version"),
        Index("ix_care_appointments_provider_start", "provider_id", "starts_at"),
        Index("ix_care_appointments_owner_start", "owner_user_id", "starts_at"),
        Index("uq_care_appointments_active_episode", "episode_id", unique=True,
            postgresql_where=text("status IN ('held','confirmed','in_progress')"),
            sqlite_where=text("status IN ('held','confirmed','in_progress')")),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_episodes.id"), nullable=False)
    eligibility_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_booking_eligibility.id"), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_providers.user_id"), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="held")
    hold_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intake_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
