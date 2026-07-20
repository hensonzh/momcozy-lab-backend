from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..users.models import AuthIdentity, User
from .models import DeviceSession, RefreshToken


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
        return await self.session.get(User, user_id)

    async def create_email_user(self, *, email: str, password_hash: str, display_name: str) -> tuple[User, AuthIdentity]:
        user = User(display_name=display_name)
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

    async def create_invite_user(self, *, invite_code: str, device_id: str, display_name: str) -> tuple[User, AuthIdentity]:
        user = User(display_name=display_name)
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

    async def get_refresh_token_by_hash(self, *, token_hash: str) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
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
        device_session = await self.session.get(DeviceSession, session_id)
        if device_session is None:
            return None
        device_session.status = "revoked"
        device_session.revoked_at = revoked_at
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
