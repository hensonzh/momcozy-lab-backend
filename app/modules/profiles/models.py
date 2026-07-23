from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
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
        CheckConstraint(
            "birth_weight_kg IS NULL OR (birth_weight_kg >= 0.2 AND birth_weight_kg <= 10)",
            name="ck_infant_profiles_birth_weight_kg",
        ),
        CheckConstraint(
            "gestational_age_at_birth_days IS NULL OR (gestational_age_at_birth_days >= 140 AND gestational_age_at_birth_days <= 315)",
            name="ck_infant_profiles_gestational_age_at_birth_days",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sex_at_birth: Mapped[str | None] = mapped_column(String(32), default=None)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    birth_weight_kg: Mapped[float | None] = mapped_column(Float, default=None)
    gestational_age_at_birth_days: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class MaternalProfile(Base):
    """General maternal facts about the latest delivery, without delivery history."""

    __tablename__ = "maternal_profiles"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            name="uq_maternal_profiles_owner_user_id",
        ),
        CheckConstraint(
            "delivery_count IS NULL OR (delivery_count >= 1 AND delivery_count <= 20)",
            name="ck_maternal_profiles_delivery_count",
        ),
        CheckConstraint(
            "latest_delivery_method IS NULL OR latest_delivery_method IN ('vaginal', 'cesarean', 'assisted_vaginal', 'other', 'unknown')",
            name="ck_maternal_profiles_delivery_method",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    delivery_count: Mapped[int | None] = mapped_column(Integer, default=None)
    latest_delivery_method: Mapped[str | None] = mapped_column(String(32), default=None)
    latest_delivery_date: Mapped[date | None] = mapped_column(Date, default=None)
    has_cesarean_history: Mapped[bool | None] = mapped_column(Boolean, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LactationProfile(Base):
    """Current lactation-specific state, separate from general maternal facts."""

    __tablename__ = "lactation_profiles"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            name="uq_lactation_profiles_owner_user_id",
        ),
        CheckConstraint(
            "current_feeding_mode IS NULL OR current_feeding_mode IN "
            "('exclusive_breastfeeding', 'expressed_milk_feeding', "
            "'mixed_feeding', 'formula_feeding', 'unknown')",
            name="ck_lactation_profiles_feeding_mode",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    current_feeding_mode: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MaternalCurrentDeliveryInfant(Base):
    """Links every infant from the latest maternal delivery."""

    __tablename__ = "maternal_current_delivery_infants"
    __table_args__ = (
        UniqueConstraint(
            "maternal_profile_id",
            "birth_order",
            name="uq_maternal_current_delivery_infants_birth_order",
        ),
        UniqueConstraint(
            "infant_id",
            name="uq_maternal_current_delivery_infants_infant_id",
        ),
        CheckConstraint(
            "birth_order >= 1 AND birth_order <= 10",
            name="ck_maternal_current_delivery_infants_birth_order",
        ),
    )

    maternal_profile_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("maternal_profiles.id"),
        primary_key=True,
    )
    infant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("infant_profiles.id"),
        primary_key=True,
    )
    birth_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
