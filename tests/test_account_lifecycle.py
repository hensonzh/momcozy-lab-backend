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
from app.modules.auth.google import GoogleClaims
from app.modules.auth.models import AuthEmailDelivery, AccountDeletionRequest, DeviceSession, EmailChallenge, RefreshToken
from app.modules.auth.passwords import verify_password
from app.modules.auth.repository import AuthAccountRepository, AuthSessionRepository
from app.modules.auth.router import get_account_lifecycle_service
from app.modules.auth.service import AuthSessionService
from app.modules.auth.workbench_models import WorkbenchLoginChallenge
from app.modules.care.models import CareProvider
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


class Google:
    def __init__(self, email='google@example.com', subject='google-sub'):
        self.email, self.subject = email, subject

    async def verify(self, **_kwargs):
        return GoogleClaims(subject=self.subject, email=self.email, email_verified=True)


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
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[User.__table__, AuthIdentity.__table__, DeviceSession.__table__, RefreshToken.__table__, EmailChallenge.__table__, AccountDeletionRequest.__table__, AuthEmailDelivery.__table__, CareProvider.__table__, WorkbenchLoginChallenge.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    clock = [datetime.now(timezone.utc)]
    mail = Mailbox()
    settings = auth_settings(auth_email_token_key='test-only-' + 'x'*32, auth_google_client_id='google-client', auth_require_active_session=True)
    def lifecycle(session, google=None):
        repository = AuthAccountRepository(session)
        auth = AuthAccountService(account_repository=repository, session_service=AuthSessionService(repository=AuthSessionRepository(session)), settings=settings)
        return AccountLifecycleService(accounts=repository, auth=auth, email_sender=mail, token_key=settings.auth_email_token_key, now=lambda: clock[0], google_verifier=google or Google())
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


def test_google_existing_email_requires_linking_and_deleted_user_cannot_return():
    async def run():
        async with account_case() as (sessions, lifecycle, _mail, _clock, _settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                first = await service.google_login(id_token='valid', device=DeviceContext())
                second = await service.google_login(id_token='valid', device=DeviceContext())
                assert first.user.id == second.user.id
                assert await session.scalar(select(func.count()).select_from(User)) == 1
                user = await service.accounts.get_user(user_id=first.user.id)
                await service.delete_account(user=user)
                assert user.status == 'deleted' and user.email is None
                assert (await session.scalar(select(AccountDeletionRequest))).status == 'pending_erasure'
            async with sessions.begin() as session:
                service = lifecycle(session)
                await service.register(email='email@example.com', password=PASSWORD)
                with pytest.raises(ApiError) as conflict:
                    await lifecycle(session, Google(email='email@example.com', subject='different')).google_login(id_token='valid', device=DeviceContext())
                assert conflict.value.code == 'account_link_required'
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
                    rejected = await client.post('/v1/auth/verify-email', json={'email':'mia@example.com', 'password':PASSWORD, 'token':'incorrect'})
                    assert rejected.status_code == 401
                rejected = await client.post('/v1/auth/verify-email', json={'email':'mia@example.com', 'password':PASSWORD, 'token':code})
                assert rejected.status_code == 401
                assert rejected.headers['cache-control'] == 'private, no-store'
            async with sessions.begin() as session:
                assert (await session.scalar(select(EmailChallenge))).attempts == 5
    asyncio.run(run())
