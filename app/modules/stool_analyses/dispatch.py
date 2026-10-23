"""One Product-owned analysis queue; no remote task, inbox or cancellation state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..baby.profile_models import BabyProfile
from ..stool_photos.models import StoolPhoto
from ..users.models import User
from .events import StoolAnalysisEventLedger
from .models import StoolAnalysis, StoolAnalysisEvent, StoolAnalysisPhoto
from .transport_contract import SpecialtyTransportEvent


def _instant(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class DispatchLease:
    analysis_id: UUID
    attempt: int


class StoolDispatchQueue:
    """Caller owns transactions. A fenced lease only authorizes one processing attempt."""

    def __init__(self, session: AsyncSession, *, market: Literal["CN", "US"]) -> None:
        self.session, self.market = session, market

    async def claim(self, *, now: datetime, lease: timedelta) -> DispatchLease | None:
        if now.tzinfo is None or lease <= timedelta(0):
            raise ValueError("An aware time and positive lease are required")
        while True:
            analysis = await self.session.scalar(select(StoolAnalysis).where(
                StoolAnalysis.market == self.market,
                StoolAnalysis.deleted_at.is_(None), StoolAnalysis.revoked_at.is_(None),
                StoolAnalysis.run_after <= now,
                or_(StoolAnalysis.status == "queued", and_(
                    StoolAnalysis.status == "running", StoolAnalysis.run_lease_until <= now,
                )),
            ).order_by(StoolAnalysis.run_after, StoolAnalysis.created_at, StoolAnalysis.id)
                .with_for_update(skip_locked=True).limit(1))
            if analysis is None:
                return None
            if not await self._eligible(analysis):
                await self._fail(analysis)
                await self.session.flush()
                continue
            analysis.status = "running"
            analysis.run_attempt += 1
            analysis.run_lease_until = now + lease
            await self.session.flush()
            return DispatchLease(analysis.id, analysis.run_attempt)

    async def _eligible(self, analysis: StoolAnalysis) -> bool:
        if (analysis.baby_context_snapshot_id is None or analysis.baby_profile_version is None
                or analysis.baby_feeding_mode is None or analysis.context_captured_at is None
                or analysis.language not in ("zh", "en")):
            return False
        owner = await self.session.get(User, analysis.owner_user_id)
        baby = await self.session.get(BabyProfile, analysis.baby_id)
        if (owner is None or owner.status != "active" or owner.deleted_at is not None
                or baby is None or baby.owner_user_id != analysis.owner_user_id or baby.deleted_at is not None):
            return False
        links = list((await self.session.scalars(select(StoolAnalysisPhoto).where(
            StoolAnalysisPhoto.analysis_id == analysis.id,
        ).order_by(StoolAnalysisPhoto.position))).all())
        if not 1 <= len(links) <= 3 or [link.position for link in links] != list(range(len(links))):
            return False
        for link in links:
            photo = await self.session.get(StoolPhoto, link.photo_id)
            if (photo is None or photo.owner_user_id != analysis.owner_user_id
                    or photo.deleted_at is not None or photo.purged_at is not None
                    or photo.review_status != "passed" or photo.review_expires_at is None
                    or photo.review_sha256 != link.original_sha256 or photo.sha256 != link.original_sha256):
                return False
        return True

    async def _current(self, lease: DispatchLease, *, now: datetime) -> StoolAnalysis | None:
        analysis = await self.session.scalar(select(StoolAnalysis).where(
            StoolAnalysis.id == lease.analysis_id,
        ).with_for_update())
        if (analysis is None or analysis.market != self.market or analysis.status != "running"
                or analysis.run_attempt != lease.attempt or analysis.run_lease_until is None
                or _instant(analysis.run_lease_until) <= _instant(now)
                or analysis.deleted_at is not None or analysis.revoked_at is not None):
            return None
        return analysis

    async def _fail(self, analysis: StoolAnalysis) -> None:
        analysis.run_lease_until = None
        owner = await self.session.get(User, analysis.owner_user_id)
        baby = await self.session.get(BabyProfile, analysis.baby_id)
        if (analysis.deleted_at is not None or analysis.revoked_at is not None
                or owner is None or owner.status != "active" or owner.deleted_at is not None
                or baby is None or baby.owner_user_id != analysis.owner_user_id or baby.deleted_at is not None):
            analysis.status = "failed"
            return
        last = await self.session.scalar(select(StoolAnalysisEvent).where(
            StoolAnalysisEvent.analysis_id == analysis.id,
        ).order_by(StoolAnalysisEvent.sequence.desc()).limit(1))
        await StoolAnalysisEventLedger(self.session).append(owner=analysis.owner_user_id, event=SpecialtyTransportEvent(
            schema_version="stool.events.v1", analysis_id=analysis.id,
            sequence=(last.sequence + 1 if last is not None else 2),
            event_type="stool.analysis.failed", payload={"code": "analysis_failed"},
        ))

    async def can_send(self, lease: DispatchLease, *, now: datetime) -> bool:
        """Recheck committed deletion/review state after reading image bytes, before outbound I/O."""
        analysis = await self._current(lease, now=now)
        if analysis is None:
            return False
        if not await self._eligible(analysis):
            await self._fail(analysis)
            return False
        return True

    async def append(self, lease: DispatchLease, event: SpecialtyTransportEvent, *, now: datetime) -> bool:
        analysis = await self._current(lease, now=now)
        if analysis is None or event.analysis_id != lease.analysis_id:
            return False
        if not await self._eligible(analysis):
            await self._fail(analysis)
            return False
        await StoolAnalysisEventLedger(self.session).append(owner=analysis.owner_user_id, event=event, now=now)
        if event.event_type in ("stool.analysis.completed", "stool.analysis.failed"):
            analysis.run_lease_until = None
        return True

    async def retry(self, lease: DispatchLease, *, now: datetime, delay: timedelta) -> bool:
        if delay <= timedelta(0):
            raise ValueError("A positive retry delay is required")
        analysis = await self._current(lease, now=now)
        if analysis is None:
            return False
        analysis.status, analysis.run_after, analysis.run_lease_until = "queued", now + delay, None
        return True

    async def fail(self, lease: DispatchLease, *, now: datetime) -> bool:
        analysis = await self._current(lease, now=now)
        if analysis is None:
            return False
        await self._fail(analysis)
        return True
