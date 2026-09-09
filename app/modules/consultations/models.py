from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class CareIntakeRevision(Base):
    """Append-only user submissions; reports can cite the exact submitted version."""
    __tablename__ = "care_intake_revisions"
    __table_args__ = (UniqueConstraint("appointment_id", "version"), CheckConstraint("version >= 1", name="intake_version"),
        Index("ix_care_intake_episode_submitted", "episode_id", "submitted_at"))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    appointment_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_appointments.id"), nullable=False)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_episodes.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    symptoms: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    feeding_goal: Mapped[str] = mapped_column(Text, nullable=False)
    support_needed: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareConsentRevision(Base):
    """A scoped grant or withdrawal applies to every subsequent read in the episode."""
    __tablename__ = "care_consent_revisions"
    __table_args__ = (UniqueConstraint("episode_id", "scope", "version"), CheckConstraint("version >= 1", name="consent_version"),
        CheckConstraint("scope IN ('ibclc_case','video','ai_context','notifications')", name="consent_scope"))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_episodes.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
