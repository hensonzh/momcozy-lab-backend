import asyncio
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import ApiError
from app.modules.stool_photos.models import StoolPhoto
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisPhoto
from app.modules.stool_photos.repository import StoolPhotoRepository
from app.modules.stool_photos.review import ReviewOutcome
from app.modules.stool_photos.service import StoolPhotoService
from app.modules.users.models import User
from app.workers.stool_photo_reviews import process_next
from test_stool_photo_rules import image_bytes


class Storage:
    def __init__(self):
        self.values = {}

    async def put_bytes(self, *, key, body, content_type):
        self.values[key] = body

    async def get_bytes(self, *, key):
        return self.values[key]

    async def delete(self, *, key):
        self.values.pop(key, None)


class Idempotency:
    def __init__(self):
        self.record = SimpleNamespace(response_ref="")
        self.calls = 0

    async def reserve(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(record=self.record,
                               status="reserved" if self.calls == 1 else "replay")

    async def mark_completed(self, *, record, response_ref):
        record.response_ref = response_ref


class Audit:
    async def record(self, **kwargs):
        assert "object_key" not in kwargs.get("details", {})


class Provider:
    def __init__(self, status):
        self.status = status
        self.calls = 0

    async def review(self, image):
        self.calls += 1
        return ReviewOutcome(self.status, () if self.status == "passed" else ("not_stool",), "test-v1")


def test_durable_upload_worker_review_and_replay():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status="active"))
        store = Storage()
        idem = Idempotency()
        body = image_bytes()
        async with sessions.begin() as session:
            service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                        idempotency=idem, audit=Audit())
            first = await service.upload(owner=owner, body=body, key="once", request_id="r")
            assert first.review_status == "pending" and first.sha256 == sha256(body).hexdigest()
            replay = await service.upload(owner=owner, body=body, key="once", request_id="r")
            assert replay.id == first.id and len(store.values) == 1
        provider = Provider("passed")
        assert await process_next(sessions, store, provider)
        async with sessions.begin() as session:
            reviewed = await StoolPhotoRepository(session).get_for_owner(first.id, owner)
            assert reviewed.review_status == "passed"
            assert reviewed.review_sha256 == first.sha256
            assert reviewed.policy_version == "test-v1"
            assert reviewed.review_expires_at is not None
            assert reviewed.review_expires_at - reviewed.reviewed_at == timedelta(days=180)
            assert await StoolPhotoRepository(session).get_for_owner(first.id, uuid4()) is None
        assert provider.calls == 1 and not await process_next(sessions, store, provider)
        await engine.dispose()
    asyncio.run(run())


def test_review_storage_tamper_fails_closed_without_model_call():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status="active"))
        store = Storage()
        async with sessions.begin() as session:
            photo = await StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                            idempotency=Idempotency(), audit=Audit()).upload(
                owner=owner, body=image_bytes(), key="one", request_id="r")
        store.values[photo.object_key] = b"changed"
        provider = Provider("passed")
        assert await process_next(sessions, store, provider)
        async with sessions.begin() as session:
            reviewed = await StoolPhotoRepository(session).get_for_owner(photo.id, owner)
            assert reviewed.review_status == "failed"
            assert reviewed.review_sha256 is None
            assert reviewed.reason_codes == ["review_unavailable"]
        assert provider.calls == 0
        await engine.dispose()
    asyncio.run(run())


def test_invalid_file_rejected_before_storage_or_idempotency():
    async def run():
        store, idem = Storage(), Idempotency()
        service = StoolPhotoService(repository=SimpleNamespace(), storage=store,
                                    idempotency=idem, audit=Audit())
        with pytest.raises(ApiError) as exc:
            await service.upload(owner=uuid4(), body=b"not-an-image", key="x", request_id="r")
        assert exc.value.code == "stool_photo_invalid_image"
        assert idem.calls == 0 and not store.values
    asyncio.run(run())


