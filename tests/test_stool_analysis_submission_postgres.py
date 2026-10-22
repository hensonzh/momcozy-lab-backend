"""Isolated PostgreSQL + MinIO synthetic submission/idempotency boundary."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.errors import ApiError
from app.modules.audit import AuditService, IdempotencyService
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditRepository
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisEvent, StoolAnalysisPhoto
from app.modules.stool_analyses.submission import SyntheticStoolSubmission
from app.modules.stool_photos.models import StoolPhoto
from app.modules.stool_photos.rules import validate_original
from app.modules.users.models import User
from test_stool_photo_postgres_integration import DATABASE_URL, ENDPOINT, isolated_storage_and_database
from test_stool_photo_rules import image_bytes

pytestmark = pytest.mark.skipif(not DATABASE_URL or not ENDPOINT, reason="Requires isolated PostgreSQL and MinIO test profile.")


def test_postgres_minio_concurrent_prepare_is_one_synthetic_task_and_card():
    async def run():
        async with isolated_storage_and_database() as (sessions, storage):
            owner, baby, thread, photo_id = (uuid4() for _ in range(4))
            body = image_bytes()
            metadata = validate_original(body)
            digest = sha256(body).hexdigest()
            object_key = f"synthetic/stool/{photo_id}"
            await storage.put_bytes(key=object_key, body=body, content_type=metadata.content_type)
            async with sessions.begin() as session:
                session.add(User(id=owner, status="active"))
                await session.flush()
                session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic infant"))
                await session.flush()
                session.add(StoolPhoto(
                    id=photo_id, owner_user_id=owner, object_key=object_key, sha256=digest,
                    content_type=metadata.content_type, size_bytes=len(body),
                    width=metadata.width, height=metadata.height,
                    review_status="passed", reason_codes=[], review_attempt=1,
                    review_sha256=digest,
                    review_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                ))

            async def prepare(key, photos):
                async with sessions.begin() as session:
                    audit_repo = AuditRepository(session)
                    return await SyntheticStoolSubmission(
                        session=session, storage=storage,
                        idempotency=IdempotencyService(repository=audit_repo),
                        audit=AuditService(repository=audit_repo),
                    ).prepare(
                        owner=owner, baby_id=baby, trusted_thread_id=thread,
                        photo_ids=photos, idempotency_key=key, request_id="synthetic-test",
                        trusted_market="CN", language="zh",
                    )

            first, second = await asyncio.gather(
                prepare("same-key", [photo_id]), prepare("same-key", [photo_id]),
            )
            assert first.id == second.id
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 1
                assert await session.scalar(select(func.count()).select_from(StoolAnalysisPhoto)) == 1
                assert await session.scalar(select(func.count()).select_from(StoolAnalysisEvent)) == 1
                assert await session.scalar(select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == "stool_analysis.prepare_synthetic",
                )) == 1
                event = await session.scalar(select(StoolAnalysisEvent))
                assert event.event_type == "stool.card.created" and event.sequence == 1
            with pytest.raises(ApiError) as error:
                await prepare("same-key", [photo_id, photo_id])
            assert error.value.code == "stool_photos_invalid_selection"
            async with sessions.begin() as session:
                (await session.get(StoolPhoto, photo_id)).review_status = "inconclusive"
            with pytest.raises(ApiError):
                await prepare("another-key", [photo_id])
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 1

    asyncio.run(run())


def test_postgres_concurrent_dispatch_claim_has_one_leased_intent():
    from app.modules.stool_analyses.dispatch import StoolDispatchQueue

    async def run():
        async with isolated_storage_and_database() as (sessions, storage):
            owner, baby, thread, photo_id = (uuid4() for _ in range(4))
            body = image_bytes()
            metadata = validate_original(body)
            digest = sha256(body).hexdigest()
            object_key = f"synthetic/stool/{photo_id}"
            await storage.put_bytes(key=object_key, body=body, content_type=metadata.content_type)
            now = datetime.now(timezone.utc)
            async with sessions.begin() as session:
                session.add(User(id=owner, status="active"))
                await session.flush()
                session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic infant"))
                await session.flush()
                session.add(StoolPhoto(
                    id=photo_id, owner_user_id=owner, object_key=object_key, sha256=digest,
                    content_type=metadata.content_type, size_bytes=len(body),
                    width=metadata.width, height=metadata.height,
                    review_status="passed", reason_codes=[], review_attempt=1,
                    review_sha256=digest, review_expires_at=now + timedelta(hours=1),
                ))
            async with sessions.begin() as session:
                audit = AuditRepository(session)
                analysis = await SyntheticStoolSubmission(
                    session=session, storage=storage,
                    idempotency=IdempotencyService(repository=audit), audit=AuditService(repository=audit),
                ).prepare(owner=owner, baby_id=baby, trusted_thread_id=thread,
                          photo_ids=[photo_id], idempotency_key="claim", request_id="synthetic", now=now,
                          trusted_market="CN", language="zh")

            async def claim():
                async with sessions.begin() as session:
                    return await StoolDispatchQueue(session, market="CN").claim(now=now, lease=timedelta(seconds=30))

            leases = await asyncio.gather(claim(), claim())
            assert [(lease.analysis_id, lease.attempt) for lease in leases if lease] == [(analysis.id, 1)]
            async with sessions.begin() as session:
                row = await session.get(StoolAnalysis, analysis.id)
                assert row.status == "running" and row.run_attempt == 1

    asyncio.run(run())
