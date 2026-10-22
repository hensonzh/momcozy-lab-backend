"""HTTP-to-Postgres/MinIO synthetic admission; no clinical service."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from PIL import Image
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi import Request
from sqlalchemy import func, select

from app.api.dependencies import get_object_storage, require_current_user
from app.core.settings import Settings
from app.factory import create_app
from app.infrastructure.agent_runtime import RuntimeConversationGateway
from app.modules.baby.profile_models import BabyProfile
from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisEvent
from app.modules.stool_analyses.public_router import analysis_available, get_stool_conversation_gateway
from app.modules.stool_photos.models import StoolPhoto
from app.modules.stool_photos.rules import validate_original
from app.modules.users.models import User
from test_stool_photo_postgres_integration import (
    DATABASE_URL, ENDPOINT, isolated_storage_and_database, service as photo_service,
)
from app.workers.stool_photo_reviews import purge_deleted
from test_stool_photo_rules import image_bytes

pytestmark = pytest.mark.skipif(not DATABASE_URL or not ENDPOINT, reason="Requires isolated PostgreSQL and MinIO test profile.")


def test_synthetic_http_admission_owner_idempotency_and_replay():
    async def run():
        async with isolated_storage_and_database() as (sessions, storage):
            owner, other, baby, thread, photo_id = (uuid4() for _ in range(5))
            body = image_bytes()
            metadata = validate_original(body)
            digest = sha256(body).hexdigest()
            object_key = f"synthetic/http/{photo_id}"
            await storage.put_bytes(key=object_key, body=body, content_type=metadata.content_type)
            async with sessions.begin() as session:
                session.add_all([User(id=owner, status="active"), User(id=other, status="active")])
                await session.flush()
                session.add(BabyProfile(id=baby, owner_user_id=owner, name="Synthetic"))
                await session.flush()
                session.add(StoolPhoto(
                    id=photo_id, owner_user_id=owner, object_key=object_key, sha256=digest,
                    content_type=metadata.content_type, size_bytes=len(body),
                    width=metadata.width, height=metadata.height,
                    review_status="passed", reason_codes=[], review_attempt=1,
                    review_sha256=digest,
                    review_expires_at=datetime.now(timezone.utc) + timedelta(days=179),
                ))
            app = create_app(Settings(app_env="test", stool_specialty_market="US"))
            app.state.db_session_factory = sessions
            app.dependency_overrides[get_object_storage] = lambda: storage

            async def test_principal(request: Request):
                return SimpleNamespace(user_id=UUID(request.headers["X-Test-Owner"]))

            app.dependency_overrides[require_current_user] = test_principal
            payload = {"baby_id": str(baby), "thread_id": str(thread), "photo_ids": [str(photo_id)]}
            headers = {"X-Test-Owner": str(owner), "Authorization": "Bearer scoped-token", "Idempotency-Key": "synthetic-once"}
            path = "/v1/stool-analyses"
            requests = []

            def agent_reply(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                if request.headers.get("Authorization") != "Bearer scoped-token":
                    return httpx.Response(401)
                return httpx.Response(200, json={"id": str(thread), "owner_user_id": str(owner)})

            async with httpx.AsyncClient(transport=httpx.MockTransport(agent_reply)) as upstream:
                app.dependency_overrides[get_stool_conversation_gateway] = lambda: RuntimeConversationGateway(
                    upstream, "https://agent.example",
                )
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                    closed = await client.post(path, json=payload, headers=headers)
                    assert closed.status_code == 503 and closed.json()["error"]["code"] == "stool_analysis_unavailable"
                    async with sessions.begin() as session:
                        assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 0
                    assert requests == []
                    # Only availability is test-overridden; thread ownership uses the Agent endpoint.
                    app.dependency_overrides[analysis_available] = lambda: True
                    first = await client.post(path, json=payload, headers=headers)
                    assert first.status_code == 202, first.text
                    analysis_id = first.json()["analysis_id"]
                    async with sessions.begin() as session:
                        pinned = await session.get(StoolAnalysis, UUID(analysis_id))
                        assert (pinned.market, pinned.language, pinned.baby_context_snapshot_id is not None) == (
                            "US", "en", True,
                        )
                    assert first.json()["status"] == "queued"
                    replay = await client.post(path, json=payload, headers=headers)
                    assert replay.status_code == 202 and replay.json() == first.json()
                    assert all(request.headers.get("Authorization") == "Bearer scoped-token" for request in requests)
                    second = await client.post(path, json=payload,
                                               headers={**headers, "Idempotency-Key": "synthetic-twice"})
                    assert second.status_code == 202 and second.json()["analysis_id"] != analysis_id
                    recent = await client.get(path, params={"thread_id": str(thread), "limit": 1}, headers=headers)
                    assert recent.status_code == 200
                    assert len(recent.json()["items"]) == 1
                    assert recent.json()["next_cursor"] == recent.json()["items"][0]["analysis_id"]
                    older = await client.get(path, params={"thread_id": str(thread), "cursor": recent.json()["next_cursor"]}, headers=headers)
                    assert older.status_code == 200 and [item["analysis_id"] for item in older.json()["items"]] == [analysis_id]
                    assert older.json()["next_cursor"] is None
                    assert "object_key" not in str(recent.json()) and "final_result" not in str(recent.json())
                    assert (await client.post(path, json={**payload, "thread_id": str(uuid4())}, headers=headers)).status_code == 404
                    other_headers = {**headers, "X-Test-Owner": str(other), "Idempotency-Key": "other"}
                    assert (await client.post(path, json=payload, headers=other_headers)).status_code == 404
                    assert (await client.get(path, params={"thread_id": str(thread)}, headers=other_headers)).status_code == 404
                    assert (await client.get(f"{path}/{analysis_id}", headers=other_headers)).status_code == 409
                    assert (await client.get(f"{path}/{analysis_id}/events/stream", headers=other_headers)).status_code == 409
                    status = await client.get(f"{path}/{analysis_id}", headers=headers)
                    assert status.status_code == 200 and status.json()["status"] == "queued"
                    refs = await client.get(f"{path}/{analysis_id}/photos", headers=headers)
                    assert refs.status_code == 200 and refs.json() == {
                        "items": [{"photo_id": str(photo_id), "position": 0}],
                    }
                    thumbnail_path = f"{path}/{analysis_id}/photos/{photo_id}/thumbnail"
                    thumbnail = await client.get(thumbnail_path, headers=headers)
                    assert thumbnail.status_code == 200
                    assert thumbnail.headers["content-type"].startswith("image/jpeg")
                    assert thumbnail.headers["cache-control"] == "private, no-store"
                    assert thumbnail.content.startswith(b"\xff\xd8\xff")
                    with Image.open(BytesIO(thumbnail.content)) as decoded:
                        assert max(decoded.size) <= 512 and not decoded.getexif()
                    assert await storage.get_bytes(key=object_key) == body
                    await storage.put_bytes(key=object_key, body=b"tampered", content_type=metadata.content_type)
                    assert (await client.get(thumbnail_path, headers=headers)).status_code == 409
                    await storage.put_bytes(key=object_key, body=body, content_type=metadata.content_type)
                    async with sessions.begin() as session:
                        (await session.get(StoolPhoto, photo_id)).review_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
                    # Old approved originals stay viewable after the 180-day *new-submission* window.
                    assert (await client.get(thumbnail_path, headers=headers)).status_code == 200
                    async with sessions.begin() as session:
                        (await session.get(StoolPhoto, photo_id)).review_expires_at = None
                    assert (await client.get(thumbnail_path, headers=headers)).status_code == 409
                    async with sessions.begin() as session:
                        (await session.get(StoolPhoto, photo_id)).review_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
                    assert (await client.get(thumbnail_path, headers=other_headers)).status_code == 409
                    assert (await client.get(
                        f"{path}/{analysis_id}/photos/{uuid4()}/thumbnail", headers=headers,
                    )).status_code == 409
                    page = await client.get(f"{path}/{analysis_id}/events/stream", headers=headers)
                    assert page.status_code == 200 and "id: 1\nevent: stool.card.created" in page.text
                    assert "object_key" not in page.text and "sha256" not in page.text
                    next_page = await client.get(f"{path}/{analysis_id}/events/stream", params={"after_sequence": 1}, headers=headers)
                    assert next_page.status_code == 200 and next_page.text == ""
                    async with sessions.begin() as session:
                        assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 2
                        assert await session.scalar(select(func.count()).select_from(StoolAnalysisEvent)) == 2
                        (await session.get(StoolPhoto, photo_id)).review_status = "rejected"
                    revoked = await client.post(path, json=payload, headers={**headers, "Idempotency-Key": "new"})
                    assert revoked.status_code == 409
                    async with sessions.begin() as session:
                        assert await session.scalar(select(func.count()).select_from(StoolAnalysis)) == 2
                    async with sessions.begin() as session:
                        await photo_service(session, storage).delete(
                            owner=owner, photo_id=photo_id, request_id="synthetic-delete",
                        )
                    assert (await client.get(f"{path}/{analysis_id}", headers=headers)).status_code == 409
                    assert (await client.get(thumbnail_path, headers=headers)).status_code == 409
                    assert (await client.get(f"{path}/{analysis_id}/photos", headers=headers)).status_code == 409
                    assert (await client.get(f"{path}/{analysis_id}/events/stream", headers=headers)).status_code == 409
                    after_delete = await client.get(path, params={"thread_id": str(thread)}, headers=headers)
                    assert after_delete.status_code == 200 and after_delete.json()["items"] == []
                    async with sessions.begin() as session:
                        rows = (await session.scalars(select(StoolAnalysis))).all()
                        assert len(rows) == 2 and all(row.revoked_at is not None for row in rows)
                    assert await purge_deleted(sessions, storage)
                    with pytest.raises(ClientError):
                        await storage.get_bytes(key=object_key)
    asyncio.run(run())
