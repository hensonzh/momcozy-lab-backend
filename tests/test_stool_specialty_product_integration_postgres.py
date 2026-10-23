"""One isolated Product PostgreSQL/MinIO store talks to a DB-free specialty ASGI service."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.modules.audit import AuditService, IdempotencyService
from app.modules.audit.repository import AuditRepository
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.dispatch_pump import SyntheticStoolDispatchPump
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisEvent
from app.modules.stool_analyses.submission import SyntheticStoolSubmission
from app.modules.stool_analyses.transport_client import SpecialtyClient
from app.modules.stool_photos.models import StoolPhoto
from app.modules.stool_photos.rules import validate_original
from app.modules.users.models import User
from services.stool_specialty.app import create_app
from services.stool_specialty.contracts import PhotoObservation, StoolAnalysisResult
from services.stool_specialty.settings import SpecialtySettings
from test_stool_photo_postgres_integration import DATABASE_URL, ENDPOINT, isolated_storage_and_database
from test_stool_photo_rules import image_bytes
from test_stool_specialty_client import SECRET

import pytest

pytestmark = pytest.mark.skipif(not DATABASE_URL or not ENDPOINT, reason="Requires isolated PostgreSQL and MinIO test profile.")


class Analyzer:
    async def analyze(self, _payload):
        return StoolAnalysisResult(
            assessment_status="assessed", color="yellow", consistency="mushy",
            findings=["none"], risk_level="routine", summary="Common yellow mushy appearance.",
            care_advice="Continue routine observation.",
            photo_observations=[PhotoObservation(position=0, image_quality="adequate",
                color="yellow", consistency="mushy", findings=["none"], note="Clear image.")],
        )


def test_product_owns_durable_task_and_events_after_independent_stateless_stream():
    async def run():
        async with isolated_storage_and_database() as (sessions, storage):
            owner, baby, thread, photo_id = (uuid4() for _ in range(4))
            body = image_bytes()
            metadata, digest = validate_original(body), sha256(body).hexdigest()
            key = f"synthetic/stool/{photo_id}"
            await storage.put_bytes(key=key, body=body, content_type=metadata.content_type)
            now = datetime.now(timezone.utc)
            async with sessions.begin() as session:
                session.add(User(id=owner, status="active"))
                await session.flush()
                session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic infant", birth_date=(datetime.now(timezone.utc).date() - timedelta(days=90))))
                await session.flush()
                session.add(StoolPhoto(
                    id=photo_id, owner_user_id=owner, object_key=key, sha256=digest,
                    content_type=metadata.content_type, size_bytes=len(body),
                    width=metadata.width, height=metadata.height,
                    review_status="passed", reason_codes=[], review_attempt=1,
                    review_sha256=digest, review_expires_at=now + timedelta(days=180),
                ))
            async with sessions.begin() as session:
                audit = AuditRepository(session)
                analysis = await SyntheticStoolSubmission(
                    session=session, storage=storage,
                    idempotency=IdempotencyService(repository=audit),
                    audit=AuditService(repository=audit),
                ).prepare(
                    owner=owner, baby_id=baby, trusted_thread_id=thread,
                    photo_ids=[photo_id], idempotency_key="independent-stream",
                    request_id="synthetic-only", now=now, trusted_market="US", language="en",
                )
            service = create_app(SpecialtySettings(
                app_env="test", market="US", service_shared_secret=SECRET,
                openai_api_key="test-key",
            ), analyzer=Analyzer())
            assert not hasattr(service.state, "sessions")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service),
                                         base_url="https://specialty.test") as http:
                pump = SyntheticStoolDispatchPump(
                    sessions=sessions, storage=storage, market="US",
                    client=SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET),
                )
                assert await pump.run_once(now=now)
            async with sessions.begin() as session:
                stored = await session.get(StoolAnalysis, analysis.id)
                events = list((await session.scalars(select(StoolAnalysisEvent).where(
                    StoolAnalysisEvent.analysis_id == analysis.id,
                ).order_by(StoolAnalysisEvent.sequence))).all())
                assert stored.status == "completed" and stored.run_attempt == 1
                assert stored.final_result["risk_level"] == "routine"
                assert [(e.sequence, e.event_type) for e in events] == [
                    (1, "stool.card.created"), (2, "stool.analysis.started"),
                    (3, "stool.field.ready"), (4, "stool.field.ready"),
                    (5, "stool.field.ready"), (6, "stool.field.ready"),
                    (7, "stool.field.ready"), (8, "stool.field.ready"),
                    (9, "stool.analysis.completed"),
                ]
    asyncio.run(run())


def test_delete_committed_during_input_read_prevents_outbound_image():
    from app.modules.stool_photos.repository import StoolPhotoRepository
    from app.modules.stool_photos.service import StoolPhotoService

    async def run():
        async with isolated_storage_and_database() as (sessions, storage):
            owner, baby, thread, photo_id = (uuid4() for _ in range(4))
            body = image_bytes()
            metadata, digest = validate_original(body), sha256(body).hexdigest()
            key = f"synthetic/stool/{photo_id}"
            await storage.put_bytes(key=key, body=body, content_type=metadata.content_type)
            now = datetime.now(timezone.utc)
            async with sessions.begin() as session:
                session.add(User(id=owner, status="active"))
                await session.flush()
                session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic infant", birth_date=(datetime.now(timezone.utc).date() - timedelta(days=90))))
                await session.flush()
                session.add(StoolPhoto(id=photo_id, owner_user_id=owner, object_key=key, sha256=digest,
                    content_type=metadata.content_type, size_bytes=len(body), width=metadata.width,
                    height=metadata.height, review_status="passed", reason_codes=[], review_attempt=1,
                    review_sha256=digest, review_expires_at=now + timedelta(days=180)))
            async with sessions.begin() as session:
                audit = AuditRepository(session)
                analysis = await SyntheticStoolSubmission(session=session, storage=storage,
                    idempotency=IdempotencyService(repository=audit), audit=AuditService(repository=audit)).prepare(
                    owner=owner, baby_id=baby, trusted_thread_id=thread, photo_ids=[photo_id],
                    idempotency_key="delete-inflight", request_id="synthetic-only", now=now,
                    trusted_market="US", language="en")
            started, release = asyncio.Event(), asyncio.Event()

            class PausedStorage:
                async def get_bytes(self, *, key):
                    started.set()
                    await release.wait()
                    return await storage.get_bytes(key=key)

            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(500)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                pump = SyntheticStoolDispatchPump(sessions=sessions, storage=PausedStorage(), market="US",
                    client=SpecialtyClient(http, origin="https://specialty.test", market="US", secret=SECRET))
                task = asyncio.create_task(pump.run_once(now=now))
                await asyncio.wait_for(started.wait(), 5)
                async with sessions.begin() as session:
                    audit = AuditRepository(session)
                    await StoolPhotoService(repository=StoolPhotoRepository(session), storage=storage,
                        idempotency=IdempotencyService(repository=audit), audit=AuditService(repository=audit)).delete(
                        owner=owner, photo_id=photo_id, request_id="synthetic-erase")
                release.set()
                assert await asyncio.wait_for(task, 5)
            assert not calls
            async with sessions.begin() as session:
                row = await session.get(StoolAnalysis, analysis.id)
                assert row.deleted_at is not None and row.revoked_at is not None
                assert row.final_result is None
    asyncio.run(run())
