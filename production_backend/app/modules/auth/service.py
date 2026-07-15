from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from ...core.errors import ApiError
from .models import DeviceSession, RefreshToken
from .repository import AuthSessionRepository


REFRESH_TOKEN_TTL = timedelta(days=30)


@dataclass(frozen=True)
class IssuedRefreshToken:
    raw_token: str
    record: RefreshToken


@dataclass(frozen=True)
class CreatedAuthSession:
    device_session: DeviceSession
    refresh_token: IssuedRefreshToken


class AuthSessionService:
    def __init__(
        self,
        *,
        repository: AuthSessionRepository,
        token_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.token_factory = token_factory or _new_refresh_token
        self.clock = clock or _utcnow

    async def create_session(
        self,
        *,
        user_id: UUID,
        device_id: str = "",
        user_agent_hash: str = "",
        ip_hash: str = "",
    ) -> CreatedAuthSession:
        device_session = await self.repository.create_device_session(
            user_id=user_id,
            device_id=device_id,
            user_agent_hash=user_agent_hash,
            ip_hash=ip_hash,
        )
        issued = await self._create_refresh_token(session_id=device_session.id, family_id=None)
        return CreatedAuthSession(device_session=device_session, refresh_token=issued)

    async def rotate_refresh_token(self, *, raw_token: str) -> IssuedRefreshToken:
        now = self.clock()
        refresh_token = await self.repository.get_refresh_token_by_hash(token_hash=refresh_token_hash(raw_token))
        if refresh_token is None:
            raise ApiError(code="authentication_required", message="Refresh token is invalid.", status=401)

        if refresh_token.status != "active":
            await self.repository.revoke_refresh_token_family(family_id=refresh_token.family_id, revoked_at=now)
            await self.repository.revoke_device_session(session_id=refresh_token.session_id, revoked_at=now)
            raise ApiError(code="refresh_token_reuse_detected", message="Refresh token reuse detected.", status=401)

        if refresh_token.expires_at <= now:
            await self.repository.revoke_refresh_token(refresh_token=refresh_token, revoked_at=now)
            raise ApiError(code="authentication_required", message="Refresh token expired.", status=401)

        new_raw_token = self.token_factory()
        replacement = await self.repository.rotate_refresh_token(
            refresh_token=refresh_token,
            new_token_hash=refresh_token_hash(new_raw_token),
            expires_at=now + REFRESH_TOKEN_TTL,
            rotated_at=now,
        )
        return IssuedRefreshToken(raw_token=new_raw_token, record=replacement)

    async def revoke_session(self, *, session_id: UUID) -> None:
        now = self.clock()
        await self.repository.revoke_device_session(session_id=session_id, revoked_at=now)

    async def revoke_user_sessions(self, *, user_id: UUID) -> int:
        now = self.clock()
        return await self.repository.revoke_user_sessions(user_id=user_id, revoked_at=now)

    async def _create_refresh_token(self, *, session_id: UUID, family_id: UUID | None) -> IssuedRefreshToken:
        raw_token = self.token_factory()
        record = await self.repository.create_refresh_token(
            session_id=session_id,
            token_hash=refresh_token_hash(raw_token),
            family_id=family_id,
            expires_at=self.clock() + REFRESH_TOKEN_TTL,
        )
        return IssuedRefreshToken(raw_token=raw_token, record=record)


def refresh_token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _new_refresh_token() -> str:
    return f"rt_{secrets.token_urlsafe(48)}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
