from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class BabyProfile(Base):
    __tablename__ = 'baby_profiles'
    __table_args__ = (
        Index('ix_baby_profiles_owner_deleted_at', 'owner_user_id', 'deleted_at'),
        Index('ix_baby_profiles_birth_date', 'birth_date'),
        CheckConstraint("sex IN ('female', 'male', 'unspecified')", name='sex'),
        CheckConstraint("feeding_mode IN ('exclusive_breastfeeding', 'expressed_milk_feeding', 'mixed_feeding', 'formula_feeding', 'unknown')", name='feeding_mode'),
        CheckConstraint('version >= 1', name='version'),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sex: Mapped[str] = mapped_column(String(32), default='unspecified', nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    feeding_mode: Mapped[str] = mapped_column(String(32), default='unknown', nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
