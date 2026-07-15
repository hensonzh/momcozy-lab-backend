from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        Index("ix_plans_owner_status_updated", "owner_user_id", "status", "updated_at"),
        Index("ix_plans_owner_type", "owner_user_id", "plan_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    plan_type: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="manual", server_default="manual", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        "payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PlanTask(Base):
    __tablename__ = "plan_tasks"
    __table_args__ = (
        Index("ix_plan_tasks_owner_date_status", "owner_user_id", "task_date", "status"),
        Index("ix_plan_tasks_plan_id", "plan_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    plan_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("plans.id"), nullable=True)
    task_date: Mapped[date | None] = mapped_column(Date, default=None)
    task_time: Mapped[str] = mapped_column(String(16), default="", server_default="", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(
        "payload_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
