"""Password mutation contracts and session revocation."""
import asyncio

import httpx
from fastapi import Depends
from sqlalchemy import select

from app.factory import create_app
from app.infrastructure.db.session import get_session
from app.modules.auth.account_lifecycle import AccountLifecycleService
from app.modules.auth.passwords import verify_password
from app.modules.auth.router import get_account_lifecycle_service
from app.modules.users.models import AuthIdentity
from tests.test_account_lifecycle import PASSWORD, account_case


def test_double_confirmation_is_mandatory_for_registration_and_reset():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, _clock, settings):
            app = create_app(settings)
            app.state.db_session_factory = sessions

            async def dependency(session=Depends(get_session)) -> AccountLifecycleService:
                return lifecycle(session)

            app.dependency_overrides[get_account_lifecycle_service] = dependency
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                assert (await client.post('/v1/auth/register', json={'email': 'mia@example.com'})).status_code == 202
                code = mail.token
                body = {'email': 'mia@example.com', 'token': code, 'password': PASSWORD}
                assert (await client.post('/v1/auth/verify-email', json=body)).status_code == 422
                assert (await client.post('/v1/auth/verify-email', json={**body, 'confirm_password': 'wrong-password-123'})).status_code == 422
                success = await client.post('/v1/auth/verify-email', json={**body, 'confirm_password': PASSWORD})
                assert success.status_code == 200
                assert (await client.post('/v1/auth/forgot-password', json={'email': 'mia@example.com'})).status_code == 202
                reset_code = mail.token
                reset = {'email': 'mia@example.com', 'token': reset_code, 'new_password': 'replacement-password-456'}
                assert (await client.post('/v1/auth/reset-password', json=reset)).status_code == 422
                assert (await client.post('/v1/auth/reset-password', json={**reset, 'confirm_password': 'wrong-password-123'})).status_code == 422
                assert (await client.post('/v1/auth/reset-password', json={**reset, 'confirm_password': reset['new_password']})).status_code == 200
    asyncio.run(run())


def test_authenticated_password_change_requires_current_password_and_revokes_sessions():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, _clock, settings):
            app = create_app(settings)
            app.state.db_session_factory = sessions

            async def dependency(session=Depends(get_session)) -> AccountLifecycleService:
                return lifecycle(session)

            app.dependency_overrides[get_account_lifecycle_service] = dependency
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                assert (await client.post('/v1/auth/register', json={'email': 'mia@example.com'})).status_code == 202
                signup = await client.post('/v1/auth/verify-email', json={
                    'email': 'mia@example.com', 'token': mail.token,
                    'password': PASSWORD, 'confirm_password': PASSWORD,
                })
                assert signup.status_code == 200
                first_token = signup.json()['access_token']
                other = await client.post('/v1/auth/login', json={'email': 'mia@example.com', 'password': PASSWORD})
                assert other.status_code == 200
                body = {'current_password': PASSWORD, 'new_password': 'replacement-password-456',
                        'confirm_password': 'replacement-password-456'}
                assert (await client.post('/v1/auth/change-password', json=body)).status_code == 401
                headers = {'Authorization': f'Bearer {first_token}'}
                assert (await client.post('/v1/auth/change-password', json={**body, 'current_password': 'wrong-password'}, headers=headers)).status_code == 422
                assert (await client.post('/v1/auth/change-password', json={**body, 'confirm_password': 'wrong-password-123'}, headers=headers)).status_code == 422
                assert (await client.post('/v1/auth/change-password', json={**body, 'new_password': PASSWORD, 'confirm_password': PASSWORD}, headers=headers)).status_code == 422
                assert (await client.get('/v1/auth/me', headers=headers)).status_code == 200
                changed = await client.post('/v1/auth/change-password', json=body, headers=headers)
                assert changed.status_code == 200 and changed.json()['status'] == 'password_changed'
                assert (await client.get('/v1/auth/me', headers=headers)).status_code == 401
                assert (await client.post('/v1/auth/refresh', json={'refresh_token': other.json()['refresh_token']})).status_code == 401
                assert (await client.post('/v1/auth/login', json={'email': 'mia@example.com', 'password': PASSWORD})).status_code == 401
                assert (await client.post('/v1/auth/login', json={'email': 'mia@example.com', 'password': body['new_password']})).status_code == 200
                async with sessions.begin() as session:
                    identity = await session.scalar(select(AuthIdentity))
                    assert verify_password(body['new_password'], identity.password_hash)
    asyncio.run(run())