def test_deletion_revokes_review_and_worker_purges_private_original():
    from app.workers.stool_photo_reviews import purge_deleted
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status="active"))
        store = Storage()
        async with sessions.begin() as session:
            service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                        idempotency=Idempotency(), audit=Audit())
            photo = await service.upload(owner=owner, body=image_bytes(), key="one", request_id="r")
            photo_id, object_key = photo.id, photo.object_key
        async with sessions.begin() as session:
            service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                        idempotency=Idempotency(), audit=Audit())
            await service.delete(owner=owner, photo_id=photo_id, request_id="r")
            await service.delete(owner=owner, photo_id=photo_id, request_id="r")
            with pytest.raises(ApiError):
                await service.get(owner=owner, photo_id=photo_id)
        assert await purge_deleted(sessions, store)
        assert object_key not in store.values
        assert not await purge_deleted(sessions, store)
        assert not await process_next(sessions, store, Provider("passed"))
        await engine.dispose()
    asyncio.run(run())


def test_original_is_not_auto_purged_by_age_but_explicit_delete_still_purges():
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.workers.stool_photo_reviews import purge_deleted

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status="active"))
        store = Storage()
        async with sessions.begin() as session:
            service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                        idempotency=Idempotency(), audit=Audit())
            photo = await service.upload(owner=owner, body=image_bytes(), key="one", request_id="r")
            photo.created_at = datetime.now(timezone.utc) - timedelta(days=181)
            photo_id, object_key = photo.id, photo.object_key
        assert not await purge_deleted(sessions, store)
        assert await process_next(sessions, store, Provider("passed"))
        async with sessions.begin() as session:
            photo = await session.scalar(select(StoolPhoto).where(StoolPhoto.id == photo_id))
            assert photo.deleted_at is None and photo.purged_at is None
            assert photo.review_expires_at - photo.reviewed_at == timedelta(days=180)
            await StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                    idempotency=Idempotency(), audit=Audit()).delete(
                owner=owner, photo_id=photo_id, request_id="r")
        assert object_key in store.values
        assert await purge_deleted(sessions, store)
        assert object_key not in store.values
        await engine.dispose()
    asyncio.run(run())


def test_expired_unlinked_original_is_tombstoned_but_linked_history_survives():
    from datetime import datetime, timedelta, timezone
    from app.workers.stool_photo_reviews import expire_unlinked, purge_deleted

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, baby_id, thread_id = uuid4(), uuid4(), uuid4()
        async with sessions.begin() as session:
            session.add_all([
                User(id=owner, status="active"),
                BabyProfile(id=baby_id, owner_user_id=owner, name="Baby"),
            ])
        store = Storage()
        async with sessions.begin() as session:
            service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                        idempotency=Idempotency(), audit=Audit())
            orphan = await service.upload(owner=owner, body=image_bytes(), key="orphan", request_id="r")
        async with sessions.begin() as session:
            service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                        idempotency=Idempotency(), audit=Audit())
            linked = await service.upload(owner=owner, body=image_bytes(), key="linked", request_id="r")
            orphan = await session.get(StoolPhoto, orphan.id)
            assert orphan is not None
            expired = datetime.now(timezone.utc) - timedelta(seconds=1)
            for photo in (orphan, linked):
                photo.review_status = "passed"
                photo.review_sha256 = photo.sha256
                photo.review_expires_at = expired
            analysis = StoolAnalysis(owner_user_id=owner, baby_id=baby_id, thread_id=thread_id)
            session.add(analysis)
            await session.flush()
            session.add(StoolAnalysisPhoto(
                analysis_id=analysis.id, photo_id=linked.id, position=0,
                original_sha256=linked.sha256,
            ))
            orphan_id, linked_id = orphan.id, linked.id
            orphan_key, linked_key = orphan.object_key, linked.object_key
        assert await expire_unlinked(sessions, now=datetime.now(timezone.utc))
        async with sessions.begin() as session:
            assert (await session.get(StoolPhoto, orphan_id)).deleted_at is not None
            assert (await session.get(StoolPhoto, linked_id)).deleted_at is None
        assert await purge_deleted(sessions, store)
        assert orphan_key not in store.values and linked_key in store.values
        await engine.dispose()

    asyncio.run(run())


