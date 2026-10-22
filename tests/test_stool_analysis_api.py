"""Internal-only synthetic follow-up resolver; disabled until clinical approval."""
import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.modules.stool_analyses.context import StoolFollowupContext
from app.modules.stool_analyses.router import context_available, get_context_service

KEY = 'agent-runtime-service-key-with-32-bytes'


class FakeResolver:
    def __init__(self, analysis_id):
        self.analysis_id = analysis_id
        self.calls = []

    async def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return StoolFollowupContext(
            analysis_id=self.analysis_id, result_schema_version='stool.result.v1',
            result={'color': 'synthetic-test-value'}, images=(b'\xff\xd8\xff\xe0synthetic',),
            handling='test-only',
        )


def request(analysis_id, actor, baby, thread, device):
    return {
        'analysis_id': str(analysis_id), 'actor_user_id': str(actor), 'baby_id': str(baby), 'thread_id': str(thread),
        'session_id': str(device),
        'token_expires_at': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }


def test_internal_stool_context_requires_service_key_and_defaults_to_closed():
    analysis_id, actor, baby, thread, device = (uuid4() for _ in range(5))
    app = create_app(Settings(app_env='test', agent_runtime_service_api_key=KEY))
    fake = FakeResolver(analysis_id)
    app.dependency_overrides[get_context_service] = lambda: fake
    url = '/v1/internal/agent/stool-analyses/context/resolve'
    body = request(analysis_id, actor, baby, thread, device)
    unauthenticated = TestClient(app).post(url, json=body)
    assert unauthenticated.status_code == 401
    disabled = TestClient(app).post(url, headers={'X-Service-Key': KEY}, json=body)
    assert disabled.status_code == 503
    assert fake.calls == []


def test_synthetic_internal_stool_context_returns_ordered_pair_without_object_key():
    analysis_id, actor, baby, thread, device = (uuid4() for _ in range(5))
    app = create_app(Settings(app_env='test', agent_runtime_service_api_key=KEY))
    fake = FakeResolver(analysis_id)
    app.dependency_overrides[get_context_service] = lambda: fake
    app.dependency_overrides[context_available] = lambda: True  # Test-only gate override.
    url = '/v1/internal/agent/stool-analyses/context/resolve'
    response = TestClient(app).post(url, headers={'X-Service-Key': KEY},
                                    json=request(analysis_id, actor, baby, thread, device))
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.json()['analysis_id'] == str(analysis_id)
    assert response.json()['result'] == {'color': 'synthetic-test-value'}
    assert base64.b64decode(response.json()['images_b64'][0]) == b'\xff\xd8\xff\xe0synthetic'
    assert 'object_key' not in response.text
    assert fake.calls[0]['actor_user_id'] == actor
    assert fake.calls[0]['session_id'] == device
    assert fake.calls[0]['thread_id'] == thread
    assert fake.calls[0]['baby_id'] == baby
    assert len(fake.calls) == 1


def test_real_internal_resolver_rechecks_revocation_each_synthetic_request():
    import asyncio
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.infrastructure.db.base import Base
    from app.modules.auth.models import DeviceSession
    from app.modules.baby.profile_models import BabyProfile
    from app.modules.stool_analyses.models import StoolAnalysis, StoolAnalysisPhoto
    from app.modules.stool_analyses.service import StoolAnalysisContextResolver
    from app.modules.stool_photos.models import StoolPhoto
    from app.modules.users.models import User
    from tests.test_stool_analysis_persistence import PrivateStorage, fixture

    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        tables = [User.__table__, BabyProfile.__table__, DeviceSession.__table__, StoolPhoto.__table__,
                  StoolAnalysis.__table__, StoolAnalysisPhoto.__table__]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = PrivateStorage()
        owner, baby, thread, device, analysis, _, now = await fixture(sessions, storage)
        app = create_app(Settings(app_env='test', agent_runtime_service_api_key=KEY))

        async def provide_service():
            async with sessions() as session:
                yield StoolAnalysisContextResolver(session=session, storage=storage)

        app.dependency_overrides[get_context_service] = provide_service
        app.dependency_overrides[context_available] = lambda: True
        payload = {
            'analysis_id': str(analysis), 'actor_user_id': str(owner),
            'baby_id': str(baby), 'thread_id': str(thread), 'session_id': str(device),
            'token_expires_at': (now + timedelta(minutes=10)).isoformat(),
        }
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            endpoint = '/v1/internal/agent/stool-analyses/context/resolve'
            first = await client.post(endpoint, headers={'X-Service-Key': KEY}, json=payload)
            assert first.status_code == 200 and storage.reads == 1
            assert first.json()['result'] == {'color': 'synthetic-test-value'}
            assert base64.b64decode(first.json()['images_b64'][0]).startswith(b'\xff\xd8')
            async with sessions.begin() as session:
                (await session.get(DeviceSession, device)).status = 'revoked'
            second = await client.post(endpoint, headers={'X-Service-Key': KEY}, json=payload)
            assert second.status_code == 409 and storage.reads == 1
            assert second.json()['error']['code'] == 'stool_analysis_context_unavailable'
        await engine.dispose()

    asyncio.run(run())
