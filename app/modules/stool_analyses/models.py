"""Private Product-owned stool analysis task, result and event ledger."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class StoolAnalysis(Base):
    __tablename__ = "stool_analyses"
    __table_args__ = (
        Index("ix_stool_analyses_owner_baby", "owner_user_id", "baby_id", "created_at"),
        Index("ix_stool_analyses_run_due", "market", "status", "run_after"),
        CheckConstraint("run_attempt >= 0", name="run_attempt_nonnegative"),
        CheckConstraint("status IN ('queued','running','completed','failed','cancelled')", name="status"),
        CheckConstraint("market IS NULL OR market IN ('CN','US')", name="market"),
        CheckConstraint("language IS NULL OR language IN ('zh','en')", name="language"),
        CheckConstraint(
            "baby_context_snapshot_id IS NULL OR (market IS NOT NULL AND language IS NOT NULL "
            "AND baby_profile_version >= 1 AND baby_feeding_mode IS NOT NULL AND context_captured_at IS NOT NULL)",
            name="context_snapshot_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    baby_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("baby_profiles.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    run_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    run_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Product-owned minimal snapshot; no name, image bytes or derived medical claims.
    baby_context_snapshot_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    market: Mapped[str | None] = mapped_column(String(2))
    language: Mapped[str | None] = mapped_column(String(2))
    baby_profile_version: Mapped[int | None] = mapped_column(Integer)
    baby_birth_date: Mapped[date | None] = mapped_column(Date)
    baby_feeding_mode: Mapped[str | None] = mapped_column(String(32))
    context_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_schema_version: Mapped[str | None] = mapped_column(String(80))
    final_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class StoolAnalysisPhoto(Base):
    __tablename__ = "stool_analysis_photos"
    __table_args__ = (
        UniqueConstraint("analysis_id", "position", name="uq_stool_analysis_photos_position"),
        CheckConstraint("position >= 0 AND position <= 2", name="position"),
    )

    analysis_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("stool_analyses.id"), primary_key=True)
    photo_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("stool_photos.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    original_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class StoolAnalysisEvent(Base):
    """Committed, replayable card, field, terminal or failure event."""

    __tablename__ = "stool_analysis_events"
    __table_args__ = (
        UniqueConstraint("analysis_id", "sequence", name="uq_stool_analysis_events_sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "event_type IN ('stool.card.created','stool.analysis.started','stool.field.ready','stool.analysis.completed','stool.analysis.failed','stool.analysis.cancelled')",
            name="type",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("stool_analyses.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
