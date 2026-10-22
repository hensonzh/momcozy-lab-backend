"""Default-off clinical service still honors owner-scoped explicit deletion."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Request

from app.api.dependencies import get_object_storage, require_current_user
from app.core.settings import Settings
from app.factory import create_app
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisPhoto
from app.modules.stool_photos.models import StoolPhoto
from app.modules.stool_photos.rules import validate_original
from app.modules.users.models import User
from app.workers.stool_photo_reviews import purge_deleted
from test_stool_photo_postgres_integration import DATABASE_URL, ENDPOINT, isolated_storage_and_database
from test_stool_photo_rules import image_bytes

pytestmark = pytest.mark.skipif(not DATABASE_URL or not ENDPOINT, reason="Requires isolated PostgreSQL and MinIO test profile.")


def test_explicit_analysis_deletion_scrubs_result_and_shared_original_even_when_analysis_closed():
    async def run():
        async with isolated_storage_and_database() as (sessions, storage):
            owner, other, baby, shared, second, unrelated, shared_photo, unrelated_photo = (uuid4() for _ in range(8))
            body = image_bytes()
            meta = validate_original(body)
            digest = sha256(body).hexdigest()
            now = datetime.now(timezone.utc)
            for photo_id in (shared_photo, unrelated_photo):
                await storage.put_bytes(key=f"synthetic/delete/{photo_id}", body=body, content_type=meta.content_type)
            async with sessions.begin() as session:
                session.add_all([User(id=owner, status="active"), User(id=other, status="active")])
                await session.flush()
                session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic"))
                await session.flush()
                for photo_id in (shared_photo, unrelated_photo):
                    session.add(StoolPhoto(id=photo_id, owner_user_id=owner,
                        object_key=f"synthetic/delete/{photo_id}", sha256=digest,
                        content_type=meta.content_type, size_bytes=len(body), width=meta.width,
                        height=meta.height, review_status="passed", reason_codes=[],
                        review_attempt=1, review_sha256=digest,
                        review_expires_at=now + timedelta(days=1)))
                for analysis_id in (shared, second, unrelated):
                    session.add(StoolAnalysis(id=analysis_id, owner_user_id=owner, baby_id=baby,
                        thread_id=uuid4(), status="completed", result_schema_version="stool.result.v1",
                        final_result={"synthetic": "sensitive"}, result_validated_at=now,
                        context_expires_at=now + timedelta(days=1),
                        baby_context_snapshot_id=uuid4(), market="CN", language="zh",
                        baby_profile_version=1, baby_birth_date=now.date(),
                        baby_feeding_mode="unknown", context_captured_at=now))
                await session.flush()
                session.add_all([
                    StoolAnalysisPhoto(analysis_id=shared, photo_id=shared_photo, position=0, original_sha256=digest),
                    StoolAnalysisPhoto(analysis_id=second, photo_id=shared_photo, position=0, original_sha256=digest),
                    StoolAnalysisPhoto(analysis_id=unrelated, photo_id=unrelated_photo, position=0, original_sha256=digest),
                ])
            app = create_app(Settings(app_env="test"))
            app.state.db_session_factory = sessions
            app.dependency_overrides[get_object_storage] = lambda: storage
            async def principal(request: Request):
                return SimpleNamespace(user_id=UUID(request.headers["X-Test-Owner"]))
            app.dependency_overrides[require_current_user] = principal
            path = f"/v1/stool-analyses/{shared}"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                assert (await client.delete(path, headers={"X-Test-Owner": str(other)})).status_code == 409
                assert (await client.delete(path, headers={"X-Test-Owner": str(owner)})).status_code == 204
                assert (await client.delete(path, headers={"X-Test-Owner": str(owner)})).status_code == 204
            async with sessions.begin() as session:
                for analysis_id in (shared, second):
                    analysis = await session.get(StoolAnalysis, analysis_id)
                    assert analysis.deleted_at is not None and analysis.revoked_at is not None
                    assert analysis.final_result is None and analysis.result_validated_at is None
                    assert analysis.baby_context_snapshot_id is None and analysis.baby_birth_date is None
                    assert analysis.baby_feeding_mode is None and analysis.market is None
                unrelated_row = await session.get(StoolAnalysis, unrelated)
                assert unrelated_row.final_result == {"synthetic": "sensitive"}
                assert unrelated_row.baby_context_snapshot_id is not None
                assert (await session.get(StoolPhoto, shared_photo)).deleted_at is not None
                assert (await session.get(StoolPhoto, unrelated_photo)).deleted_at is None
            assert await purge_deleted(sessions, storage)
            assert not await purge_deleted(sessions, storage)
    asyncio.run(run())
