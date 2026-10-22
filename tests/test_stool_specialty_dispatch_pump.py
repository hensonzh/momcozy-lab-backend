"""One Product queue and replay ledger; the specialty service holds no task state."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.dispatch_pump import SyntheticStoolDispatchPump
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisEvent, StoolAnalysisPhoto
from app.modules.stool_analyses.transport_client import SpecialtyClient
from app.modules.stool_photos.models import StoolPhoto
from app.modules.users.models import User
from services.stool_specialty.app import create_app
from services.stool_specialty.settings import SpecialtySettings
from test_stool_analysis_submission import Storage, fixture, prepare
from test_stool_specialty_client import SECRET, wire


async def database(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__, StoolAnalysisEvent.__table__]
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def service(enabled=True):
    return create_app(SpecialtySettings(app_env="test", market="US", service_shared_secret=SECRET,
                                        synthetic_analyze_enabled=enabled))


async def pump(sessions, storage, specialty):
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=specialty), base_url="https://specialty.test")
    return http, SyntheticStoolDispatchPump(sessions=sessions, storage=storage,
        client=SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET), market="US")


def test_streamed_failure_is_durable_replay_after_restart(tmp_path):
    async def run():
        path = tmp_path / "product.db"
        engine, sessions = await database(path)
        storage = Storage()
        ids = await fixture(sessions, storage)
        now = datetime.now(timezone.utc)
        analysis = await prepare(sessions, storage, ids, at=now, market="US", language="en")
        http, processor = await pump(sessions, storage, service())
        async with http:
            assert await processor.run_once(now=now)
            assert not await processor.run_once(now=now + timedelta(seconds=1))
        await engine.dispose()
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            stored = await session.get(StoolAnalysis, analysis.id)
            events = list((await session.scalars(select(StoolAnalysisEvent).where(
                StoolAnalysisEvent.analysis_id == analysis.id,
            ).order_by(StoolAnalysisEvent.sequence))).all())
            assert stored.status == "failed" and stored.run_attempt == 1 and stored.final_result is None
            assert [(row.sequence, row.event_type) for row in events] == [
                (1, "stool.card.created"), (2, "stool.analysis.started"), (3, "stool.analysis.failed"),
            ]
        await engine.dispose()
    asyncio.run(run())


def test_original_tamper_is_never_sent_and_card_fails(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "tamper.db")
        storage = Storage()
        ids = await fixture(sessions, storage)
        now = datetime.now(timezone.utc)
        analysis = await prepare(sessions, storage, ids, at=now, market="US", language="en")
        storage.data[ids[4]] = b"mutated after submission"
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(500)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            processor = SyntheticStoolDispatchPump(sessions=sessions, storage=storage,
                client=SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET), market="US")
            assert await processor.run_once(now=now)
        assert not calls
        async with sessions.begin() as session:
            assert (await session.get(StoolAnalysis, analysis.id)).status == "failed"
        await engine.dispose()
    asyncio.run(run())


def test_unavailable_service_retries_then_recovers_without_second_job(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "retry.db")
        storage = Storage()
        ids = await fixture(sessions, storage)
        now = datetime.now(timezone.utc)
        analysis = await prepare(sessions, storage, ids, at=now, market="US", language="en")
        http, processor = await pump(sessions, storage, service(False))
        async with http:
            assert await processor.run_once(now=now)
        async with sessions.begin() as session:
            row = await session.get(StoolAnalysis, analysis.id)
            assert row.status == "queued" and row.run_attempt == 1
        http, processor = await pump(sessions, storage, service(True))
        async with http:
            assert not await processor.run_once(now=now + timedelta(seconds=1))
            assert await processor.run_once(now=now + timedelta(seconds=3))
        async with sessions.begin() as session:
            row = await session.get(StoolAnalysis, analysis.id)
            assert row.status == "failed" and row.run_attempt == 2
        await engine.dispose()
    asyncio.run(run())


def test_delete_during_stream_drops_late_event(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "delete.db")
        storage = Storage()
        ids = await fixture(sessions, storage)
        now = datetime.now(timezone.utc)
        analysis = await prepare(sessions, storage, ids, at=now, market="US", language="en")
        started, release = asyncio.Event(), asyncio.Event()

        class Paused(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                await release.wait()
                yield wire(analysis.id)

        def handler(_request):
            return httpx.Response(200, stream=Paused(), headers={
                "Content-Type": "text/event-stream", "Cache-Control": "private, no-store",
            })
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            processor = SyntheticStoolDispatchPump(sessions=sessions, storage=storage,
                client=SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET), market="US")
            task = asyncio.create_task(processor.run_once(now=now))
            await asyncio.wait_for(started.wait(), 5)
            async with sessions.begin() as session:
                row = await session.get(StoolAnalysis, analysis.id)
                row.revoked_at = row.deleted_at = now
            release.set()
            assert await asyncio.wait_for(task, 5)
        async with sessions.begin() as session:
            rows = list((await session.scalars(select(StoolAnalysisEvent).where(
                StoolAnalysisEvent.analysis_id == analysis.id,
            ).order_by(StoolAnalysisEvent.sequence))).all())
            assert [row.event_type for row in rows] == ["stool.card.created"]
        await engine.dispose()
    asyncio.run(run())


def test_partial_stream_retries_and_replays_progress_once(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "partial.db")
        storage = Storage()
        ids = await fixture(sessions, storage)
        now = datetime.now(timezone.utc)
        analysis = await prepare(sessions, storage, ids, at=now, market="US", language="en")
        calls = 0

        def handler(_request):
            nonlocal calls
            calls += 1
            stream = wire(analysis.id, event_type="stool.analysis.started", sequence=2)
            if calls == 2:
                stream += wire(analysis.id, event_type="stool.analysis.failed", sequence=3)
            return httpx.Response(200, content=stream, headers={
                "Content-Type": "text/event-stream", "Cache-Control": "private, no-store",
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            processor = SyntheticStoolDispatchPump(sessions=sessions, storage=storage,
                client=SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET), market="US")
            assert await processor.run_once(now=now)
            async with sessions.begin() as session:
                row = await session.get(StoolAnalysis, analysis.id)
                assert row.status == "queued" and row.run_attempt == 1
                assert [(e.sequence, e.event_type) for e in (await session.scalars(
                    select(StoolAnalysisEvent).where(StoolAnalysisEvent.analysis_id == analysis.id)
                    .order_by(StoolAnalysisEvent.sequence)
                )).all()] == [(1, "stool.card.created"), (2, "stool.analysis.started")]
            assert not await processor.run_once(now=now + timedelta(seconds=1))
            assert await processor.run_once(now=now + timedelta(seconds=3))
        assert calls == 2
        async with sessions.begin() as session:
            row = await session.get(StoolAnalysis, analysis.id)
            assert row.status == "failed" and row.run_attempt == 2
            assert [(e.sequence, e.event_type) for e in (await session.scalars(
                select(StoolAnalysisEvent).where(StoolAnalysisEvent.analysis_id == analysis.id)
                .order_by(StoolAnalysisEvent.sequence)
            )).all()] == [(1, "stool.card.created"), (2, "stool.analysis.started"), (3, "stool.analysis.failed")]
        await engine.dispose()
    asyncio.run(run())


def test_invalid_medical_event_after_progress_fails_without_result(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "invalid-medical.db")
        storage = Storage()
        ids = await fixture(sessions, storage)
        now = datetime.now(timezone.utc)
        analysis = await prepare(sessions, storage, ids, at=now, market="US", language="en")
        stream = (wire(analysis.id, event_type="stool.analysis.started", sequence=2)
                  + wire(analysis.id, event_type="stool.analysis.completed", sequence=3,
                         extra=',"risk_level":"unvalidated"'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=stream, headers={"Content-Type": "text/event-stream", "Cache-Control": "private, no-store"},
        ))) as http:
            pump = SyntheticStoolDispatchPump(sessions=sessions, storage=storage,
                client=SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET), market="US")
            assert await pump.run_once(now=now)
        async with sessions.begin() as session:
            row = await session.get(StoolAnalysis, analysis.id)
            assert row.status == "failed" and row.final_result is None and row.result_validated_at is None
            assert [(event.sequence, event.event_type) for event in (await session.scalars(
                select(StoolAnalysisEvent).where(StoolAnalysisEvent.analysis_id == analysis.id)
                .order_by(StoolAnalysisEvent.sequence)
            )).all()] == [(1, "stool.card.created"), (2, "stool.analysis.started"), (3, "stool.analysis.failed")]
        await engine.dispose()
    asyncio.run(run())


def test_deletion_after_progress_discards_late_terminal_event(tmp_path):
    async def run():
        engine, sessions = await database(tmp_path / "deleted-midstream.db")
        storage = Storage()
        ids = await fixture(sessions, storage)
        now = datetime.now(timezone.utc)
        analysis = await prepare(sessions, storage, ids, at=now, market="US", language="en")
        sent_progress, release = asyncio.Event(), asyncio.Event()

        class Paused(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield wire(analysis.id, event_type="stool.analysis.started", sequence=2)
                sent_progress.set()
                await release.wait()
                yield wire(analysis.id, event_type="stool.analysis.failed", sequence=3)

        def handler(_request):
            return httpx.Response(200, stream=Paused(), headers={
                "Content-Type": "text/event-stream", "Cache-Control": "private, no-store",
            })
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            pump = SyntheticStoolDispatchPump(sessions=sessions, storage=storage,
                client=SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET), market="US")
            task = asyncio.create_task(pump.run_once(now=now))
            await asyncio.wait_for(sent_progress.wait(), timeout=5)
            async def progress_persisted():
                while True:
                    async with sessions.begin() as session:
                        rows = (await session.scalars(select(StoolAnalysisEvent).where(
                            StoolAnalysisEvent.analysis_id == analysis.id,
                        ))).all()
                    if len(rows) == 2:
                        return
                    await asyncio.sleep(0)
            await asyncio.wait_for(progress_persisted(), timeout=5)
            async with sessions.begin() as session:
                row = await session.get(StoolAnalysis, analysis.id)
                row.deleted_at = row.revoked_at = now
            release.set()
            assert await asyncio.wait_for(task, timeout=5)
        async with sessions.begin() as session:
            rows = list((await session.scalars(select(StoolAnalysisEvent).where(
                StoolAnalysisEvent.analysis_id == analysis.id,
            ).order_by(StoolAnalysisEvent.sequence))).all())
            assert [(row.sequence, row.event_type) for row in rows] == [
                (1, "stool.card.created"), (2, "stool.analysis.started"),
            ]
        await engine.dispose()
    asyncio.run(run())
