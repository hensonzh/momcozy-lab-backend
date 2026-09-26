import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.modules.auth.email import QueuedAuthEmailSender, deliver_next_email
from app.modules.auth.models import AuthEmailDelivery, EmailChallenge
from tests.test_account_lifecycle import account_case, PASSWORD


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


@pytest.mark.parametrize('status', ['disabled', 'suspended', 'deleted'])
def test_nonactive_account_cannot_login_or_refresh(status):
    from app.core.errors import ApiError
    from app.modules.auth.account_service import DeviceContext
    from app.modules.users.models import User
    async def run():
        async with account_case() as (sessions, lifecycle, mailbox, _clock, _settings):
            async with sessions.begin() as session:
                await lifecycle(session).register(email='mia@example.com', password=PASSWORD)
                issued = await lifecycle(session).verify_email(email='mia@example.com', token=mailbox.token, password=PASSWORD, device=DeviceContext())
                issued.user.status = status
            async with sessions.begin() as session:
                with pytest.raises(ApiError):
                    await lifecycle(session).auth.login(email='mia@example.com', password=PASSWORD)
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
