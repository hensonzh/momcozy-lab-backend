import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.modules.auth import google
from app.modules.auth.email import QueuedAuthEmailSender, deliver_next_email
from app.modules.auth.models import AuthEmailDelivery, EmailChallenge
from tests.test_account_lifecycle import account_case, PASSWORD


def _claims():
    now = datetime.now(timezone.utc)
    return dict(sub='google-sub', email='mia@example.com', email_verified=True,
                iss='https://accounts.google.com', aud='expected-client', iat=now, exp=now + timedelta(minutes=10))


@pytest.mark.parametrize('change', [
    {'aud': 'other-client'}, {'iss': 'https://attacker.invalid'}, {'exp': 0},
    {'iat': 99999999999}, {'email_verified': False}, {'sub': ''}, {'email': ''},
    {'exp': None}, {'iat': None}, {'aud': None}, {'iss': None}, {'sub': None},
])
def test_google_rejects_invalid_signed_claims(monkeypatch, change):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(google, '_google_jwks', lambda: SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())))
    claims = _claims()
    claims.update(change)
    claims = {k: v for k, v in claims.items() if v is not None}
    token = jwt.encode(claims, key, algorithm='RS256')
    with pytest.raises((ValueError, jwt.PyJWTError)):
        asyncio.run(google.GoogleOidcTokenVerifier().verify(id_token=token, audience='expected-client'))


def test_google_verifies_signature_and_only_exposes_identity(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(google, '_google_jwks', lambda: SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())))
    token = jwt.encode(_claims(), key, algorithm='RS256')
    claims = asyncio.run(google.GoogleOidcTokenVerifier().verify(id_token=token, audience='expected-client'))
    assert claims.subject == 'google-sub' and claims.email_verified
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.InvalidSignatureError):
        asyncio.run(google.GoogleOidcTokenVerifier().verify(id_token=jwt.encode(_claims(), wrong_key, algorithm='RS256'), audience='expected-client'))


def test_mail_queue_is_transactional_encrypted_retries_and_scrubs():
    async def run():
        async with account_case() as (sessions, lifecycle, mailbox, _clock, settings):
            async with sessions.begin() as session:
                await lifecycle(session).register(email='mia@example.com', password=PASSWORD)
            async with sessions() as session:
                challenge = await session.scalar(select(EmailChallenge))
                await QueuedAuthEmailSender(session, settings).send(challenge=challenge, recipient='mia@example.com', subject='Verify', body='12345678')
                await session.rollback()
            async with sessions.begin() as session:
                assert await session.scalar(select(AuthEmailDelivery)) is None
                challenge = await session.scalar(select(EmailChallenge))
                await QueuedAuthEmailSender(session, settings).send(challenge=challenge, recipient='mia@example.com', subject='Verify', body='12345678')
                row = await session.scalar(select(AuthEmailDelivery))
                assert '12345678' not in row.encrypted_message and 'mia@example.com' not in row.encrypted_message
            class Failure:
                async def send(self, **kwargs):
                    raise RuntimeError('private SMTP failure')
            async with sessions.begin() as session:
                assert await deliver_next_email(session, settings, Failure()) == 'retry'
                row = await session.scalar(select(AuthEmailDelivery))
                assert row.attempts == 1 and row.encrypted_message
                row.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            async with sessions.begin() as session:
                assert await deliver_next_email(session, settings, mailbox) == 'sent'
                row = await session.scalar(select(AuthEmailDelivery))
                assert row.encrypted_message is None and mailbox.messages[-1]['body'] == '12345678'
    asyncio.run(run())


def test_google_link_requires_password_and_matching_email_and_never_duplicates():
    from app.core.errors import ApiError
    from app.modules.auth.account_service import DeviceContext
    from app.modules.users.models import AuthIdentity, User
    from tests.test_account_lifecycle import Google
    async def run():
        async with account_case() as (sessions, lifecycle, mailbox, _clock, _settings):
            async with sessions.begin() as session:
                await lifecycle(session).register(email='mia@example.com', password=PASSWORD)
                issued = await lifecycle(session).verify_email(email='mia@example.com', token=mailbox.token, password=PASSWORD, device=DeviceContext())
                user_id = issued.user.id
            async with sessions.begin() as session:
                user = await session.get(User, user_id)
                service = lifecycle(session, Google(email='mia@example.com'))
                with pytest.raises(ApiError) as denied:
                    await service.link_google(current_user=user, password='wrong', id_token='valid')
                assert denied.value.status == 401
                with pytest.raises(ApiError) as mismatch:
                    await lifecycle(session, Google(email='other@example.com')).link_google(current_user=user, password=PASSWORD, id_token='valid')
                assert mismatch.value.code == 'account_link_conflict'
                await service.link_google(current_user=user, password=PASSWORD, id_token='valid')
                await service.link_google(current_user=user, password=PASSWORD, id_token='valid')
                signed_in = await service.google_login(id_token='valid', device=DeviceContext())
                assert signed_in.user.id == user_id
                providers = list((await session.scalars(select(AuthIdentity.provider).where(AuthIdentity.user_id == user_id))).all())
                assert sorted(providers) == ['email', 'google']
    asyncio.run(run())


@pytest.mark.parametrize('status', ['disabled', 'suspended', 'deleted'])
def test_nonactive_account_cannot_login_refresh_or_google_sign_in(status):
    from app.core.errors import ApiError
    from app.modules.auth.account_service import DeviceContext
    from app.modules.users.models import User
    async def run():
        async with account_case() as (sessions, lifecycle, _mailbox, _clock, _settings):
            async with sessions.begin() as session:
                issued = await lifecycle(session).google_login(id_token='valid', device=DeviceContext())
                issued.user.status = status
            async with sessions.begin() as session:
                with pytest.raises(ApiError):
                    await lifecycle(session).google_login(id_token='valid', device=DeviceContext())
                with pytest.raises(ApiError):
                    await lifecycle(session).auth.refresh(refresh_token=issued.refresh_token)
                assert (await session.get(User, issued.user.id)).status == status
    asyncio.run(run())


def test_verification_code_is_purpose_bound_and_activation_invalidates_old_reset():
    from app.core.errors import ApiError
    from app.modules.auth.account_service import DeviceContext
    async def run():
        async with account_case() as (sessions, lifecycle, mailbox, _clock, _settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                await service.register(email='mia@example.com', password=PASSWORD)
                verification = mailbox.token
                with pytest.raises(ApiError):
                    await service.reset_password(email='mia@example.com', token=verification, new_password='replacement123')
                await service.request_password_reset(email='mia@example.com')
                reset = mailbox.token
                await service.verify_email(email='mia@example.com', token=verification, password=PASSWORD, device=DeviceContext())
            async with sessions.begin() as session:
                with pytest.raises(ApiError):
                    await lifecycle(session).reset_password(email='mia@example.com', token=reset, new_password='replacement123')
    asyncio.run(run())
