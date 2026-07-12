from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ....infrastructure.db.base import Base


class UserFact(Base):
    __tablename__ = "user_facts"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "fact_key", name="uq_user_facts_owner_key"),
        Index("ix_user_facts_owner_updated", "owner_user_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    fact_key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[Any] = mapped_column("value_json", postgresql.JSONB, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_priority: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[str] = mapped_column(String(500), default="", server_default="", nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    source_message_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_messages.id"), nullable=False)
    source_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="extracting")
    extracted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
