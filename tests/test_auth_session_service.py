import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.auth.models import DeviceSession, RefreshToken
from app.modules.auth.service import AuthSessionService, refresh_token_hash


def test_create_session_stores_only_refresh_token_hash() -> None:
    repository = FakeAuthSessionRepository()
    service = AuthSessionService(repository=repository, token_factory=TokenFactory(["raw-refresh"]), clock=_clock)
    user_id = uuid4()

    created = asyncio.run(service.create_session(user_id=user_id, device_id="ios-device"))

    assert created.device_session.user_id == user_id
    assert created.refresh_token.raw_token == "raw-refresh"
    assert created.refresh_token.record.token_hash == refresh_token_hash("raw-refresh")
    assert created.refresh_token.record.token_hash != "raw-refresh"


def test_rotate_refresh_token_marks_old_token_and_returns_new_raw_token() -> None:
    old_token = _refresh_token(raw_token="old-refresh", status="active", expires_at=_clock() + timedelta(days=1))
    repository = FakeAuthSessionRepository(existing_refresh_token=old_token)
    service = AuthSessionService(repository=repository, token_factory=TokenFactory(["new-refresh"]), clock=_clock)

    issued = asyncio.run(service.rotate_refresh_token(raw_token="old-refresh"))

    assert old_token.status == "rotated"
    assert old_token.rotated_at == _clock()
    assert issued.raw_token == "new-refresh"
    assert issued.record.family_id == old_token.family_id
    assert issued.record.token_hash == refresh_token_hash("new-refresh")


def test_rotate_refresh_token_detects_reuse_and_revokes_family_and_session() -> None:
    reused = _refresh_token(raw_token="old-refresh", status="rotated", expires_at=_clock() + timedelta(days=1))
    repository = FakeAuthSessionRepository(existing_refresh_token=reused)
    service = AuthSessionService(repository=repository, token_factory=TokenFactory(["new-refresh"]), clock=_clock)

    with pytest.raises(ApiError, match="reuse detected"):
        asyncio.run(service.rotate_refresh_token(raw_token="old-refresh"))

    assert repository.revoked_family_id == reused.family_id
    assert repository.revoked_session_id == reused.session_id


def test_rotate_refresh_token_rejects_expired_token() -> None:
    expired = _refresh_token(raw_token="old-refresh", status="active", expires_at=_clock() - timedelta(seconds=1))
    repository = FakeAuthSessionRepository(existing_refresh_token=expired)
    service = AuthSessionService(repository=repository, token_factory=TokenFactory(["new-refresh"]), clock=_clock)

    with pytest.raises(ApiError, match="expired"):
        asyncio.run(service.rotate_refresh_token(raw_token="old-refresh"))

    assert expired.status == "revoked"
    assert expired.revoked_at == _clock()


def test_revoke_session_delegates_to_repository() -> None:
    repository = FakeAuthSessionRepository()
    service = AuthSessionService(repository=repository, clock=_clock)
    session_id = uuid4()

    asyncio.run(service.revoke_session(session_id=session_id))

    assert repository.revoked_session_id == session_id


def test_revoke_user_sessions_delegates_to_repository() -> None:
    repository = FakeAuthSessionRepository()
    service = AuthSessionService(repository=repository, clock=_clock)
    user_id = uuid4()

    asyncio.run(service.revoke_user_sessions(user_id=user_id))

    assert repository.revoked_user_id == user_id


def _clock() -> datetime:
    return datetime(2026, 7, 2, tzinfo=timezone.utc)


def _refresh_token(*, raw_token: str, status: str, expires_at: datetime) -> RefreshToken:
    return RefreshToken(
        session_id=uuid4(),
        token_hash=refresh_token_hash(raw_token),
        family_id=uuid4(),
        status=status,
        expires_at=expires_at,
    )


class TokenFactory:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens

    def __call__(self) -> str:
        return self.tokens.pop(0)


class FakeAuthSessionRepository:
    def __init__(self, *, existing_refresh_token=None) -> None:
        self.existing_refresh_token = existing_refresh_token
        self.revoked_family_id = None
        self.revoked_session_id = None
        self.revoked_user_id = None

    async def create_device_session(self, **kwargs):
        return DeviceSession(id=uuid4(), **kwargs)

    async def create_refresh_token(self, **kwargs):
        return RefreshToken(**kwargs)

    async def get_refresh_token_by_hash(self, *, token_hash: str):
        if self.existing_refresh_token and self.existing_refresh_token.token_hash == token_hash:
            return self.existing_refresh_token
        return None

    async def rotate_refresh_token(self, *, refresh_token, new_token_hash: str, expires_at: datetime, rotated_at: datetime):
        refresh_token.status = "rotated"
        refresh_token.rotated_at = rotated_at
        return RefreshToken(
            session_id=refresh_token.session_id,
            token_hash=new_token_hash,
            family_id=refresh_token.family_id,
            expires_at=expires_at,
        )

    async def revoke_refresh_token(self, *, refresh_token, revoked_at: datetime):
        refresh_token.status = "revoked"
        refresh_token.revoked_at = revoked_at
        return refresh_token

    async def revoke_refresh_token_family(self, *, family_id, revoked_at: datetime):
        self.revoked_family_id = family_id

    async def revoke_device_session(self, *, session_id, revoked_at: datetime):
        self.revoked_session_id = session_id
        return None

    async def revoke_user_sessions(self, *, user_id, revoked_at: datetime):
        self.revoked_user_id = user_id
        return 1
