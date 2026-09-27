"""Account lifecycle state transitions against a real SQLAlchemy database."""
import asyncio
from uuid import uuid4
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.errors import ApiError
from app.factory import create_app
from app.infrastructure.db.base import Base
from app.modules.auth.account_lifecycle import AccountLifecycleService
from app.modules.auth.account_service import AuthAccountService, DeviceContext
from app.modules.auth.models import AuthEmailDelivery, AccountDeletionRequest, DeviceSession, EmailChallenge, RefreshToken
from app.modules.auth.passwords import verify_password
from app.modules.auth.repository import AuthAccountRepository, AuthSessionRepository
from app.modules.auth.router import get_account_lifecycle_service
from app.modules.auth.service import AuthSessionService
from app.modules.users.models import AuthIdentity, User
from tests.auth_key_material import auth_settings

PASSWORD = 'correct-password-123'


class Mailbox:
    def __init__(self):
        self.messages = []

    async def send(self, **message):
        self.messages.append(message)

    @property
    def token(self):
        return self.messages[-1]['body'].split('\n\n')[1]


@asynccontextmanager
async def account_case(database_url=None):
    admin = None
    schema = f'auth_account_{uuid4().hex}'
    if database_url:
        admin = create_async_engine(database_url)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(database_url, connect_args={'server_settings': {'search_path': schema}})
    else:
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[User.__table__, AuthIdentity.__table__, DeviceSession.__table__, RefreshToken.__table__, EmailChallenge.__table__, AccountDeletionRequest.__table__, AuthEmailDelivery.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    clock = [datetime.now(timezone.utc)]
    mail = Mailbox()
    settings = auth_settings(auth_email_token_key='test-only-' + 'x'*32, auth_require_active_session=True)
    def lifecycle(session):
        repository = AuthAccountRepository(session)
        auth = AuthAccountService(account_repository=repository, session_service=AuthSessionService(repository=AuthSessionRepository(session)), settings=settings)
        return AccountLifecycleService(accounts=repository, auth=auth, email_sender=mail, token_key=settings.auth_email_token_key, now=lambda: clock[0])
    try:
        yield sessions, lifecycle, mail, clock, settings
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


def test_register_verify_login_reset_and_logout_lifecycle():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, clock, _settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                await service.register(email='Mia@Example.com', password=PASSWORD)
                user = await session.scalar(select(User))
                assert user.status == 'email_unverified' and user.email_verified_at is None
                assert await session.scalar(select(func.count()).select_from(DeviceSession)) == 0
                code = mail.token
                challenge = await session.scalar(select(EmailChallenge))
                assert code not in challenge.token_hash and len(challenge.token_hash) == 64
                with pytest.raises(ApiError) as pending:
                    await service.auth.login(email=user.email, password=PASSWORD)
                assert pending.value.code == 'email_unverified'
            async with sessions.begin() as session:
                issued = await lifecycle(session).verify_email(email='mia@example.com', token=code, password=PASSWORD, device=DeviceContext())
                assert issued.user.status == 'active' and issued.user.email_verified_at is not None
            async with sessions.begin() as session:
                with pytest.raises(ApiError):
                    await lifecycle(session).verify_email(email='mia@example.com', token=code, password=PASSWORD, device=DeviceContext())
            clock[0] += timedelta(minutes=2)
            async with sessions.begin() as session:
                await lifecycle(session).request_password_reset(email='mia@example.com')
                reset = mail.token
            async with sessions.begin() as session:
                await lifecycle(session).reset_password(email='mia@example.com', token=reset, new_password='replacement-password-456')
                assert all(s.status == 'revoked' for s in (await session.scalars(select(DeviceSession))).all())
                identity = await session.scalar(select(AuthIdentity))
                assert not verify_password(PASSWORD, identity.password_hash)
            async with sessions.begin() as session:
                with pytest.raises(ApiError):
                    await lifecycle(session).auth.refresh(refresh_token=issued.refresh_token)
    asyncio.run(run())


def test_duplicate_registration_never_replaces_password_and_expired_tokens_cannot_activate():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, clock, _settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                await service.register(email='mia@example.com', password=PASSWORD)
                old = mail.token
                await service.register(email='mia@example.com', password='attacker-password-123')
                assert await session.scalar(select(func.count()).select_from(User)) == 1
                identity = await session.scalar(select(AuthIdentity))
                assert verify_password(PASSWORD, identity.password_hash)
            clock[0] += timedelta(minutes=16)
            async with sessions.begin() as session:
                with pytest.raises(ApiError):
                    await lifecycle(session).verify_email(email='mia@example.com', token=old, password=PASSWORD, device=DeviceContext())
    asyncio.run(run())


def test_active_email_duplicate_registration_keeps_password_and_sends_no_verification():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, _clock, _settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                await service.register(email='mia@example.com', password=PASSWORD)
                await service.verify_email(email='mia@example.com', token=mail.token, password=PASSWORD, device=DeviceContext())
            sent_before = len(mail.messages)
            async with sessions.begin() as session:
                result = await lifecycle(session).register(email='mia@example.com', password='attacker-password-123')
                assert result.verification_required is True
                assert len(mail.messages) == sent_before
            async with sessions.begin() as session:
                await lifecycle(session).auth.login(email='mia@example.com', password=PASSWORD)
                assert await session.scalar(select(func.count()).select_from(User)) == 1
    asyncio.run(run())


def test_legacy_external_only_account_can_set_email_password_with_mailbox_proof():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, clock, _settings):
            async with sessions.begin() as session:
                user = User(email='legacy@example.com', email_verified_at=clock[0])
                session.add(user)
                session.add(AuthIdentity(user=user, provider='google', subject='legacy-sub', email=user.email))
            async with sessions.begin() as session:
                service = lifecycle(session)
                await service.register(email='legacy@example.com', password=PASSWORD)
                assert mail.messages == []  # Duplicate registration does not grant access.
                await service.request_password_reset(email='legacy@example.com')
                code = mail.token
            async with sessions.begin() as session:
                await lifecycle(session).reset_password(email='legacy@example.com', token=code, new_password=PASSWORD)
                identities = (await session.scalars(select(AuthIdentity))).all()
                assert {identity.provider for identity in identities} == {'google', 'email'}
                assert await session.scalar(select(func.count()).select_from(User)) == 1
            async with sessions.begin() as session:
                pair = await lifecycle(session).auth.login(email='legacy@example.com', password=PASSWORD)
                assert pair.user.email == 'legacy@example.com'
            async with sessions.begin() as session:
                with pytest.raises(ApiError):
                    await lifecycle(session).reset_password(email='legacy@example.com', token=code, new_password='another-password-123')
    asyncio.run(run())


