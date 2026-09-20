"""Me page preferences, voluntary context and mother observations."""

from typing import Any
from datetime import datetime
from uuid import UUID
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from ...infrastructure.db.base import Base


class MePreferences(Base):
    __tablename__ = "me_preferences"
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    concerns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    record_order: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class MotherObservation(Base):
    __tablename__ = "mother_observations"
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(120), nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
