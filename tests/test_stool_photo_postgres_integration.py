"""Private, synthetic-image PostgreSQL + MinIO review and erasure verification."""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import ApiError
from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models  # noqa: F401 - register every table
from app.infrastructure.object_storage import S3ObjectStorage
from app.modules.audit import AuditService, IdempotencyService
from app.modules.audit.repository import AuditRepository
from app.modules.auth.repository import AuthAccountRepository
from app.modules.stool_photos.models import StoolPhoto
from app.modules.stool_photos.repository import StoolPhotoRepository
from app.modules.stool_photos.review import ReviewOutcome, require_approved_photos
from app.modules.stool_photos.service import StoolPhotoService
from app.modules.users.models import User
from app.workers.stool_photo_reviews import LEASE, process_next, purge_deleted
from test_stool_photo_rules import image_bytes

DATABASE_URL = os.getenv('MOMCOZY_TEST_DATABASE_URL', '')
ENDPOINT = os.getenv('MOMCOZY_TEST_OBJECT_STORAGE_ENDPOINT_URL', '')
pytestmark = pytest.mark.skipif(not DATABASE_URL or not ENDPOINT, reason='Requires isolated PostgreSQL and MinIO test profile.')


@asynccontextmanager
async def isolated_storage_and_database():
    schema = f'stool_review_{uuid4().hex}'
    bucket = f'stool-review-{uuid4().hex}'
    admin = create_async_engine(DATABASE_URL)
    storage = S3ObjectStorage(bucket=bucket, endpoint_url=ENDPOINT,
        access_key_id=os.getenv('MOMCOZY_TEST_OBJECT_STORAGE_ACCESS_KEY_ID', 'minioadmin'),
        secret_access_key=os.getenv('MOMCOZY_TEST_OBJECT_STORAGE_SECRET_ACCESS_KEY', 'minioadmin'))
    engine = None
    schema_created = bucket_created = False
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        await asyncio.to_thread(storage.client.create_bucket, Bucket=bucket)
        bucket_created = True
        engine = create_async_engine(DATABASE_URL, connect_args={'server_settings': {'search_path': schema}})
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False), storage
    finally:
        if engine is not None:
            await engine.dispose()
        if bucket_created:
            # Only the unique bucket created by this test may be inspected/deleted.
            listing = await asyncio.to_thread(storage.client.list_objects_v2, Bucket=bucket)
            for item in listing.get('Contents', []):
                await storage.delete(key=item['Key'])
            await asyncio.to_thread(storage.client.delete_bucket, Bucket=bucket)
        if schema_created:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def service(session, storage):
    audit_repo = AuditRepository(session)
    return StoolPhotoService(repository=StoolPhotoRepository(session), storage=storage,
                             idempotency=IdempotencyService(repository=audit_repo),
                             audit=AuditService(repository=audit_repo))


class OutcomeProvider:
    def __init__(self, status):
        self.status = status

    async def review(self, image):
        return ReviewOutcome(self.status, () if self.status == 'passed' else ('not_stool',), 'synthetic-test-v1')


async def create_photo(sessions, storage, owner, key):
    async with sessions.begin() as session:
        photo = await service(session, storage).upload(owner=owner, body=image_bytes(), key=key, request_id='synthetic-test')
        return photo.id, photo.object_key


def test_postgres_minio_worker_restart_skip_locked_and_owner_isolation():
    async def scenario():
        async with isolated_storage_and_database() as (sessions, storage):
            first_owner, second_owner = uuid4(), uuid4()
            async with sessions.begin() as session:
                session.add_all([User(id=first_owner), User(id=second_owner)])
            first_id, first_key = await create_photo(sessions, storage, first_owner, 'first')
            second_id, _ = await create_photo(sessions, storage, second_owner, 'second')
            assert await storage.get_bytes(key=first_key) == image_bytes()

            class PausedProvider(OutcomeProvider):
                def __init__(self):
                    super().__init__('passed')
                    self.started, self.release = asyncio.Event(), asyncio.Event()

                async def review(self, image):
                    self.started.set()
                    await self.release.wait()
                    return await super().review(image)

            paused = PausedProvider()
            old_worker = asyncio.create_task(process_next(sessions, storage, paused))
            await asyncio.wait_for(paused.started.wait(), timeout=10)
            # The first claimed row is reviewing; a second worker takes another row.
            assert await asyncio.wait_for(process_next(sessions, storage, OutcomeProvider('rejected')), timeout=10)
            async with sessions.begin() as session:
                first = await session.get(StoolPhoto, first_id)
                second = await session.get(StoolPhoto, second_id)
                assert first.review_status == 'reviewing'
                assert second.review_status == 'rejected'
                assert await StoolPhotoRepository(session).get_for_owner(second_id, first_owner) is None

            # An expired lease is reclaimed, and the old worker cannot overwrite its result.
            assert await process_next(sessions, storage, OutcomeProvider('rejected'),
                                      now=datetime.now(timezone.utc) + LEASE + timedelta(seconds=1))
            paused.release.set()
            await asyncio.wait_for(old_worker, timeout=10)
            async with sessions.begin() as session:
                current = await session.get(StoolPhoto, first_id)
                assert current.review_attempt == 2 and current.review_status == 'rejected'
                assert current.review_sha256 is None

    asyncio.run(scenario())


