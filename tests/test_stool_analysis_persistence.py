"""Persist synthetic final results; authorize every private image resolution."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import ApiError
from app.infrastructure.db.base import Base
from app.modules.auth.models import DeviceSession
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisPhoto
from app.modules.stool_analyses.service import StoolAnalysisContextResolver
from app.modules.stool_photos.models import StoolPhoto
from app.modules.users.models import User
from test_stool_photo_rules import image_bytes
from stool_result_fixture import valid_stool_result


class PrivateStorage:
    def __init__(self):
        self.data = {}
        self.reads = 0

    async def get_bytes(self, *, key):
        self.reads += 1
        return self.data[key]


async def fixture(sessions, storage):
    now = datetime.now(timezone.utc)
    owner, baby_id, thread_id, session_id, analysis_id, photo_id = (uuid4() for _ in range(6))
    body = image_bytes()
    digest = sha256(body).hexdigest()
    object_key = f"synthetic/stool/{photo_id}"
    storage.data[object_key] = body
    async with sessions.begin() as session:
        session.add(User(id=owner, status='active'))
        session.add(BabyProfile(id=baby_id, owner_user_id=owner, name='Synthetic infant'))
        session.add(DeviceSession(id=session_id, user_id=owner, status='active'))
        session.add(StoolPhoto(
            id=photo_id, owner_user_id=owner, object_key=object_key, sha256=digest,
            content_type='image/png', size_bytes=len(body), width=720, height=720,
            review_status='passed', reason_codes=[], review_attempt=1, review_sha256=digest,
            review_expires_at=now + timedelta(hours=1),
        ))
        session.add(StoolAnalysis(
            id=analysis_id, owner_user_id=owner, baby_id=baby_id, thread_id=thread_id,
            status='completed', result_schema_version='stool.result.v1',
            final_result=valid_stool_result(),
            result_validated_at=now, completed_at=now,
            context_expires_at=now + timedelta(minutes=30),
        ))
        session.add(StoolAnalysisPhoto(
            analysis_id=analysis_id, photo_id=photo_id, position=0, original_sha256=digest,
        ))
    return owner, baby_id, thread_id, session_id, analysis_id, photo_id, now


def resolve(session, storage, ids, **overrides):
    owner, baby, thread, device, analysis, _, now = ids
    args = dict(actor_user_id=owner, baby_id=baby, thread_id=thread, session_id=device,
                analysis_id=analysis, token_expires_at=now + timedelta(minutes=10), now=now)
    args.update(overrides)
    return StoolAnalysisContextResolver(session=session, storage=storage).resolve(**args)


def test_synthetic_result_survives_database_restart_and_images_remain_private(tmp_path):
    async def run():
        url = f"sqlite+aiosqlite:///{tmp_path / 'synthetic.db'}"
        engine = create_async_engine(url)
        tables = [User.__table__, BabyProfile.__table__, DeviceSession.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__]
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = PrivateStorage()
        ids = await fixture(sessions, store)
        await engine.dispose()
        reopened = create_async_engine(url)
        sessions = async_sessionmaker(reopened, expire_on_commit=False)
        async with sessions.begin() as session:
            analysis = await session.get(StoolAnalysis, ids[4])
            assert analysis.status == 'completed'
            assert analysis.final_result['risk_level'] == 'routine'
            assert not hasattr(analysis, 'image_bytes')
            assert not hasattr(analysis, 'object_key')
            material = await resolve(session, store, ids)
            assert material.result == valid_stool_result()
            assert len(material.images) == 1 and material.images[0].startswith(b'\xff\xd8')
        assert store.reads == 1
        await reopened.dispose()
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    'owner', 'baby', 'thread', 'device', 'token_expired', 'device_revoked', 'account_deleted',
    'baby_deleted', 'analysis_running', 'result_revoked',
    'photo_deleted', 'photo_expiry_missing', 'photo_hash_mismatch', 'oversized_result',
])
def test_each_resolution_denies_revoked_or_cross_scope_data_before_read(tmp_path, change):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'deny.db'}")
        tables = [User.__table__, BabyProfile.__table__, DeviceSession.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__]
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = PrivateStorage()
        ids = await fixture(sessions, store)
        override = {}
        async with sessions.begin() as session:
            if change == 'owner':
                override['actor_user_id'] = uuid4()
            elif change == 'baby':
                override['baby_id'] = uuid4()
            elif change == 'thread':
                override['thread_id'] = uuid4()
            elif change == 'device':
                override['session_id'] = uuid4()
            elif change == 'token_expired':
                override['token_expires_at'] = ids[-1] - timedelta(seconds=1)
            elif change == 'device_revoked':
                (await session.get(DeviceSession, ids[3])).status = 'revoked'
            elif change == 'account_deleted':
                (await session.get(User, ids[0])).status = 'deleted'
            elif change == 'baby_deleted':
                (await session.get(BabyProfile, ids[1])).deleted_at = ids[-1]
            elif change == 'analysis_running':
                (await session.get(StoolAnalysis, ids[4])).status = 'running'
            elif change == 'result_revoked':
                (await session.get(StoolAnalysis, ids[4])).revoked_at = ids[-1]
            elif change == 'photo_deleted':
                (await session.get(StoolPhoto, ids[5])).deleted_at = ids[-1]
            elif change == 'photo_expiry_missing':
                (await session.get(StoolPhoto, ids[5])).review_expires_at = None
            elif change == 'photo_hash_mismatch':
                (await session.get(StoolPhoto, ids[5])).sha256 = 'changed'
            elif change == 'oversized_result':
                (await session.get(StoolAnalysis, ids[4])).final_result = {'note': 'x' * 20_000}
        async with sessions.begin() as session:
            with pytest.raises(ApiError) as error:
                await resolve(session, store, ids, **override)
            assert error.value.code == 'stool_analysis_context_unavailable'
            assert 'object_key' not in error.value.message
        assert store.reads == 0
        await engine.dispose()
    asyncio.run(run())


def test_tampered_object_fails_closed_even_after_database_authorization(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tampered.db'}")
        tables = [User.__table__, BabyProfile.__table__, DeviceSession.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__]
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = PrivateStorage()
        ids = await fixture(sessions, store)
        key = next(iter(store.data))
        store.data[key] = b'tampered'
        async with sessions.begin() as session:
            with pytest.raises(ApiError):
                await resolve(session, store, ids)
        assert store.reads == 1
        await engine.dispose()
    asyncio.run(run())


def test_followup_rechecks_live_session_and_result_on_each_call(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recheck.db'}")
        tables = [User.__table__, BabyProfile.__table__, DeviceSession.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__]
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = PrivateStorage()
        ids = await fixture(sessions, storage)
        async with sessions.begin() as session:
            first = await resolve(session, storage, ids)
            assert first.result == valid_stool_result()
        assert storage.reads == 1
        async with sessions.begin() as session:
            (await session.get(DeviceSession, ids[3])).status = 'revoked'
        async with sessions.begin() as session:
            with pytest.raises(ApiError):
                await resolve(session, storage, ids)
        assert storage.reads == 1
        async with sessions.begin() as session:
            (await session.get(DeviceSession, ids[3])).status = 'active'
            (await session.get(StoolAnalysis, ids[4])).revoked_at = ids[-1]
        async with sessions.begin() as session:
            with pytest.raises(ApiError):
                await resolve(session, storage, ids)
        assert storage.reads == 1
        await engine.dispose()
    asyncio.run(run())


def test_persisted_completed_followup_ignores_expired_submission_review(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'historical-review.db'}")
        tables = [User.__table__, BabyProfile.__table__, DeviceSession.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = PrivateStorage()
        ids = await fixture(sessions, storage)
        async with sessions.begin() as session:
            (await session.get(StoolPhoto, ids[5])).review_expires_at = ids[-1] - timedelta(days=1)
        async with sessions.begin() as session:
            context = await resolve(session, storage, ids)
            assert context.result == valid_stool_result()
            assert len(context.images) == 1
        assert storage.reads == 1
        await engine.dispose()
    asyncio.run(run())
