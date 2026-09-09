from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .event_models import CareEventKind, CareServiceEvent


async def record_care_event(session: AsyncSession, *, episode_id: UUID, kind: CareEventKind,
    aggregate_id: UUID, aggregate_version: int, actor_user_id: UUID | None,
    recipient_id: UUID | None, occurred_at: datetime, appointment_id: UUID | None = None) -> None:
    # Call inside the owning business transaction, after the aggregate is flushed.
    # Retrying an operation cannot create a second milestone or reset read state.
    await session.execute(insert(CareServiceEvent).values(
        episode_id=episode_id, kind=kind, aggregate_id=aggregate_id, aggregate_version=aggregate_version,
        actor_user_id=actor_user_id, workbench_recipient_id=recipient_id, occurred_at=occurred_at,
        appointment_id=appointment_id,
    ).on_conflict_do_nothing(constraint='uq_care_events_aggregate_version'))
