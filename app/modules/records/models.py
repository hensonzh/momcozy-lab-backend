from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class FeedingRecord(Base):
    __tablename__ = "feeding_records"
    __table_args__ = (
        Index("ix_feeding_records_owner_time", "owner_user_id", "feed_time"),
        Index("ix_feeding_records_owner_infant_time", "owner_user_id", "infant_id", "feed_time"),
        Index("ix_feeding_records_plan_task", "plan_task_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    plan_task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("plan_tasks.id"), nullable=True)
    infant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("baby_profiles.id"), nullable=True)
    feed_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feed_type: Mapped[str] = mapped_column(String(32), default="", server_default="", nullable=False)
    feed_action: Mapped[str] = mapped_column(String(32), default="", server_default="", nullable=False)
    volume_ml: Mapped[float | None] = mapped_column(Float, default=None)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    title: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PumpingRecord(Base):
    __tablename__ = "pumping_records"
    __table_args__ = (
        Index("ix_pumping_records_owner_start", "owner_user_id", "pump_start_time"),
        Index("ix_pumping_records_owner_status", "owner_user_id", "status"),
        Index("ix_pumping_records_plan_task", "plan_task_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    plan_task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("plan_tasks.id"), nullable=True)
    pump_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pump_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    milk_volume_ml: Mapped[float | None] = mapped_column(Float, default=None)
    pump_type: Mapped[str] = mapped_column(String(32), default="", server_default="", nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    source: Mapped[str] = mapped_column(String(32), default="manual", server_default="manual", nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class GrowthRecord(Base):
    __tablename__ = "growth_records"
    __table_args__ = (
        Index("ix_growth_records_owner_measured", "owner_user_id", "measured_at"),
        Index("ix_growth_records_owner_infant_measured", "owner_user_id", "infant_id", "measured_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    infant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("baby_profiles.id"), nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    height_cm: Mapped[float | None] = mapped_column(Float, default=None)
    weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    head_cm: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
