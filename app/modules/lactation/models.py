from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class LactationRecord(Base):
    __tablename__ = "lactation_records"
    __table_args__ = (
        Index("ix_lactation_owner_time", "owner_user_id", "occurred_at"),
        CheckConstraint("side IN ('left', 'right')", name="lactation_side"),
        CheckConstraint("(method = 'pump' AND duration_minutes IS NULL) OR (method = 'nurse' AND volume_ml IS NULL)", name="lactation_measurement_kind"),
        CheckConstraint("volume_ml IS NULL OR (volume_ml >= 0 AND volume_ml <= 2000)", name="lactation_volume"),
        CheckConstraint("duration_minutes IS NULL OR (duration_minutes >= 0 AND duration_minutes <= 240)", name="lactation_duration"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    volume_ml: Mapped[float | None] = mapped_column(Float)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    feeling: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @property
    def observation(self) -> dict[str, Any]:
        return {"method": self.method, "occurred_at": self.occurred_at, "side": self.side,
            "feeling": self.feeling, "note": self.note,
            **({"volume_ml": self.volume_ml} if self.method == "pump" else {"duration_minutes": self.duration_minutes})}
