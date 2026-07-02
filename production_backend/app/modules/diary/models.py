from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class PregnancyDiaryEntry(Base):
    __tablename__ = "pregnancy_diary_entries"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "entry_date", name="uq_pregnancy_diary_owner_date"),
        Index("ix_pregnancy_diary_owner_date", "owner_user_id", "entry_date"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    gestational_week: Mapped[str] = mapped_column(String(32), default="", server_default="", nullable=False)
    mood: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    energy_level: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    sleep_summary: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    fetal_movement: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    symptom_tags: Mapped[list[Any]] = mapped_column(
        "symptom_tags_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    appointment_note: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    nutrition_note: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    attachments: Mapped[list[Any]] = mapped_column(
        "attachments_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PregnancyDiaryHealthNote(Base):
    __tablename__ = "pregnancy_diary_health_notes"
    __table_args__ = (
        Index("ix_pregnancy_diary_health_notes_entry", "entry_id", "created_at"),
        Index("ix_pregnancy_diary_health_notes_owner_date", "owner_user_id", "entry_date"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("pregnancy_diary_entries.id"), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    topic: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    user_report: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    asked_questions: Mapped[list[Any]] = mapped_column(
        "asked_questions_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    known_answers: Mapped[dict[str, Any]] = mapped_column(
        "known_answers_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    suggestion_summary: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    follow_up: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