def test_retired_external_routes_are_not_exposed_and_inactive_legacy_accounts_cannot_reset():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, clock, settings):
            app = create_app(settings)
            assert '/v1/auth/google' not in app.openapi()['paths']
            assert '/v1/auth/google/link' not in app.openapi()['paths']
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                assert (await client.post('/v1/auth/google', json={})).status_code == 404
            async with sessions.begin() as session:
                user = User(email='inactive@example.com', email_verified_at=clock[0], status='suspended')
                session.add(user)
                session.add(AuthIdentity(user=user, provider='google', subject='inactive-sub', email=user.email))
            async with sessions.begin() as session:
                await lifecycle(session).request_password_reset(email='inactive@example.com')
                assert mail.messages == []
    asyncio.run(run())


def test_http_failed_code_attempts_commit_and_old_signup_uses_verification():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, _clock, settings):
            app = create_app(settings)
            app.state.db_session_factory = sessions
            from fastapi import Depends
            from app.infrastructure.db.session import get_session
            async def dependency(session=Depends(get_session)):
                return lifecycle(session)
            app.dependency_overrides[get_account_lifecycle_service] = dependency
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                response = await client.post('/v1/auth/signup', json={'email':'mia@example.com', 'password':PASSWORD})
                assert response.status_code == 202 and 'access_token' not in response.json()
                code = mail.token
                for _ in range(5):
                    rejected = await client.post('/v1/auth/verify-email', json={'email':'mia@example.com', 'password':PASSWORD, 'confirm_password': PASSWORD, 'token':'incorrect'})
                    assert rejected.status_code == 401
                rejected = await client.post('/v1/auth/verify-email', json={'email':'mia@example.com', 'password':PASSWORD, 'confirm_password': PASSWORD, 'token':code})
                assert rejected.status_code == 401
                assert rejected.headers['cache-control'] == 'private, no-store'
            async with sessions.begin() as session:
                assert (await session.scalar(select(EmailChallenge))).attempts == 5
    asyncio.run(run())


