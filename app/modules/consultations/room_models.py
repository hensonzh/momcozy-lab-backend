from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class CareConsultation(Base):
    __tablename__ = "care_consultations"
    __table_args__ = (CheckConstraint("status IN ('waiting_room','in_progress','note_pending','no_show','failed','cancelled','closed')", name="consultation_status"),
        CheckConstraint("room_status IN ('creating','ready','closing','closed','failed')", name="consultation_room_status"))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    appointment_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_appointments.id"), nullable=False, unique=True)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_episodes.id"), nullable=False, index=True)
    intake_revision_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_intake_revisions.id"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="waiting_room")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    video_provider: Mapped[str] = mapped_column(String(16), nullable=False)
    room_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    room_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    room_status: Mapped[str] = mapped_column(String(16), nullable=False, default="creating")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareRoomParticipant(Base):
    __tablename__ = "care_room_participants"
    __table_args__ = (CheckConstraint("role IN ('mom','ibclc')", name="room_participant_role"),
        CheckConstraint("presence IN ('not_joined','joining','joined','reconnecting','left')", name="room_participant_presence"))
    consultation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_consultations.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    presence: Mapped[str] = mapped_column(String(16), nullable=False, default="not_joined")
    connection_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    connection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CareLocationCheck(Base):
    __tablename__ = "care_location_checks"
    __table_args__ = (Index("ix_care_location_appointment_created", "appointment_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    appointment_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_appointments.id"), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareSessionConsumption(Base):
    __tablename__ = "care_session_consumptions"
    consultation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_consultations.id"), primary_key=True)
    episode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_episodes.id"), nullable=False, index=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareVideoCommand(Base):
    __tablename__ = "care_video_commands"
    __table_args__ = (UniqueConstraint("room_name", "operation"), Index("ix_care_video_commands_due", "status", "available_at"),
        CheckConstraint("operation IN ('create','close')", name="video_command_operation"), CheckConstraint("status IN ('pending','done')", name="video_command_status"))
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    consultation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("care_consultations.id"), nullable=False)
    room_name: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(80))
