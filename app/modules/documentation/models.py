from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class ClinicalNote(Base):
    __tablename__ = "care_clinical_notes"
    __table_args__ = (UniqueConstraint("consultation_id", "revision"),
        CheckConstraint("status IN ('draft','signed')", name="clinical_note_status"),
        CheckConstraint("revision >= 1 AND version >= 1", name="clinical_note_versions"),
        CheckConstraint("(status = 'signed') = (signed_at IS NOT NULL)", name="clinical_note_signature"),
        Index("uq_care_clinical_note_draft", "consultation_id", unique=True,
            postgresql_where=text("status = 'draft'"), sqlite_where=text("status = 'draft'")))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    consultation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_consultations.id"), nullable=False)
    author_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_providers.user_id"), nullable=False)
    revises_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_clinical_notes.id"))
    amendment_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    content: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CarePlanDraft(Base):
    __tablename__ = "care_plan_drafts"
    __table_args__ = (CheckConstraint("version >= 1 AND published_revision >= 0", name="care_plan_draft_versions"),)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    consultation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_consultations.id"), nullable=False, unique=True)
    author_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_providers.user_id"), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CarePlanPublication(Base):
    __tablename__ = "care_plan_publications"
    __table_args__ = (UniqueConstraint("plan_id", "revision"), CheckConstraint("revision >= 1", name="care_plan_publication_revision"))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_plan_drafts.id"), nullable=False)
    signed_note_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_clinical_notes.id"), nullable=False)
    publisher_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_providers.user_id"), nullable=False)
    publisher_name: Mapped[str] = mapped_column(String(120), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareTaskProgress(Base):
    __tablename__ = "care_task_progress"
    __table_args__ = (CheckConstraint("status IN ('pending','in_progress','completed','skipped')", name="care_task_status"),
        CheckConstraint("version >= 1", name="care_task_version"))
    publication_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_plan_publications.id"), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
