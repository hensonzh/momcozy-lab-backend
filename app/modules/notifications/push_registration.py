from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ..auth.current_user import CurrentUser
from ..auth.models import DeviceSession
from ..users.models import User
from .models import PushInstallation
from .schemas import PushInstallationRead, PushInstallationWrite

SEND_PERMISSIONS = ("authorized", "provisional")


def token_cipher(key: str) -> Fernet:
    if len(key.encode()) < 32:
        raise ApiError(code="push_unavailable", message="Background notifications are unavailable.", status=503)
    digest = hashlib.sha256(("push-token:" + key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def installation_read(value: PushInstallation) -> PushInstallationRead:
    return PushInstallationRead(installation_id=value.id, binding_id=value.binding_id, permission=value.permission,
        revision=value.client_revision, token_registered=value.encrypted_token is not None and value.invalidated_at is None)


async def active_installations(session: AsyncSession, owner: UUID, *, now: datetime) -> list[PushInstallation]:
    return list(await session.scalars(select(PushInstallation)
        .join(DeviceSession, DeviceSession.id == PushInstallation.session_id)
        .join(User, User.id == PushInstallation.owner_user_id)
        .where(PushInstallation.owner_user_id == owner, DeviceSession.user_id == owner,
            User.status == "active", DeviceSession.status == "active",
            PushInstallation.permission.in_(SEND_PERMISSIONS), PushInstallation.encrypted_token.is_not(None),
            PushInstallation.invalidated_at.is_(None), PushInstallation.last_seen_at > now - timedelta(days=30))
        .order_by(PushInstallation.id).execution_options(populate_existing=True)))


class PushRegistrationService:
    def __init__(self, session: AsyncSession, *, token_key: str, now: Callable[[], datetime] | None = None) -> None:
        self.session, self.token_key = session, token_key
        self.now = now or (lambda: datetime.now(timezone.utc))

    async def _active_session(self, actor: CurrentUser) -> DeviceSession:
        try:
            session_id = UUID(actor.session_id)
        except ValueError as exc:
            raise ApiError(code="authentication_required", message="Sign in to manage notifications.", status=401) from exc
        # All account/session operations lock the account before the session.
        user = await self.session.scalar(select(User).where(User.id == actor.user_id).with_for_update()
            .execution_options(populate_existing=True))
        device = await self.session.scalar(select(DeviceSession).where(DeviceSession.id == session_id,
            DeviceSession.user_id == actor.user_id).with_for_update().execution_options(populate_existing=True))
        if user is None or user.status != "active" or device is None or device.status != "active":
            raise ApiError(code="authentication_required", message="Sign in to manage notifications.", status=401)
        return device

    async def register(self, actor: CurrentUser, body: PushInstallationWrite) -> PushInstallationRead:
        cipher = token_cipher(self.token_key)
        device_session = await self._active_session(actor)
        # Serialize creation of an absent installation and unique token ownership.
        # A single registration lock avoids opposite-token transfer lock cycles.
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            await self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('push-registration', 0))"))
        value = await self.session.scalar(select(PushInstallation).where(PushInstallation.id == body.installation_id)
            .with_for_update().execution_options(populate_existing=True))
        if value is not None:
            self._check_secret(value, body.installation_secret.get_secret_value())
            if body.revision <= value.client_revision:
                if value.owner_user_id != actor.user_id or value.session_id != device_session.id:
                    raise ApiError(code="push_binding_changed", message="Refresh notification registration.", status=409)
                return installation_read(value)
        else:
            value = PushInstallation(id=body.installation_id, secret_hash=_digest(body.installation_secret.get_secret_value()),
                platform=body.platform, last_seen_at=self.now())
            self.session.add(value)
        raw_token = body.token.get_secret_value() if body.token is not None else None
        if raw_token is not None:
            digest = _digest(raw_token)
            existing = await self.session.scalar(select(PushInstallation).where(PushInstallation.token_hash == digest,
                PushInstallation.id != body.installation_id).with_for_update())
            if existing is not None:
                # A refreshed/reinstalled client can own the current token, but
                # a previous installation must not receive the same push twice.
                existing.encrypted_token, existing.token_hash = None, None
                existing.invalidated_at = self.now()
                await self.session.flush()
            value.encrypted_token, value.token_hash = cipher.encrypt(raw_token.encode()).decode(), digest
            value.invalidated_at = None
        if value.owner_user_id != actor.user_id or value.session_id != device_session.id:
            value.binding_id = uuid4()
        value.owner_user_id, value.session_id = actor.user_id, device_session.id
        value.platform, value.permission, value.locale = body.platform, body.permission, body.locale
        value.client_revision, value.last_seen_at = body.revision, self.now()
        await self.session.flush()
        return installation_read(value)

    async def detach(self, actor: CurrentUser, installation_id: UUID, secret: str) -> None:
        device_session = await self._active_session(actor)
        value = await self.session.scalar(select(PushInstallation).where(PushInstallation.id == installation_id)
            .with_for_update().execution_options(populate_existing=True))
        if value is None:
            return
        self._check_secret(value, secret)
        if value.owner_user_id != actor.user_id or value.session_id != device_session.id:
            raise ApiError(code="not_found", message="Notification installation not found.", status=404)
        value.owner_user_id, value.session_id = None, None
        value.binding_id = uuid4()
        value.client_revision += 1
        await self.session.flush()

    @staticmethod
    def _check_secret(value: PushInstallation, secret: str) -> None:
        if not hmac.compare_digest(value.secret_hash, _digest(secret)):
            raise ApiError(code="permission_denied", message="Notification installation cannot be changed.", status=403)
