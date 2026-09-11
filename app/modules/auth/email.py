from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Protocol
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ...core.settings import Settings
from .models import AuthEmailDelivery, EmailChallenge
from ..users.models import User


class AuthEmailSender(Protocol):
    async def send(self, *, recipient: str, subject: str, body: str) -> None: ...


class AuthChallengeEmailSender(Protocol):
    async def send(self, *, challenge: EmailChallenge, recipient: str, subject: str, body: str) -> None: ...


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _cipher(key: str) -> Fernet:
    if len(key.encode()) < 32:
        raise ApiError(code="auth_email_unavailable", message="Email authentication is temporarily unavailable.", status=503)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("auth-mail-encryption:" + key).encode()).digest()))


class QueuedAuthEmailSender:
    """Only encrypted email content is durable; insert commits with the challenge."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session, self.settings = session, settings

    async def send(self, *, challenge: EmailChallenge, recipient: str, subject: str, body: str) -> None:
        now = datetime.now(timezone.utc)
        data = json.dumps({"recipient": recipient, "subject": subject, "body": body,
            "challenge_id": str(challenge.id), "challenge_token_hash": challenge.token_hash,
            "challenge_sent_at": _aware(challenge.sent_at).isoformat()}).encode()
        self.session.add(AuthEmailDelivery(user_id=challenge.user_id, encrypted_message=_cipher(self.settings.auth_email_token_key).encrypt(data).decode(),
            expires_at=challenge.expires_at, available_at=now))
        await self.session.flush()


class SmtpAuthEmailSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, *, recipient: str, subject: str, body: str) -> None:
        await asyncio.to_thread(self._send_sync, recipient, subject, body)

    def _send_sync(self, recipient: str, subject: str, body: str) -> None:
        if not self.settings.auth_smtp_host or not self.settings.auth_email_from:
            raise RuntimeError("Authentication email delivery is not configured.")
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.settings.auth_email_from, recipient, subject
        message.set_content(body)
        with smtplib.SMTP(self.settings.auth_smtp_host, self.settings.auth_smtp_port, timeout=10) as client:
            client.starttls(context=ssl.create_default_context())
            if self.settings.auth_smtp_username:
                client.login(self.settings.auth_smtp_username, self.settings.auth_smtp_password)
            client.send_message(message)


async def deliver_next_email(session: AsyncSession, settings: Settings, sender: AuthEmailSender) -> str | None:
    now = datetime.now(timezone.utc)
    # Resend, reset and deletion also lock User first. Hold that lock through
    # SMTP so the challenge cannot be replaced between validation and sending.
    user = await session.scalar(select(User).join(AuthEmailDelivery, AuthEmailDelivery.user_id == User.id)
        .where(AuthEmailDelivery.status == "pending", AuthEmailDelivery.available_at <= now)
        .order_by(AuthEmailDelivery.available_at).with_for_update(of=User, skip_locked=True).limit(1)
        .execution_options(populate_existing=True))
    if user is None:
        return None
    record = await session.scalar(select(AuthEmailDelivery).where(AuthEmailDelivery.user_id == user.id,
        AuthEmailDelivery.status == "pending", AuthEmailDelivery.available_at <= now)
        .order_by(AuthEmailDelivery.available_at).with_for_update(skip_locked=True).limit(1)
        .execution_options(populate_existing=True))
    if record is None:
        return None
    if _aware(record.expires_at) <= now:
        record.status, record.encrypted_message = "expired", None
        return record.status
    try:
        payload = json.loads(_cipher(settings.auth_email_token_key).decrypt((record.encrypted_message or "").encode()))
        challenge_id = payload.get("challenge_id")
        challenge = await session.scalar(select(EmailChallenge).where(EmailChallenge.id == UUID(challenge_id),
            EmailChallenge.user_id == user.id).execution_options(populate_existing=True)) if challenge_id else None
        if (challenge is None or challenge.consumed_at is not None or challenge.attempts >= 5
            or _aware(challenge.expires_at) <= now or challenge.token_hash != payload.get("challenge_token_hash")
            or _aware(challenge.sent_at).isoformat() != payload.get("challenge_sent_at")
            or user.email != payload.get("recipient") or user.status not in {"active", "email_unverified"}):
            # Legacy deliveries without a challenge version also fail closed.
            record.status, record.encrypted_message = "cancelled", None
            return record.status
        await sender.send(recipient=payload["recipient"], subject=payload["subject"], body=payload["body"])
    except Exception:
        # Never log SMTP responses or payloads; they contain recipient/code/credentials.
        record.attempts += 1
        record.available_at = now + timedelta(seconds=min(30 * 2 ** record.attempts, 300))
        if record.attempts >= 5:
            record.status, record.encrypted_message = "failed", None
        return "retry" if record.status == "pending" else record.status
    record.attempts += 1
    record.status, record.encrypted_message = "sent", None
    return record.status
