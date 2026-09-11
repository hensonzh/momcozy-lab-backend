import asyncio

import httpx
from sqlalchemy import select, func

from app.factory import create_app
from app.modules.auth.account_service import DeviceContext
from app.modules.auth.router import get_account_lifecycle_service
from app.modules.auth.models import DeviceSession, RefreshToken
from app.modules.users.models import User
from tests.test_account_lifecycle import account_case, PASSWORD
from test_care_booking import DATABASE_URL, postgres


@postgres
def test_concurrent_registration_and_google_signin_create_one_account():
    async def run():
        async with account_case(DATABASE_URL) as (sessions, lifecycle, mailbox, _clock, _settings):
            async def register():
                async with sessions.begin() as session:
                    await lifecycle(session).register(email='mia@example.com', password=PASSWORD)
            await asyncio.gather(register(), register())
            assert len(mailbox.messages) == 1
            async def google():
                async with sessions.begin() as session:
                    return (await lifecycle(session).google_login(id_token='test-token', device=DeviceContext())).user.id
            ids = await asyncio.gather(google(), google())
            assert ids[0] == ids[1]
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count()).select_from(User)) == 2
    asyncio.run(run())


@postgres
def test_http_logout_and_deletion_reject_existing_access_and_refresh_tokens():
    async def run():
        async with account_case(DATABASE_URL) as (sessions, lifecycle, _mailbox, _clock, settings):
            app = create_app(settings)
            app.state.db_session_factory = sessions
            async def lifecycle_dependency():
                async with sessions.begin() as session:
                    yield lifecycle(session)
            app.dependency_overrides[get_account_lifecycle_service] = lifecycle_dependency
            async with sessions.begin() as session:
                pair = await lifecycle(session).google_login(id_token='valid', device=DeviceContext())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                headers = {'Authorization': f'Bearer {pair.access_token}'}
                profile = await client.get('/v1/auth/me', headers=headers)
                assert profile.status_code == 200 and profile.json()['auth_providers'] == ['google']
                rotated = await client.post('/v1/auth/refresh', json={'refresh_token': pair.refresh_token})
                assert rotated.status_code == 200
                # Logout remains valid with a previously rotated refresh token.
                assert (await client.post('/v1/auth/logout-session', json={'refresh_token': pair.refresh_token})).status_code == 200
                assert (await client.get('/v1/auth/me', headers=headers)).status_code == 401
                assert (await client.post('/v1/auth/refresh', json={'refresh_token': rotated.json()['refresh_token']})).status_code == 401
                async with sessions.begin() as session:
                    fresh = await lifecycle(session).google_login(id_token='valid', device=DeviceContext())
                headers = {'Authorization': f'Bearer {fresh.access_token}'}
                removed = await client.delete('/v1/auth/me', headers=headers)
                assert removed.status_code == 200 and removed.json()['status'] == 'deletion_pending'
                assert (await client.get('/v1/auth/me', headers=headers)).status_code in (401, 403)
                assert (await client.post('/v1/auth/refresh', json={'refresh_token': fresh.refresh_token})).status_code == 401
            async with sessions.begin() as session:
                assert all(s.status == 'revoked' for s in (await session.scalars(select(DeviceSession))).all())
                assert all(t.status == 'revoked' for t in (await session.scalars(select(RefreshToken))).all())
    asyncio.run(run())
