from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..users.models import AuthIdentity, User
from .models import AuthEmailDelivery, AccountDeletionRequest, DeviceSession, EmailChallenge, RefreshToken


class AuthAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_identity(self, *, provider: str, subject: str) -> AuthIdentity | None:
        statement = (
            select(AuthIdentity)
            .options(selectinload(AuthIdentity.user))
            .where(AuthIdentity.provider == provider, AuthIdentity.subject == subject)
        )
        return cast(AuthIdentity | None, await self.session.scalar(statement))

    async def get_invite_identity(self, *, invite_code: str) -> AuthIdentity | None:
        statement = (
            select(AuthIdentity)
            .options(selectinload(AuthIdentity.user))
            .where(AuthIdentity.provider == "invite", AuthIdentity.subject == invite_code)
        )
        return cast(AuthIdentity | None, await self.session.scalar(statement))

    async def get_user(self, *, user_id: UUID) -> User | None:
        statement = select(User).options(selectinload(User.identities)).where(User.id == user_id)
        return cast(User | None, await self.session.scalar(statement))

    async def lock_email(self, email: str) -> None:
        # PostgreSQL transaction locks also serialize absent rows during first signup.
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            await self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:email, 0))"), {"email": email})

    async def lock_user(self, user_id: UUID) -> User | None:
        return cast(User | None, await self.session.scalar(
            select(User).options(selectinload(User.identities)).where(User.id == user_id)
            .with_for_update().execution_options(populate_existing=True)))

    async def get_user_by_email(self, *, email: str) -> User | None:
        return cast(User | None, await self.session.scalar(select(User).where(User.email == email)))

    async def save_email_challenge(self, *, user_id: UUID, purpose: str, token_hash: str, expires_at: datetime, sent_at: datetime) -> EmailChallenge:
        existing = await self.session.scalar(
            select(EmailChallenge).where(EmailChallenge.user_id == user_id, EmailChallenge.purpose == purpose).with_for_update()
        )
        if existing is None:
            existing = EmailChallenge(user_id=user_id, purpose=purpose, token_hash=token_hash, expires_at=expires_at, sent_at=sent_at)
            self.session.add(existing)
        else:
            existing.token_hash, existing.expires_at, existing.sent_at = token_hash, expires_at, sent_at
            existing.attempts, existing.consumed_at = 0, None
        await self.session.flush()
        return existing

    async def get_email_challenge(self, *, user_id: UUID, purpose: str) -> EmailChallenge | None:
        return cast(EmailChallenge | None, await self.session.scalar(
            select(EmailChallenge).where(EmailChallenge.user_id == user_id, EmailChallenge.purpose == purpose).with_for_update()
        ))

    async def create_deletion_request(self, *, user_id: UUID) -> AccountDeletionRequest:
        request = AccountDeletionRequest(user_id=user_id)
        self.session.add(request)
        await self.session.flush()
        return request

    async def invalidate_challenges(self, *, user_id: UUID, now: datetime) -> None:
        await self.session.execute(update(EmailChallenge).where(EmailChallenge.user_id == user_id).values(consumed_at=now))

    async def anonymize_user(self, *, user: User, marker: str, now: datetime) -> None:
        await self.session.execute(update(AuthEmailDelivery).where(AuthEmailDelivery.user_id == user.id, AuthEmailDelivery.status == "pending").values(status="cancelled", encrypted_message=None))
        user.status, user.email, user.deleted_at = "deleted", None, now
        user.email_verified_at, user.last_login_at = None, None
        for identity in list(user.identities):
            identity.subject = f"deleted:{marker}:{identity.id}"
            identity.email, identity.phone, identity.device_id, identity.password_hash = "", "", "", ""
            identity.display_name, identity.avatar_url = "", ""
        await self.session.flush()

    async def create_email_user(self, *, email: str, password_hash: str) -> tuple[User, AuthIdentity]:
        user = User(email=email)
        identity = AuthIdentity(
            user=user,
            provider="email",
            subject=email,
            email=email,
            password_hash=password_hash,
        )
        self.session.add(user)
        self.session.add(identity)
        await self.session.flush()
        return user, identity

    async def create_pending_email_user(self, *, email: str, password_hash: str) -> User:
        user = User(email=email, status="email_unverified")
        self.session.add(user)
        self.session.add(AuthIdentity(user=user, provider="email", subject=email, email=email, password_hash=password_hash))
        await self.session.flush()
        return user

    async def create_invite_user(self, *, invite_code: str, device_id: str) -> tuple[User, AuthIdentity]:
        user = User()
        identity = AuthIdentity(
            user=user,
            provider="invite",
            subject=invite_code,
            device_id=device_id,
            email="",
            password_hash="",
        )
        self.session.add(user)
        self.session.add(identity)
        await self.session.flush()
        return user, identity

    async def bind_invite_identity_device(self, *, identity: AuthIdentity, device_id: str) -> AuthIdentity:
        identity.device_id = device_id
        await self.session.flush()
        return identity


class AuthSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_device_session(
        self,
        *,
        user_id: UUID,
        device_id: str,
        user_agent_hash: str,
        ip_hash: str,
    ) -> DeviceSession:
        device_session = DeviceSession(
            user_id=user_id,
            device_id=device_id,
            user_agent_hash=user_agent_hash,
            ip_hash=ip_hash,
        )
        self.session.add(device_session)
        await self.session.flush()
        return device_session

    async def create_refresh_token(
        self,
        *,
        session_id: UUID,
        token_hash: str,
        family_id: UUID | None,
        expires_at: datetime,
    ) -> RefreshToken:
        refresh_token = RefreshToken(
            session_id=session_id,
            token_hash=token_hash,
            family_id=family_id or uuid4(),
            expires_at=expires_at,
        )
        self.session.add(refresh_token)
        await self.session.flush()
        return refresh_token

    async def lock_refresh_user(self, token_hash: str) -> User | None:
        user_id = await self.session.scalar(select(DeviceSession.user_id).join(RefreshToken, RefreshToken.session_id == DeviceSession.id).where(RefreshToken.token_hash == token_hash))
        if user_id is None:
            return None
        return await AuthAccountRepository(self.session).lock_user(user_id)

    async def get_refresh_token_by_hash(self, *, token_hash: str) -> RefreshToken | None:
        session_id = await self.session.scalar(select(RefreshToken.session_id).where(RefreshToken.token_hash == token_hash))
        if session_id is None:
            return None
        # All refreshes in a session, including different generations, share this
        # lock with logout. Re-read token state after the preceding transaction.
        await self.session.scalar(select(DeviceSession).where(DeviceSession.id == session_id)
            .with_for_update().execution_options(populate_existing=True))
        statement = (select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            .with_for_update().execution_options(populate_existing=True))
        return cast(RefreshToken | None, await self.session.scalar(statement))

    async def get_device_session(self, *, session_id: UUID) -> DeviceSession | None:
        return await self.session.get(DeviceSession, session_id)

    async def rotate_refresh_token(
        self,
        *,
        refresh_token: RefreshToken,
        new_token_hash: str,
        expires_at: datetime,
        rotated_at: datetime,
    ) -> RefreshToken:
        refresh_token.status = "rotated"
        refresh_token.rotated_at = rotated_at
        replacement = RefreshToken(
            session_id=refresh_token.session_id,
            token_hash=new_token_hash,
            family_id=refresh_token.family_id,
            expires_at=expires_at,
        )
        self.session.add(replacement)
        await self.session.flush()
        return replacement

    async def revoke_refresh_token(self, *, refresh_token: RefreshToken, revoked_at: datetime) -> RefreshToken:
        refresh_token.status = "revoked"
        refresh_token.revoked_at = revoked_at
        await self.session.flush()
        return refresh_token

    async def revoke_refresh_token_family(self, *, family_id: UUID, revoked_at: datetime) -> None:
        statement = select(RefreshToken).where(RefreshToken.family_id == family_id)
        tokens = await self.session.scalars(statement)
        for refresh_token in tokens:
            refresh_token.status = "revoked"
            refresh_token.revoked_at = revoked_at
        await self.session.flush()

    async def revoke_device_session(self, *, session_id: UUID, revoked_at: datetime) -> DeviceSession | None:
        device_session = await self.session.scalar(select(DeviceSession).where(DeviceSession.id == session_id)
            .with_for_update().execution_options(populate_existing=True))
        if device_session is None:
            return None
        device_session.status = "revoked"
        device_session.revoked_at = revoked_at
        await self.session.execute(update(RefreshToken).where(RefreshToken.session_id == session_id).values(status="revoked", revoked_at=revoked_at))
        await self.session.flush()
        return device_session

    async def revoke_user_sessions(self, *, user_id: UUID, revoked_at: datetime) -> int:
        sessions = list(
            (
                await self.session.scalars(
                    select(DeviceSession).where(DeviceSession.user_id == user_id, DeviceSession.status == "active")
                )
            ).all()
        )
        if not sessions:
            return 0

        session_ids = [session.id for session in sessions]
        for device_session in sessions:
            device_session.status = "revoked"
            device_session.revoked_at = revoked_at

        refresh_tokens = await self.session.scalars(
            select(RefreshToken).where(RefreshToken.session_id.in_(session_ids), RefreshToken.status == "active")
        )
        for refresh_token in refresh_tokens:
            refresh_token.status = "revoked"
            refresh_token.revoked_at = revoked_at

        await self.session.flush()
        return len(sessions)
