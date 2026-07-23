from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_profiles_user_id"),
        Index("ix_user_profiles_estimated_due_date", "estimated_due_date"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    preferred_name: Mapped[str | None] = mapped_column(String(120), default=None)
    age: Mapped[int | None] = mapped_column(Integer, default=None)
    estimated_due_date: Mapped[date | None] = mapped_column(Date, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class InfantProfile(Base):
    __tablename__ = "infant_profiles"
    __table_args__ = (
        Index("ix_infant_profiles_owner_deleted_at", "owner_user_id", "deleted_at"),
        Index("ix_infant_profiles_birth_date", "birth_date"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sex_at_birth: Mapped[str | None] = mapped_column(String(32), default=None)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