def test_registration_verifies_mailbox_before_password_and_requires_matching_confirmation():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, _clock, settings):
            app = create_app(settings)
            app.state.db_session_factory = sessions
            from fastapi import Depends
            from app.infrastructure.db.session import get_session

            async def dependency(session=Depends(get_session)):
                return lifecycle(session)

            app.dependency_overrides[get_account_lifecycle_service] = dependency
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                requested = await client.post('/v1/auth/register', json={'email': 'mia@example.com'})
                assert requested.status_code == 202 and requested.json() == {'status': 'verification_required'}
                code = mail.token
                async with sessions.begin() as session:
                    user = await AuthAccountRepository(session).get_user_by_email(email='mia@example.com')
                    assert user is not None and user.status == 'email_unverified'
                    identity = await session.scalar(select(AuthIdentity))
                    assert identity is not None and not verify_password(PASSWORD, identity.password_hash)
                bad = await client.post('/v1/auth/verify-registration-code', json={'email': 'mia@example.com', 'token': '00000000'})
                assert bad.status_code == 401
                checked = await client.post('/v1/auth/verify-registration-code', json={'email': 'mia@example.com', 'token': code})
                assert checked.status_code == 200 and checked.json() == {'status': 'code_valid'}
                mismatch = await client.post('/v1/auth/verify-email', json={
                    'email': 'mia@example.com', 'token': code,
                    'password': PASSWORD, 'confirm_password': 'different-password-123',
                })
                assert mismatch.status_code == 422
                completed = await client.post('/v1/auth/verify-email', json={
                    'email': 'mia@example.com', 'token': code,
                    'password': PASSWORD, 'confirm_password': PASSWORD,
                })
                assert completed.status_code == 200 and completed.json()['access_token']
                replay = await client.post('/v1/auth/verify-email', json={
                    'email': 'mia@example.com', 'token': code,
                    'password': PASSWORD, 'confirm_password': PASSWORD,
                })
                assert replay.status_code == 401
    asyncio.run(run())


def test_registration_code_check_has_bounded_attempts_and_does_not_activate_account():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, _clock, settings):
            app = create_app(settings)
            app.state.db_session_factory = sessions
            from fastapi import Depends
            from app.infrastructure.db.session import get_session

            async def dependency(session=Depends(get_session)):
                return lifecycle(session)

            app.dependency_overrides[get_account_lifecycle_service] = dependency
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                assert (await client.post('/v1/auth/register', json={'email': 'mia@example.com'})).status_code == 202
                code = mail.token
                for _ in range(5):
                    assert (await client.post('/v1/auth/verify-registration-code', json={'email': 'mia@example.com', 'token': '00000000'})).status_code == 401
                assert (await client.post('/v1/auth/verify-registration-code', json={'email': 'mia@example.com', 'token': code})).status_code == 401
                async with sessions.begin() as session:
                    user = await AuthAccountRepository(session).get_user_by_email(email='mia@example.com')
                    assert user is not None and user.status == 'email_unverified'
                    challenge = await session.scalar(select(EmailChallenge))
                    assert challenge.attempts == 5
    asyncio.run(run())


def test_registration_precheck_expires_without_issuing_a_session():
    async def run():
        async with account_case() as (sessions, lifecycle, mail, clock, _settings):
            async with sessions.begin() as session:
                await lifecycle(session).start_registration(email='mia@example.com')
            code = mail.token
            async with sessions.begin() as session:
                await lifecycle(session).check_registration_code(email='mia@example.com', token=code)
                user = await AuthAccountRepository(session).get_user_by_email(email='mia@example.com')
                assert user is not None and user.status == 'email_unverified'
                assert await session.scalar(select(func.count()).select_from(DeviceSession)) == 0
            clock[0] += timedelta(minutes=16)
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as rejected:
                    await lifecycle(session).check_registration_code(email='mia@example.com', token=code)
                assert rejected.value.code == 'invalid_or_expired_code'
                with pytest.raises(ApiError):
                    await lifecycle(session).verify_email(email='mia@example.com', token=code, password=PASSWORD, device=DeviceContext())
            async with sessions.begin() as session:
                user = await AuthAccountRepository(session).get_user_by_email(email='mia@example.com')
                assert user is not None and user.status == 'email_unverified'
                assert await session.scalar(select(func.count()).select_from(DeviceSession)) == 0
    asyncio.run(run())
