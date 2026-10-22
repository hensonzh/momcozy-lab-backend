"""Durable, synthetic-only card shell/failure event semantics."""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import ApiError
from app.infrastructure.db.base import Base
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.events import StoolNonmedicalEventLedger
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisEvent
from app.modules.stool_analyses.transport_contract import SpecialtyTransportEvent
from app.modules.users.models import User


def event(analysis_id, sequence, event_type):
    return SpecialtyTransportEvent(
        schema_version="stool.events.v1", analysis_id=analysis_id,
        sequence=sequence, event_type=event_type,
    )


async def seeded(sessions):
    owner, baby, analysis = uuid4(), uuid4(), uuid4()
    async with sessions.begin() as session:
        session.add(User(id=owner, status="active"))
        session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic"))
        session.add(StoolAnalysis(id=analysis, owner_user_id=owner, baby_id=baby,
                                  thread_id=uuid4(), status="queued"))
    return owner, analysis


def test_synthetic_card_and_failure_persist_and_replay_after_restart(tmp_path):
    async def run():
        url = f"sqlite+aiosqlite:///{tmp_path / 'events.db'}"
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                User.__table__, BabyProfile.__table__, StoolAnalysis.__table__, StoolAnalysisEvent.__table__,
            ]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, analysis = await seeded(sessions)
        async with sessions.begin() as session:
            ledger = StoolNonmedicalEventLedger(session)
            card = await ledger.append(owner=owner, event=event(analysis, 1, "stool.card.created"))
            same = await ledger.append(owner=owner, event=event(analysis, 1, "stool.card.created"))
            assert same.id == card.id
            assert [row.sequence for row in await ledger.list_after(owner=owner, analysis_id=analysis, after=0)] == [1]
        await engine.dispose()
        reopened = create_async_engine(url)
        sessions = async_sessionmaker(reopened, expire_on_commit=False)
        async with sessions.begin() as session:
            ledger = StoolNonmedicalEventLedger(session)
            await ledger.append(owner=owner, event=event(analysis, 2, "stool.analysis.failed"))
            assert (await session.get(StoolAnalysis, analysis)).status == "failed"
        async with sessions.begin() as session:
            ledger = StoolNonmedicalEventLedger(session)
            rows = await ledger.list_after(owner=owner, analysis_id=analysis, after=0)
            assert [(row.sequence, row.event_type) for row in rows] == [
                (1, "stool.card.created"), (2, "stool.analysis.failed"),
            ]
            assert [row.sequence for row in await ledger.list_after(owner=owner, analysis_id=analysis, after=1)] == [2]
            assert await ledger.list_after(owner=owner, analysis_id=analysis, after=2) == []
            same = await ledger.append(owner=owner, event=event(analysis, 2, "stool.analysis.failed"))
            assert same.id == rows[1].id
        await reopened.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("first", [
    (1, "stool.analysis.failed"), (2, "stool.card.created"),
])
def test_card_shell_must_be_first(tmp_path, first):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'first.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                User.__table__, BabyProfile.__table__, StoolAnalysis.__table__, StoolAnalysisEvent.__table__,
            ]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, analysis = await seeded(sessions)
        async with sessions.begin() as session:
            with pytest.raises(ApiError) as captured:
                await StoolNonmedicalEventLedger(session).append(owner=owner, event=event(analysis, *first))
            assert captured.value.code == "stool_analysis_event_conflict"
        await engine.dispose()
    asyncio.run(run())


def test_sequence_conflict_terminal_and_cross_owner_fail_closed(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'conflict.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                User.__table__, BabyProfile.__table__, StoolAnalysis.__table__, StoolAnalysisEvent.__table__,
            ]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, analysis = await seeded(sessions)
        foreign = uuid4()
        async with sessions.begin() as session:
            ledger = StoolNonmedicalEventLedger(session)
            with pytest.raises(ApiError) as captured:
                await ledger.append(owner=foreign, event=event(analysis, 1, "stool.card.created"))
            assert captured.value.code == "stool_analysis_context_unavailable"
            with pytest.raises(ApiError):
                await ledger.list_after(owner=foreign, analysis_id=analysis, after=0)
            await ledger.append(owner=owner, event=event(analysis, 1, "stool.card.created"))
            for bad in (event(analysis, 1, "stool.analysis.cancelled"),
                        event(analysis, 3, "stool.analysis.failed"),
                        event(analysis, 2, "stool.card.created")):
                with pytest.raises(ApiError) as captured:
                    await ledger.append(owner=owner, event=bad)
                assert captured.value.code == "stool_analysis_event_conflict"
            await ledger.append(owner=owner, event=event(analysis, 2, "stool.analysis.cancelled"))
            with pytest.raises(ApiError):
                await ledger.append(owner=owner, event=event(analysis, 3, "stool.analysis.failed"))
        async with sessions.begin() as session:
            analysis_row = await session.get(StoolAnalysis, analysis)
            assert analysis_row.status == "cancelled"
            analysis_row.revoked_at = analysis_row.created_at
        async with sessions.begin() as session:
            with pytest.raises(ApiError):
                await StoolNonmedicalEventLedger(session).list_after(owner=owner, analysis_id=analysis, after=0)
        await engine.dispose()
    asyncio.run(run())


def test_account_deactivation_invalidates_replay_without_deleting_event_history(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inactive.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                User.__table__, BabyProfile.__table__, StoolAnalysis.__table__, StoolAnalysisEvent.__table__,
            ]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, analysis = await seeded(sessions)
        async with sessions.begin() as session:
            await StoolNonmedicalEventLedger(session).append(
                owner=owner, event=event(analysis, 1, "stool.card.created"),
            )
        async with sessions.begin() as session:
            (await session.get(User, owner)).status = "deleted"
        async with sessions.begin() as session:
            ledger = StoolNonmedicalEventLedger(session)
            with pytest.raises(ApiError) as captured:
                await ledger.list_after(owner=owner, analysis_id=analysis, after=0)
            assert captured.value.code == "stool_analysis_context_unavailable"
            with pytest.raises(ApiError):
                await ledger.append(owner=owner, event=event(analysis, 2, "stool.analysis.failed"))
        await engine.dispose()
    asyncio.run(run())



def test_progress_and_failure_are_committed_in_order_and_replayed_after_restart(tmp_path):
    async def run():
        path = tmp_path / "progress.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                User.__table__, BabyProfile.__table__, StoolAnalysis.__table__, StoolAnalysisEvent.__table__,
            ]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, analysis = await seeded(sessions)
        async with sessions.begin() as session:
            ledger = StoolNonmedicalEventLedger(session)
            await ledger.append(owner=owner, event=event(analysis, 1, "stool.card.created"))
            await ledger.append(owner=owner, event=event(analysis, 2, "stool.analysis.started"))
            await ledger.append(owner=owner, event=event(analysis, 3, "stool.analysis.failed"))
        await engine.dispose()
        reopened = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with async_sessionmaker(reopened, expire_on_commit=False).begin() as session:
            ledger = StoolNonmedicalEventLedger(session)
            assert [(row.sequence, row.event_type) for row in await ledger.list_after(
                owner=owner, analysis_id=analysis, after=0,
            )] == [(1, "stool.card.created"), (2, "stool.analysis.started"), (3, "stool.analysis.failed")]
            assert [(row.sequence, row.event_type) for row in await ledger.list_after(
                owner=owner, analysis_id=analysis, after=2,
            )] == [(3, "stool.analysis.failed")]
            with pytest.raises(ApiError):
                await ledger.append(owner=owner, event=event(analysis, 4, "stool.analysis.started"))
        await reopened.dispose()
    asyncio.run(run())
