"""Random-schema PostgreSQL concurrency for synthetic nonmedical events."""
import asyncio

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.events import StoolNonmedicalEventLedger
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisEvent
from app.modules.users.models import User
from test_stool_analysis_events import event
from test_stool_photo_postgres_integration import DATABASE_URL, ENDPOINT, isolated_storage_and_database
from uuid import uuid4

pytestmark = pytest.mark.skipif(not DATABASE_URL or not ENDPOINT, reason="Requires isolated PostgreSQL and MinIO test profile.")


def test_postgres_serializes_duplicate_and_competing_terminal_events():
    async def run():
        async with isolated_storage_and_database() as (sessions, _storage):
            owner, baby, analysis = uuid4(), uuid4(), uuid4()
            async with sessions.begin() as session:
                session.add(User(id=owner, status="active"))
                await session.flush()
                session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic"))
                await session.flush()
                session.add(StoolAnalysis(id=analysis, owner_user_id=owner, baby_id=baby,
                                          thread_id=uuid4(), status="queued"))

            async def append(seq, kind):
                async with sessions.begin() as session:
                    return (await StoolNonmedicalEventLedger(session).append(
                        owner=owner, event=event(analysis, seq, kind),
                    )).id

            same = await asyncio.gather(
                append(1, "stool.card.created"), append(1, "stool.card.created"),
            )
            assert same[0] == same[1]
            outcomes = await asyncio.gather(
                append(2, "stool.analysis.failed"), append(2, "stool.analysis.cancelled"),
                return_exceptions=True,
            )
            assert sum(not isinstance(outcome, BaseException) for outcome in outcomes) == 1
            errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
            assert len(errors) == 1 and isinstance(errors[0], ApiError)
            assert errors[0].code == "stool_analysis_event_conflict"
            async with sessions.begin() as session:
                rows = (await session.scalars(select(StoolAnalysisEvent).where(
                    StoolAnalysisEvent.analysis_id == analysis,
                ).order_by(StoolAnalysisEvent.sequence))).all()
                assert [row.sequence for row in rows] == [1, 2]
                final = await session.get(StoolAnalysis, analysis)
                assert final.status == ("failed" if rows[1].event_type == "stool.analysis.failed" else "cancelled")

    asyncio.run(run())
