import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch

import httpx
import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi import Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import authenticate_request_user
from app.core.errors import ApiError
from app.factory import create_app
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import get_session
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.auth.account_service import AuthAccountService, DeviceContext
from app.modules.auth.jwt import authenticate_access_token
from app.modules.auth.models import DeviceSession, RefreshToken
from app.modules.auth.passwords import hash_password
from app.modules.auth.repository import AuthAccountRepository, AuthSessionRepository
from app.modules.auth.service import AuthSessionService
from app.modules.auth.workbench_models import WorkbenchLoginChallenge, WorkbenchMfaCredential
from app.modules.auth.workbench_repository import WorkbenchAuthRepository
from app.modules.auth.workbench_router import get_workbench_auth_service
from app.modules.auth.workbench_service import WorkbenchAuthService
from app.modules.care.models import CareProvider
from app.modules.users.models import AuthIdentity, User
from tests.auth_key_material import auth_settings
from test_care_booking import DATABASE_URL, postgres
from scripts.enroll_workbench_mfa import enroll


@asynccontextmanager
async def workbench_auth_case():
    schema = f'workbench_auth_{uuid4().hex}'
    admin = create_async_engine(DATABASE_URL)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(DATABASE_URL, connect_args={'server_settings': {'search_path': schema}})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[User.__table__, AuthIdentity.__table__,
                DeviceSession.__table__, RefreshToken.__table__, CareProvider.__table__, WorkbenchMfaCredential.__table__, WorkbenchLoginChallenge.__table__, AuditLog.__table__]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        at = [datetime.now(timezone.utc).replace(microsecond=0)]
        # These independently generated keys and identities are confined to this isolated test schema.
        encryption_key = Fernet.generate_key()
        cipher = Fernet(encryption_key)
        settings = auth_settings(ibclc_mfa_encryption_key=encryption_key.decode(), app_env='test')
        secret = pyotp.random_base32()
        async with sessions.begin() as session:
            user, _ = await AuthAccountRepository(session).create_email_user(email='expert@example.test', password_hash=hash_password('test-password'))
            session.add(CareProvider(user_id=user.id, display_name='Test IBCLC', timezone='UTC', regions=['CA'], languages=['en'], active=True))
            await session.flush()
            session.add(WorkbenchMfaCredential(provider_id=user.id, encrypted_secret=cipher.encrypt(secret.encode()).decode()))
            provider_id = user.id
        def service(session):
            session_service = AuthSessionService(repository=AuthSessionRepository(session), clock=lambda: at[0])
            accounts = AuthAccountService(account_repository=AuthAccountRepository(session), session_service=session_service, settings=settings)
            return WorkbenchAuthService(WorkbenchAuthRepository(session), accounts, session_service, AuditService(repository=AuditRepository(session)), settings, now=lambda: at[0])
        yield sessions, service, at, secret, settings, provider_id
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def begin(service):
    return await service.begin(email='expert@example.test', password='test-password', device=DeviceContext(device_id='workbench-browser'), request_id='begin')


@postgres
def test_workbench_tokens_require_both_factors_and_concurrent_verification_creates_one_session():
    async def run():
        async with workbench_auth_case() as (sessions, service, at, secret, settings, provider_id):
            async with sessions.begin() as session:
                challenge = await begin(service(session))
                assert await session.scalar(select(func.count()).select_from(DeviceSession)) == 0
                stored = await session.scalar(select(WorkbenchLoginChallenge))
                assert stored.token_hash != challenge.challenge and len(stored.token_hash) == 64
                credential = await session.get(WorkbenchMfaCredential, provider_id)
                assert secret not in credential.encrypted_secret and secret not in repr(credential)
            async def verify():
                async with sessions.begin() as session:
                    return await service(session).verify(challenge_token=challenge.challenge, code=pyotp.TOTP(secret).at(at[0]), request_id='verify')
            results = await asyncio.gather(verify(), verify(), return_exceptions=True)
            success = [result for result in results if not isinstance(result, Exception)]
            assert len(success) == 1 and success[0].issued is not None
            assert any(isinstance(result, ApiError) and result.code == 'mfa_challenge_expired' for result in results)
            issued = success[0].issued
            actor = await authenticate_request_user(token=issued.access_token, settings=settings, session_factory=sessions)
            assert actor.roles == frozenset({'ibclc'})
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count()).select_from(DeviceSession)) == 1
                # The ordinary App login never grants the expert role, even for a provisioned provider.
                ordinary = await service(session).accounts.login(email='expert@example.test', password='test-password')
                assert authenticate_access_token(ordinary.access_token, settings).roles == frozenset({'user'})
                refreshed = await service(session).accounts.refresh(refresh_token=issued.refresh_token)
                assert authenticate_access_token(refreshed.access_token, settings).roles == frozenset({'ibclc'})
                used = await begin(service(session))
                replay = await service(session).verify(challenge_token=used.challenge, code=pyotp.TOTP(secret).at(at[0]), request_id='used-code')
                assert replay.issued is None and replay.error.code == 'mfa_invalid'
    asyncio.run(run())