def test_upload_and_account_deletion_serialize_on_postgres_user_row():
    async def scenario():
        async with isolated_storage_and_database() as (sessions, storage):
            owner = uuid4()
            async with sessions.begin() as session:
                session.add(User(id=owner))
            deletion_started, release_deletion = asyncio.Event(), asyncio.Event()

            async def delete_account():
                async with sessions.begin() as session:
                    account = AuthAccountRepository(session)
                    user = await account.lock_user(owner)
                    deletion_started.set()
                    await release_deletion.wait()
                    await account.anonymize_user(user=user, marker='synthetic', now=datetime.now(timezone.utc))

            deleting = asyncio.create_task(delete_account())
            await asyncio.wait_for(deletion_started.wait(), timeout=10)

            async def upload():
                async with sessions.begin() as session:
                    return await service(session, storage).upload(owner=owner, body=image_bytes(),
                                                                 key='racing', request_id='synthetic-test')

            uploading = asyncio.create_task(upload())
            await asyncio.sleep(0.1)
            assert not uploading.done()  # User row lock blocks the upload until deletion commits.
            release_deletion.set()
            await asyncio.wait_for(deleting, timeout=10)
            with pytest.raises(ApiError) as error:
                await asyncio.wait_for(uploading, timeout=10)
            assert error.value.status == 403
            async with sessions.begin() as session:
                assert await session.scalar(select(StoolPhoto)) is None

    asyncio.run(scenario())


def test_postgres_minio_account_deletion_and_failed_purge_retry():
    async def scenario():
        async with isolated_storage_and_database() as (sessions, storage):
            owner, other_owner = uuid4(), uuid4()
            async with sessions.begin() as session:
                session.add_all([User(id=owner), User(id=other_owner)])
            photo_id, key = await create_photo(sessions, storage, owner, 'account-delete')
            other_id, other_key = await create_photo(sessions, storage, other_owner, 'unrelated')
            assert await process_next(sessions, storage, OutcomeProvider('passed'))
            async with sessions.begin() as session:
                photo = await session.get(StoolPhoto, photo_id)
                require_approved_photos([photo], owner)
                user = await AuthAccountRepository(session).lock_user(owner)
                assert user is not None
                await AuthAccountRepository(session).anonymize_user(user=user, marker='synthetic',
                                                                  now=datetime.now(timezone.utc))
            async with sessions.begin() as session:
                photo = await session.get(StoolPhoto, photo_id)
                assert photo.deleted_at is not None and photo.review_sha256 is None
                with pytest.raises(ApiError) as error:
                    require_approved_photos([photo], owner)
                assert error.value.status == 409
                assert (await session.get(StoolPhoto, other_id)).deleted_at is None

            class FlakyDelete:
                def __init__(self):
                    self.fails = 1

                async def delete(self, *, key):
                    if self.fails:
                        self.fails -= 1
                        raise OSError('synthetic storage outage')
                    await storage.delete(key=key)

            with pytest.raises(OSError):
                await purge_deleted(sessions, FlakyDelete())
            async with sessions.begin() as session:
                assert (await session.get(StoolPhoto, photo_id)).purged_at is None
            assert await storage.get_bytes(key=key) == image_bytes()
            assert await purge_deleted(sessions, storage)
            async with sessions.begin() as session:
                assert (await session.get(StoolPhoto, photo_id)).purged_at is not None
            with pytest.raises(ClientError):
                await storage.get_bytes(key=key)
            assert await storage.get_bytes(key=other_key) == image_bytes()

    asyncio.run(scenario())


