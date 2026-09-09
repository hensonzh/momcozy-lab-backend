from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base

CareEventKind = Literal[
    'appointment_confirmed', 'appointment_cancelled', 'intake_submitted',
    'case_consent_revoked', 'consultation_completed', 'consultation_user_no_show',
    'consultation_technical_failure', 'plan_published', 'report_generated', 'report_reviewed',
]

WORK_REMINDER_KINDS = (
    'appointment_confirmed', 'appointment_cancelled', 'intake_submitted',
    'case_consent_revoked', 'consultation_completed', 'consultation_user_no_show',
    'consultation_technical_failure', 'report_generated',
)


class CareServiceEvent(Base):
    """Immutable service milestones; no clinical narrative or notification copy."""
    __tablename__ = 'care_service_events'
    __table_args__ = (
        UniqueConstraint('episode_id', 'kind', 'aggregate_id', 'aggregate_version', name='uq_care_events_aggregate_version'),
        Index('ix_care_events_recipient_created', 'workbench_recipient_id', 'occurred_at', 'id'),
        Index('ix_care_events_episode_created', 'episode_id', 'occurred_at', 'id'),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_episodes.id'), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    appointment_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_appointments.id'))
    actor_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    workbench_recipient_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_providers.user_id'))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CareServiceEventRead(Base):
    __tablename__ = 'care_service_event_reads'
    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('care_service_events.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('users.id'), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
