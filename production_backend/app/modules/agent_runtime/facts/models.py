from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ....infrastructure.db.base import Base


class UserFact(Base):
    __tablename__ = "user_facts"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "fact_key", "fact_kind", name="uq_user_facts_owner_key_kind"),
        CheckConstraint("fact_kind IN ('verified', 'conversation_candidate')", name="ck_user_facts_kind"),
        CheckConstraint("status IN ('active', 'tombstoned')", name="ck_user_facts_status"),
        Index("ix_user_facts_owner_status_kind_updated", "owner_user_id", "status", "fact_kind", "updated_at"),
        Index("ix_user_facts_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    fact_key: Mapped[str] = mapped_column(String(120), nullable=False)
    fact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", nullable=False)
    value: Mapped[Any | None] = mapped_column("value_json", postgresql.JSONB, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), default="personal", server_default="personal", nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_reason: Mapped[str] = mapped_column(String(32), default="", server_default="", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserFactExtractionRun(Base):
    __tablename__ = "user_fact_extraction_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "source_message_id",
            "catalog_version",
            "extractor_version",
            name="uq_user_fact_extractions_source_version",
        ),
        Index("ix_user_fact_extractions_owner_created", "owner_user_id", "created_at"),
        Index("ix_user_fact_extractions_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_user_fact_extractions_locked_until", "locked_until"),
        CheckConstraint(
            "status IN ('queued', 'locked', 'ready_to_apply', 'completed', 'dead_lettered', "
            "'skipped_disabled', 'cancelled')",
            name="ck_user_fact_extractions_status",
        ),
        CheckConstraint("stage IN ('extract', 'apply')", name="ck_user_fact_extractions_stage"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    source_message_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_messages.id"), nullable=False)
    source_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(16), default="extract", server_default="extract", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    request_id: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    trace_id: Mapped[str] = mapped_column(String(120), default="", server_default="", nullable=False)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        "candidates_json",
        postgresql.JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    extracted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
