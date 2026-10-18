from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class OnboardingConfirmation(Base):
    __tablename__ = "onboarding_confirmations"

    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    primary_infant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("baby_profiles.id"), nullable=False
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatFirstOnboarding(Base):
    """Separate entry permission from the legacy full postpartum profile."""

    __tablename__ = "chat_first_onboarding"
    __table_args__ = (
        CheckConstraint(
            "role IN ('mother', 'father', 'paternal-grandparent', "
            "'maternal-grandparent', 'other-family')",
            name="ck_chat_first_onboarding_role",
        ),
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
