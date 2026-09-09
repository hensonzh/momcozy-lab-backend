import asyncio
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.factory import create_app
from app.modules.auth.models import DeviceSession, RefreshToken
from test_care_booking import postgres
from test_workbench_auth import workbench_auth_case


@postgres
def test_concurrent_refresh_http_commits_reuse_revocation_and_rotates_at_most_once():
    async def run():
        async with workbench_auth_case() as (sessions, service, _at, _secret, settings, _provider):
            async with sessions.begin() as session:
                issued = await service(session).accounts.login(email='expert@example.test', password='test-password')
            app = create_app(settings)
            app.state.db_session_factory = sessions
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                responses = await asyncio.gather(*[
                    client.post('/v1/auth/refresh', json={'refresh_token': issued.refresh_token}) for _ in range(2)
                ])
                assert sorted(response.status_code for response in responses) == [200, 401]
                failed = next(response for response in responses if response.status_code == 401)
                assert failed.json()['error']['code'] == 'refresh_token_reuse_detected'
                assert failed.headers['cache-control'] == 'private, no-store'
            async with sessions.begin() as session:
                device = await session.scalar(select(DeviceSession))
                assert device.status == 'revoked' and device.revoked_at is not None
                tokens = list((await session.scalars(select(RefreshToken))).all())
                assert len(tokens) == 2
                assert all(token.status == 'revoked' and token.revoked_at is not None for token in tokens)
    asyncio.run(run())


@postgres
def test_expired_refresh_http_commits_token_revocation():
    async def run():
        async with workbench_auth_case() as (sessions, service, at, _secret, settings, _provider):
            async with sessions.begin() as session:
                issued = await service(session).accounts.login(email='expert@example.test', password='test-password')
                record = await session.scalar(select(RefreshToken))
                record.expires_at = at[0] - timedelta(seconds=1)
            app = create_app(settings)
            app.state.db_session_factory = sessions
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                response = await client.post('/v1/auth/refresh', json={'refresh_token': issued.refresh_token})
                assert response.status_code == 401
            async with sessions.begin() as session:
                record = await session.scalar(select(RefreshToken))
                assert record.status == 'revoked' and record.revoked_at is not None
    asyncio.run(run())