def test_postgres_minio_synthetic_result_is_persistent_and_rechecked_per_call():
    from app.modules.auth.models import DeviceSession
    from app.modules.baby.profile_models import BabyProfile
    from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisPhoto
    from app.modules.stool_analyses.service import StoolAnalysisContextResolver

    class CountingStorage:
        def __init__(self, inner):
            self.inner = inner
            self.reads = 0

        async def get_bytes(self, *, key):
            self.reads += 1
            return await self.inner.get_bytes(key=key)

    async def scenario():
        async with isolated_storage_and_database() as (sessions, storage):
            owner, baby_id, thread_id, device_id, analysis_id = (uuid4() for _ in range(5))
            now = datetime.now(timezone.utc)
            async with sessions.begin() as session:
                session.add(User(id=owner))
                await session.flush()
                session.add(BabyProfile(id=baby_id, owner_user_id=owner, name='Synthetic infant'))
                session.add(DeviceSession(id=device_id, user_id=owner, status='active'))
            photo_id, _ = await create_photo(sessions, storage, owner, 'synthetic-result')
            assert await process_next(sessions, storage, OutcomeProvider('passed'))
            async with sessions.begin() as session:
                photo = await session.get(StoolPhoto, photo_id)
                session.add(StoolAnalysis(
                    id=analysis_id, owner_user_id=owner, baby_id=baby_id, thread_id=thread_id,
                    status='completed', result_schema_version='stool.result.v1',
                    final_result={'color': 'synthetic'}, result_validated_at=now,
                    completed_at=now, context_expires_at=now + timedelta(minutes=30),
                ))
                await session.flush()
                session.add(StoolAnalysisPhoto(
                    analysis_id=analysis_id, photo_id=photo_id, position=0, original_sha256=photo.sha256,
                ))
            counting = CountingStorage(storage)
            async with sessions.begin() as session:
                resolved = await StoolAnalysisContextResolver(session=session, storage=counting).resolve(
                    actor_user_id=owner, baby_id=baby_id, thread_id=thread_id, session_id=device_id,
                    analysis_id=analysis_id, token_expires_at=now + timedelta(minutes=10), now=now,
                )
                assert resolved.result == {'color': 'synthetic'}
                assert len(resolved.images) == 1 and resolved.images[0].startswith(b'\xff\xd8')
            assert counting.reads == 1
            async with sessions.begin() as session:
                (await session.get(StoolPhoto, photo_id)).review_expires_at = now - timedelta(seconds=1)
            async with sessions.begin() as session:
                historical = await StoolAnalysisContextResolver(session=session, storage=counting).resolve(
                    actor_user_id=owner, baby_id=baby_id, thread_id=thread_id, session_id=device_id,
                    analysis_id=analysis_id, token_expires_at=now + timedelta(minutes=10), now=now,
                )
                assert historical.result == {'color': 'synthetic'}
            assert counting.reads == 2
            async with sessions.begin() as session:
                (await session.get(DeviceSession, device_id)).status = 'revoked'
            async with sessions.begin() as session:
                with pytest.raises(ApiError):
                    await StoolAnalysisContextResolver(session=session, storage=counting).resolve(
                        actor_user_id=owner, baby_id=baby_id, thread_id=thread_id, session_id=device_id,
                        analysis_id=analysis_id, token_expires_at=now + timedelta(minutes=10), now=now,
                    )
            assert counting.reads == 2
            async with sessions.begin() as session:
                account = await AuthAccountRepository(session).get_user(user_id=owner)
                assert account is not None
                await AuthAccountRepository(session).anonymize_user(
                    user=account, marker='synthetic-erasure', now=now,
                )
            async with sessions.begin() as session:
                persisted = await session.get(StoolAnalysis, analysis_id)
                assert persisted is not None
                assert persisted.final_result is None
                assert persisted.result_validated_at is None
                assert persisted.revoked_at is not None
                with pytest.raises(ApiError):
                    await StoolAnalysisContextResolver(session=session, storage=counting).resolve(
                        actor_user_id=owner, baby_id=baby_id, thread_id=thread_id, session_id=device_id,
                        analysis_id=analysis_id, token_expires_at=now + timedelta(minutes=10), now=now,
                    )
            assert counting.reads == 2

    asyncio.run(scenario())


def test_postgres_old_original_survives_until_explicit_delete():
    async def scenario():
        async with isolated_storage_and_database() as (sessions, storage):
            owner = uuid4()
            async with sessions.begin() as session:
                session.add(User(id=owner))
            photo_id, key = await create_photo(sessions, storage, owner, 'long-lived-original')
            async with sessions.begin() as session:
                (await session.get(StoolPhoto, photo_id)).created_at = datetime.now(timezone.utc) - timedelta(days=181)
            assert await process_next(sessions, storage, OutcomeProvider('passed'))
            async with sessions.begin() as session:
                photo = await session.get(StoolPhoto, photo_id)
                assert photo.review_expires_at - photo.reviewed_at == timedelta(days=180)
            assert not await purge_deleted(sessions, storage)
            assert await storage.get_bytes(key=key) == image_bytes()
            async with sessions.begin() as session:
                await service(session, storage).delete(owner=owner, photo_id=photo_id, request_id='synthetic-test')
            assert await purge_deleted(sessions, storage)
            with pytest.raises(ClientError):
                await storage.get_bytes(key=key)

    asyncio.run(scenario())
