from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class CareConversationLink(Base):
    """One explicit service association per Runtime thread; sharing begins here."""
    __tablename__ = 'care_conversation_links'
    __table_args__ = (Index('ix_care_conversations_episode_shared', 'episode_id', 'shared_since'),)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_episodes.id'), nullable=False)
    shared_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CareReport(Base):
    __tablename__ = 'care_reports'
    __table_args__ = (
        UniqueConstraint('episode_id', 'purpose', 'report_date', 'version', name='uq_care_report_revision'),
        CheckConstraint("purpose IN ('daily','preparation')", name='care_report_purpose'),
        CheckConstraint("status IN ('waiting_for_record','queued','running','ready','failed','cancelled')", name='care_report_status'),
        CheckConstraint('version >= 1 AND attempts >= 0', name='care_report_versions'),
        Index('ix_care_reports_work', 'status', 'available_at', 'lease_until'),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_episodes.id'), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_providers.user_id'), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    case_consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB, 'postgresql'))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB, 'postgresql'))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default='queued')
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class CareReportReview(Base):
    __tablename__ = 'care_report_reviews'
    __table_args__ = (UniqueConstraint('report_id', 'version', name='uq_care_report_review_version'),
        CheckConstraint("decision IN ('confirmed','feedback')", name='care_report_review_decision'),
        CheckConstraint('version >= 1', name='care_report_review_version'),
        CheckConstraint("(decision = 'confirmed' AND feedback = '') OR (decision = 'feedback' AND length(trim(feedback)) >= 5)", name='care_report_review_feedback'))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_reports.id'), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_providers.user_id'), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