@postgres
def test_wrong_code_http_responses_commit_attempts_and_lockout_across_transactions():
    async def run():
        async with workbench_auth_case() as (sessions, service, at, secret, settings, provider_id):
            app = create_app(settings)
            app.state.db_session_factory = sessions
            async def dependency(session: AsyncSession = Depends(get_session)):
                return service(session)
            app.dependency_overrides[get_workbench_auth_service] = dependency
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                login = await client.post('/v1/ibclc/auth/login', json={'email': 'expert@example.test', 'password': 'test-password', 'device_id': 'browser'})
                assert login.status_code == 200 and 'access_token' not in login.json()
                assert login.headers['cache-control'] == 'private, no-store'
                totp = pyotp.TOTP(secret)
                valid = {totp.at(at[0] + timedelta(seconds=offset)) for offset in (-30, 0, 30)}
                wrong = next(f'{number:06d}' for number in range(1000000) if f'{number:06d}' not in valid)
                for index in range(5):
                    response = await client.post('/v1/ibclc/auth/verify', json={'challenge': login.json()['challenge'], 'code': wrong})
                    assert response.status_code == (429 if index == 4 else 401)
                blocked = await client.post('/v1/ibclc/auth/verify', json={'challenge': login.json()['challenge'], 'code': totp.at(at[0])})
                assert blocked.status_code == 429
            async with sessions.begin() as session:
                credential = await session.get(WorkbenchMfaCredential, provider_id)
                assert credential.failed_attempts == 5 and credential.locked_until == at[0] + timedelta(minutes=10)
                assert await session.scalar(select(func.count()).select_from(DeviceSession)) == 0
                at[0] += timedelta(minutes=11)
                fresh = await begin(service(session))
                result = await service(session).verify(challenge_token=fresh.challenge, code=pyotp.TOTP(secret).at(at[0]), request_id='unlocked')
                assert result.issued is not None
    asyncio.run(run())


@postgres
def test_workbench_access_checks_mfa_age_and_provider_activation_on_every_request():
    async def run():
        async with workbench_auth_case() as (sessions, service, at, secret, settings, provider_id):
            async with sessions.begin() as session:
                challenge = await begin(service(session))
                result = await service(session).verify(challenge_token=challenge.challenge, code=pyotp.TOTP(secret).at(at[0]), request_id='verify')
                token = result.issued.access_token
                device = await session.scalar(select(DeviceSession))
                device_id = device.id
                device.mfa_verified_at = at[0] - timedelta(hours=13)
            with pytest.raises(ApiError) as old:
                await authenticate_request_user(token=token, settings=settings, session_factory=sessions)
            assert old.value.code == 'mfa_required'
            async with sessions.begin() as session:
                (await session.get(DeviceSession, device_id)).mfa_verified_at = at[0]
                (await session.get(CareProvider, provider_id)).active = False
            with pytest.raises(ApiError) as inactive:
                await authenticate_request_user(token=token, settings=settings, session_factory=sessions)
            assert inactive.value.status == 403
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as refresh:
                    await service(session).accounts.refresh(refresh_token=result.issued.refresh_token)
                assert refresh.value.status == 403
    asyncio.run(run())


@postgres
def test_rotating_an_authenticator_revokes_existing_workbench_sessions_and_challenges():
    async def run():
        async with workbench_auth_case() as (sessions, service, at, secret, settings, provider_id):
            async with sessions.begin() as session:
                first = await begin(service(session))
                result = await service(session).verify(challenge_token=first.challenge, code=pyotp.TOTP(secret).at(at[0]), request_id='verify')
                old_challenge = await begin(service(session))
            with patch('scripts.enroll_workbench_mfa.create_db_engine', return_value=sessions.kw['bind']), \
                patch('scripts.enroll_workbench_mfa.create_session_factory', return_value=sessions):
                uri = await enroll('expert@example.test', rotate=True, settings=settings)
            assert pyotp.parse_uri(uri).secret != secret
            with pytest.raises(ApiError) as revoked:
                await authenticate_request_user(token=result.issued.access_token, settings=settings, session_factory=sessions)
            assert revoked.value.code == 'authentication_required'
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as stale:
                    await service(session).verify(challenge_token=old_challenge.challenge, code=pyotp.TOTP(secret).at(at[0]), request_id='stale')
                assert stale.value.code == 'mfa_challenge_expired'
                assert (await session.get(WorkbenchMfaCredential, provider_id)).version == 2
    asyncio.run(run())
