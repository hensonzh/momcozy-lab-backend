from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...infrastructure.db.base import Base


class CryAnalysis(Base):
    __tablename__ = 'cry_analyses'
    __table_args__ = (
        Index('ix_cry_analyses_owner_created', 'owner_user_id', 'created_at'),
        CheckConstraint("status IN ('pending', 'done', 'incomplete', 'failed')", name='ck_cry_analyses_status'),
        CheckConstraint("(status IN ('pending', 'incomplete', 'failed') AND answer IS NULL) OR (status = 'done' AND answer IN ('sleep', 'pain', 'burp', 'uncomfortable', 'hungry', 'no_cry'))", name='ck_cry_analyses_answer'),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    # First Agent Run claims the result for exactly one owner-scoped thread.
    thread_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    answer: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
