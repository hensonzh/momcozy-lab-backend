"""Test-only Product processor: one task ledger, one stateless specialty stream."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...core.errors import ApiError
from ...infrastructure.object_storage import ObjectStorage
from .dispatch import DispatchLease, StoolDispatchQueue
from .job_builder import StoolSpecialtyInputBuilder
from .transport_client import SpecialtyClient, SpecialtyPermanentError, SpecialtyTransientError

LEASE = timedelta(seconds=30)
MAX_ATTEMPTS = 5


class SyntheticStoolDispatchPump:
    """Only manually invoked with synthetic data; no real-photo deployment entry point."""

    def __init__(self, *, sessions: async_sessionmaker[AsyncSession], storage: ObjectStorage,
                 client: SpecialtyClient, market: Literal["CN", "US"]) -> None:
        if client.market != market:
            raise ValueError("Specialty service and Product market must match")
        self.sessions, self.storage, self.client, self.market = sessions, storage, client, market

    async def _fail(self, lease: DispatchLease, now: datetime) -> None:
        async with self.sessions.begin() as session:
            await StoolDispatchQueue(session, market=self.market).fail(lease, now=now)

    async def run_once(self, *, now: datetime | None = None) -> bool:
        def clock() -> datetime:
            return now if now is not None else datetime.now(timezone.utc)

        async with self.sessions.begin() as session:
            lease = await StoolDispatchQueue(session, market=self.market).claim(now=clock(), lease=LEASE)
        if lease is None:
            return False
        try:
            async with self.sessions.begin() as session:
                payload = await StoolSpecialtyInputBuilder(session=session, storage=self.storage).build(lease.analysis_id)
            if payload.market != self.market:
                raise SpecialtyPermanentError("Specialty market scope mismatch")
            async with self.sessions.begin() as session:
                if not await StoolDispatchQueue(session, market=self.market).can_send(lease, now=clock()):
                    return True
            received = False
            async for event in self.client.analyze(payload):
                async with self.sessions.begin() as session:
                    if not await StoolDispatchQueue(session, market=self.market).append(lease, event, now=clock()):
                        return True  # Erased, stale lease or changed source: never persist late output.
                received = True
            if not received:
                raise SpecialtyTransientError("Incomplete specialty stream")
        except (ApiError, SpecialtyPermanentError):
            await self._fail(lease, clock())
        except SpecialtyTransientError:
            async with self.sessions.begin() as session:
                queue = StoolDispatchQueue(session, market=self.market)
                if lease.attempt >= MAX_ATTEMPTS:
                    await queue.fail(lease, now=clock())
                else:
                    await queue.retry(lease, now=clock(), delay=timedelta(seconds=min(2 ** lease.attempt, 120)))
        return True