def test_object_deletion_failure_leaves_tombstone_for_worker_retry():
    from sqlalchemy import select
    from app.workers.stool_photo_reviews import purge_deleted

    class FlakyStorage(Storage):
        failures = 1

        async def delete(self, *, key):
            if self.failures:
                self.failures -= 1
                raise OSError('object storage unavailable')
            await super().delete(key=key)

    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status='active'))
        store = FlakyStorage()
        async with sessions.begin() as session:
            service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                        idempotency=Idempotency(), audit=Audit())
            photo = await service.upload(owner=owner, body=image_bytes(), key='one', request_id='r')
            photo_id, object_key = photo.id, photo.object_key
        async with sessions.begin() as session:
            service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                        idempotency=Idempotency(), audit=Audit())
            await service.delete(owner=owner, photo_id=photo_id, request_id='r')
        with pytest.raises(OSError):
            await purge_deleted(sessions, store)
        async with sessions.begin() as session:
            value = await session.scalar(select(StoolPhoto).where(StoolPhoto.id == photo_id))
            assert value.deleted_at is not None and value.purged_at is None
        assert object_key in store.values
        assert await purge_deleted(sessions, store)
        assert object_key not in store.values
        await engine.dispose()
    asyncio.run(run())


def test_expired_worker_lease_is_reclaimed_and_old_result_cannot_overwrite():
    """A slow attempt can finish after a restarted worker has claimed its lease."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.workers.stool_photo_reviews import LEASE

    class PausedProvider:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def review(self, image):
            self.started.set()
            await self.release.wait()
            return ReviewOutcome('passed', (), 'stale-policy')

    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as connection:
            await connection.run_sync(User.__table__.create)
            await connection.run_sync(StoolPhoto.__table__.create)
            await connection.run_sync(BabyProfile.__table__.create)
            await connection.run_sync(StoolAnalysis.__table__.create)
            await connection.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status='active'))
        storage = Storage()
        async with sessions.begin() as session:
            photo = await StoolPhotoService(
                repository=StoolPhotoRepository(session), storage=storage,
                idempotency=Idempotency(), audit=Audit(),
            ).upload(owner=owner, body=image_bytes(), key='lease', request_id='r')
            photo_id = photo.id
        paused = PausedProvider()
        first = asyncio.create_task(process_next(sessions, storage, paused))
        await paused.started.wait()
        # A restarted worker sees the expired lease and safely reclaims it.
        now = datetime.now(timezone.utc) + LEASE + timedelta(seconds=1)
        assert await process_next(sessions, storage, Provider('rejected'), now=now)
        paused.release.set()
        assert await first
        async with sessions.begin() as session:
            current = await session.scalar(select(StoolPhoto).where(StoolPhoto.id == photo_id))
            assert current.review_attempt == 2
            assert current.review_status == 'rejected'
            assert current.policy_version == 'test-v1'
            assert current.review_sha256 is None
        await engine.dispose()
    asyncio.run(run())


def test_purge_while_review_is_in_flight_never_restores_approval():
    from sqlalchemy import select
    from app.workers.stool_photo_reviews import purge_deleted

    class PausedProvider:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def review(self, image):
            self.started.set()
            await self.release.wait()
            return ReviewOutcome('passed', (), 'test-v1')

    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as connection:
            await connection.run_sync(User.__table__.create)
            await connection.run_sync(StoolPhoto.__table__.create)
            await connection.run_sync(BabyProfile.__table__.create)
            await connection.run_sync(StoolAnalysis.__table__.create)
            await connection.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status='active'))
        storage = Storage()
        async with sessions.begin() as session:
            photo = await StoolPhotoService(
                repository=StoolPhotoRepository(session), storage=storage,
                idempotency=Idempotency(), audit=Audit(),
            ).upload(owner=owner, body=image_bytes(), key='concurrent-delete', request_id='r')
            photo_id = photo.id
        paused = PausedProvider()
        inflight = asyncio.create_task(process_next(sessions, storage, paused))
        await paused.started.wait()
        async with sessions.begin() as session:
            await StoolPhotoService(
                repository=StoolPhotoRepository(session), storage=storage,
                idempotency=Idempotency(), audit=Audit(),
            ).delete(owner=owner, photo_id=photo_id, request_id='r')
        assert await purge_deleted(sessions, storage)
        paused.release.set()
        assert await inflight
        async with sessions.begin() as session:
            photo = await session.scalar(select(StoolPhoto).where(StoolPhoto.id == photo_id))
            assert photo.review_status == 'failed' and photo.review_sha256 is None
            assert photo.purged_at is not None
        await engine.dispose()
    asyncio.run(run())


def test_worker_loop_survives_one_off_purge_failure(monkeypatch):
    from app.workers import stool_photo_reviews as worker

    calls = []

    async def purge(*args):
        calls.append('purge')
        if calls.count('purge') <= 2:
            raise OSError('storage temporarily unavailable')
        return False

    async def process(*args):
        calls.append('process')
        return False

    async def expire(*args):
        calls.append('expire')
        return False

    async def stop_after_second_iteration(*args):
        if calls.count('purge') == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(worker, 'purge_deleted', purge)
    monkeypatch.setattr(worker, 'expire_unlinked', expire)
    monkeypatch.setattr(worker, 'process_next', process)
    monkeypatch.setattr(worker.asyncio, 'sleep', stop_after_second_iteration)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run_loop(None, None, None))
    assert calls == ['purge', 'expire', 'process', 'purge', 'expire', 'process']


def test_failed_request_commit_compensates_uploaded_object():
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from app.infrastructure.db.session import get_session

    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status='active'))
        store = Storage()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_session_factory=sessions)))
        with pytest.raises(IntegrityError):
            async for session in get_session(request):
                service = StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                            idempotency=Idempotency(), audit=Audit())
                await service.upload(owner=owner, body=image_bytes(), key='once', request_id='r')
                assert len(store.values) == 1
                # Commit fails only after the upload has returned to the router.
                session.add(User(id=owner, status='active'))
        async with sessions.begin() as session:
            assert await session.scalar(select(StoolPhoto)) is None
        assert store.values == {}
        await engine.dispose()

    asyncio.run(run())


def test_partial_object_write_failure_is_compensated_after_request_rollback():
    from sqlalchemy import select
    from app.infrastructure.db.session import get_session

    class PartialWriteStorage(Storage):
        async def put_bytes(self, *, key, body, content_type):
            self.values[key] = body
            raise OSError('synthetic partial write')

    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status='active'))
        store = PartialWriteStorage()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_session_factory=sessions)))
        dependency = get_session(request)
        session = await dependency.__anext__()
        with pytest.raises(OSError, match='partial write'):
            await StoolPhotoService(repository=StoolPhotoRepository(session), storage=store,
                                    idempotency=Idempotency(), audit=Audit()).upload(
                owner=owner, body=image_bytes(), key='once', request_id='r')
        assert len(store.values) == 1
        # FastAPI throws the route exception back into its yield dependency.
        with pytest.raises(OSError, match='partial write'):
            await dependency.athrow(OSError('synthetic partial write'))
        async with sessions.begin() as session:
            assert await session.scalar(select(StoolPhoto)) is None
        assert store.values == {}
        await engine.dispose()

    asyncio.run(run())


def test_deleted_account_cannot_upload_new_private_photo():
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(StoolPhoto.__table__.create)
            await conn.run_sync(BabyProfile.__table__.create)
            await conn.run_sync(StoolAnalysis.__table__.create)
            await conn.run_sync(StoolAnalysisPhoto.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid4()
        async with sessions.begin() as session:
            session.add(User(id=owner, status='deleted'))
        storage = Storage()
        async with sessions.begin() as session:
            with pytest.raises(ApiError):
                await StoolPhotoService(repository=StoolPhotoRepository(session), storage=storage,
                                        idempotency=Idempotency(), audit=Audit()).upload(
                    owner=owner, body=image_bytes(), key='deleted-owner', request_id='r')
        assert storage.values == {}
        await engine.dispose()

    asyncio.run(run())


def test_photo_deletion_atomically_revokes_linked_analysis_result_and_history():
    from datetime import datetime, timezone
    from sqlalchemy import select

    from app.infrastructure.db.base import Base
    from app.modules.baby.profile_models import BabyProfile
    from app.modules.stool_analyses.events import StoolNonmedicalEventLedger
    from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisPhoto

    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        tables = [User.__table__, BabyProfile.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__]
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, baby, linked_id, unrelated_id, photo_id = (uuid4() for _ in range(5))
        now = datetime.now(timezone.utc)
        async with sessions.begin() as session:
            session.add(User(id=owner, status='active'))
            await session.flush()
            session.add(BabyProfile(id=baby, owner_user_id=owner, name='Synthetic'))
            await session.flush()
            session.add(StoolPhoto(
                id=photo_id, owner_user_id=owner, object_key=f'synthetic/{photo_id}',
                sha256='a' * 64, content_type='image/jpeg', size_bytes=1024,
                width=800, height=800, review_status='passed', reason_codes=[],
                review_sha256='a' * 64, review_expires_at=now,
            ))
            session.add_all([
                StoolAnalysis(id=linked_id, owner_user_id=owner, baby_id=baby,
                              thread_id=uuid4(), status='completed',
                              final_result={'synthetic': 'sensitive'},
                              result_schema_version='stool.result.v1',
                              result_validated_at=now, context_expires_at=now,
                              baby_context_snapshot_id=uuid4(), market='CN', language='zh',
                              baby_profile_version=1, baby_feeding_mode='mixed_feeding',
                              baby_birth_date=now.date(), context_captured_at=now),
                StoolAnalysis(id=unrelated_id, owner_user_id=owner, baby_id=baby,
                              thread_id=uuid4(), status='queued'),
            ])
            await session.flush()
            session.add(StoolAnalysisPhoto(analysis_id=linked_id, photo_id=photo_id,
                                           position=0, original_sha256='a' * 64))
        async with sessions.begin() as session:
            await StoolPhotoService(repository=StoolPhotoRepository(session), storage=Storage(),
                                    idempotency=Idempotency(), audit=Audit()).delete(
                owner=owner, photo_id=photo_id, request_id='synthetic',
            )
        async with sessions.begin() as session:
            linked = await session.get(StoolAnalysis, linked_id)
            unrelated = await session.get(StoolAnalysis, unrelated_id)
            assert linked.revoked_at is not None and linked.deleted_at is not None
            assert linked.final_result is None and linked.result_validated_at is None
            assert linked.baby_context_snapshot_id is None and linked.baby_birth_date is None
            assert linked.baby_feeding_mode is None and linked.market is None
            assert unrelated.revoked_at is None and unrelated.deleted_at is None
            assert (await session.get(StoolPhoto, photo_id)).deleted_at is not None
            with pytest.raises(ApiError):
                await StoolNonmedicalEventLedger(session).get_analysis(owner=owner, analysis_id=linked_id)
            assert await session.scalar(select(StoolAnalysis).where(
                StoolAnalysis.id == unrelated_id)) is not None
        await engine.dispose()
    asyncio.run(run())


def test_disabled_worker_fails_pending_without_reading_original():
    from app.modules.stool_photos.review import DisabledReviewProvider

    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for table in (User.__table__, StoolPhoto.__table__, BabyProfile.__table__,
                          StoolAnalysis.__table__, StoolAnalysisPhoto.__table__):
                await conn.run_sync(table.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner, storage = uuid4(), Storage()
        async with sessions.begin() as session:
            session.add(User(id=owner, status="active"))
        async with sessions.begin() as session:
            row = await StoolPhotoService(repository=StoolPhotoRepository(session), storage=storage,
                idempotency=Idempotency(), audit=Audit()).upload(
                    owner=owner, body=image_bytes(), key="disabled-review", request_id="test")
            photo_id, object_key = row.id, row.object_key

        class NoReadStorage:
            reads = 0

            async def get_bytes(self, *, key):
                self.reads += 1
                raise AssertionError("Disabled review must not read the original")

        private_storage = NoReadStorage()
        assert await process_next(sessions, private_storage, DisabledReviewProvider())
        assert private_storage.reads == 0
        async with sessions.begin() as session:
            photo = await session.get(StoolPhoto, photo_id)
            assert photo.review_status == "failed" and photo.reason_codes == ["review_unavailable"]
            assert photo.review_sha256 is None and photo.review_expires_at is None
        assert object_key in storage.values
        await engine.dispose()
    asyncio.run(run())
