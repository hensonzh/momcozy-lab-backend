from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base
from .schemas import Observation, observation_adapter


class BabyRecord(Base):
    __tablename__ = 'baby_records'
    __table_args__ = (
        Index('ix_baby_records_scope_time', 'owner_user_id', 'baby_id', 'occurred_at', 'id'),
        Index('ix_baby_records_scope_date', 'owner_user_id', 'baby_id', 'recorded_on', 'id'),
        Index('uq_baby_records_active_sleep', 'baby_id', unique=True, postgresql_where=text("kind = 'sleep' AND ended_at IS NULL AND deleted_at IS NULL")),
        CheckConstraint("kind IN ('feeding','sleep','diaper','growth','development','daily_status')", name='baby_record_kind'),
        CheckConstraint("ended_at IS NULL OR (kind = 'sleep' AND ended_at > occurred_at)", name='baby_record_sleep_end'),
        CheckConstraint('version >= 1', name='baby_record_version'),
        CheckConstraint("(kind IN ('growth','development','daily_status') AND recorded_on IS NOT NULL AND occurred_at IS NULL) OR (kind IN ('feeding','sleep','diaper') AND occurred_at IS NOT NULL AND recorded_on IS NULL)", name='baby_record_time_kind'),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    baby_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('baby_profiles.id'), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_on: Mapped[date | None] = mapped_column(Date)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def observation(self) -> Observation:
        return observation_adapter.validate_python({'kind': self.kind, **self.data,
            **({'recorded_on': self.recorded_on} if self.kind in {'growth', 'development', 'daily_status'} else {'occurred_at': self.occurred_at}),
            **({'ended_at': self.ended_at} if self.kind == 'sleep' else {})})
