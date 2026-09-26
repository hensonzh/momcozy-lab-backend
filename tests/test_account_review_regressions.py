"""Regressions for pending MFA challenges, PostgreSQL lock order, and stale mail."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.modules.auth.email import QueuedAuthEmailSender, deliver_next_email
from app.modules.auth.models import AuthEmailDelivery, EmailChallenge
from tests.test_account_lifecycle import PASSWORD, account_case
from tests.product_database import DATABASE_URL, postgres


async def wait_for_database_lock(sessions, pid):
    async with sessions() as observer:
        while True:
            waiting = await observer.scalar(text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"), {'pid': pid})
            if waiting:
                return
            await observer.rollback()
            await asyncio.sleep(0.01)


@postgres
def test_mail_worker_skips_account_locked_by_resend_and_rechecks_after_commit():
    async def run():
        async with account_case(DATABASE_URL) as (sessions, lifecycle, mailbox, clock, settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                service.email_sender = QueuedAuthEmailSender(session, settings)
                await service.register(email='mia@example.com', password=PASSWORD)
                await service.register(email='other@example.com', password=PASSWORD)
            clock[0] += timedelta(seconds=61)
            async with sessions.begin() as resending:
                service = lifecycle(resending)
                service.email_sender = QueuedAuthEmailSender(resending, settings)
                await service.resend_verification(email='mia@example.com')
                async with sessions.begin() as worker:
                    assert await asyncio.wait_for(deliver_next_email(worker, settings, mailbox), timeout=5) == 'sent'
                    assert mailbox.messages[-1]['recipient'] == 'other@example.com'
            async with sessions.begin() as worker:
                assert await deliver_next_email(worker, settings, mailbox) == 'cancelled'
            async with sessions.begin() as worker:
                assert await deliver_next_email(worker, settings, mailbox) == 'sent'
                assert mailbox.messages[-1]['recipient'] == 'mia@example.com'
            assert len(mailbox.messages) == 2
    asyncio.run(run())


@postgres
def test_resend_waits_until_current_mail_delivery_finishes():
    async def run():
        async with account_case(DATABASE_URL) as (sessions, lifecycle, mailbox, clock, settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                service.email_sender = QueuedAuthEmailSender(session, settings)
                await service.register(email='mia@example.com', password=PASSWORD)
            sending, release_smtp, resend_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
            resend_pid = []
            class PausedSender:
                async def send(self, **message):
                    sending.set()
                    await release_smtp.wait()
                    await mailbox.send(**message)
            async def deliver():
                async with sessions.begin() as session:
                    return await deliver_next_email(session, settings, PausedSender())
            async def resend():
                await sending.wait()
                async with sessions.begin() as session:
                    resend_pid.append(await session.scalar(text('SELECT pg_backend_pid()')))
                    resend_started.set()
                    clock[0] += timedelta(seconds=61)
                    service = lifecycle(session)
                    service.email_sender = QueuedAuthEmailSender(session, settings)
                    await service.resend_verification(email='mia@example.com')
            tasks = [asyncio.create_task(deliver()), asyncio.create_task(resend())]
            try:
                await asyncio.wait_for(resend_started.wait(), timeout=5)
                await asyncio.wait_for(wait_for_database_lock(sessions, resend_pid[0]), timeout=5)
                release_smtp.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
                assert results[0] == 'sent'
                assert len(mailbox.messages) == 1
                async with sessions.begin() as session:
                    assert await deliver_next_email(session, settings, mailbox) == 'sent'
                assert len(mailbox.messages) == 2
            finally:
                release_smtp.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('invalidated', ['consumed', 'expired', 'attempts_exhausted', 'legacy'])
def test_mail_worker_cancels_invalid_challenges_without_contacting_sender(invalidated):
    import json
    from app.modules.auth.email import _cipher
    async def run():
        async with account_case() as (sessions, lifecycle, mailbox, _clock, settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                service.email_sender = QueuedAuthEmailSender(session, settings)
                await service.register(email='mia@example.com', password=PASSWORD)
                challenge = await session.scalar(select(EmailChallenge))
                if invalidated == 'consumed':
                    challenge.consumed_at = datetime.now(timezone.utc)
                elif invalidated == 'expired':
                    challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                elif invalidated == 'attempts_exhausted':
                    challenge.attempts = 5
                else:
                    delivery = await session.scalar(select(AuthEmailDelivery))
                    payload = json.dumps({'recipient': 'mia@example.com', 'subject': 'Verify', 'body': 'local legacy code'}).encode()
                    delivery.encrypted_message = _cipher(settings.auth_email_token_key).encrypt(payload).decode()
            async with sessions.begin() as session:
                assert await deliver_next_email(session, settings, mailbox) == 'cancelled'
                delivery = await session.scalar(select(AuthEmailDelivery))
                assert delivery.encrypted_message is None and delivery.attempts == 0
            assert not mailbox.messages
    asyncio.run(run())


def test_issuing_reset_mail_preserves_current_verification_mail():
    async def run():
        async with account_case() as (sessions, lifecycle, mailbox, _clock, settings):
            async with sessions.begin() as session:
                service = lifecycle(session)
                service.email_sender = QueuedAuthEmailSender(session, settings)
                await service.register(email='mia@example.com', password=PASSWORD)
                await service.request_password_reset(email='mia@example.com')
            for _ in range(2):
                async with sessions.begin() as session:
                    assert await deliver_next_email(session, settings, mailbox) == 'sent'
            assert {message['subject'] for message in mailbox.messages} == {'Verify your Momcozy email', 'Reset your Momcozy password'}
    asyncio.run(run())


@pytest.mark.parametrize('purpose', ['verify_email', 'reset_password'])
def test_resend_never_delivers_a_superseded_code_after_retry(purpose):
    async def run():
        async with account_case() as (sessions, lifecycle, mailbox, clock, settings):
            def queued(session):
                result = lifecycle(session)
                result.email_sender = QueuedAuthEmailSender(session, settings)
                return result
            async with sessions.begin() as session:
                await (queued(session) if purpose == 'verify_email' else lifecycle(session)).register(email='mia@example.com', password=PASSWORD)
                if purpose == 'reset_password':
                    await queued(session).request_password_reset(email='mia@example.com')
                old_id = (await session.scalar(select(AuthEmailDelivery))).id
            class Failure:
                async def send(self, **kwargs):
                    raise RuntimeError('simulated local SMTP failure')
            async with sessions.begin() as session:
                assert await deliver_next_email(session, settings, Failure()) == 'retry'
            clock[0] += timedelta(seconds=61)
            async with sessions.begin() as session:
                if purpose == 'verify_email':
                    await queued(session).resend_verification(email='mia@example.com')
                else:
                    await queued(session).request_password_reset(email='mia@example.com')
            async with sessions.begin() as session:
                assert await deliver_next_email(session, settings, mailbox) == 'sent'
                new_code = mailbox.token
                (await session.get(AuthEmailDelivery, old_id)).available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            count = len(mailbox.messages)
            async with sessions.begin() as session:
                assert await deliver_next_email(session, settings, mailbox) == 'cancelled'
                old = await session.get(AuthEmailDelivery, old_id)
                assert old.encrypted_message is None
                assert len(mailbox.messages) == count and mailbox.token == new_code
                challenge = await session.scalar(select(EmailChallenge).where(EmailChallenge.purpose == purpose))
                assert challenge.attempts == 0
    asyncio.run(run())
