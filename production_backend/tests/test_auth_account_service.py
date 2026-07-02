import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.modules.auth.account_service import AuthAccountService, DeviceContext
from production_backend.app.modules.auth.models import DeviceSession, RefreshToken
from production_backend.app.modules.auth.passwords import hash_password, verify_password
from production_backend.app.modules.auth.service import CreatedAuthSession, IssuedRefreshToken, refresh_token_hash
from production_backend.app.modules.users.models import AuthIdentity, User


def test_signup_creates_email_identity_hashes_password_and_issues_tokens() -> None:
    account_repository = FakeAccountRepository()
    session_service = FakeSessionService()
    service = AuthAccountService(
        account_repository=account_repository,
        session_service=session_service,
        settings=_settings(),
    )

    issued = asyncio.run(
        service.signup(
            email=" Test@Example.COM ",
            password="super-secret",
            display_name="Test User",
            device_context=DeviceContext(device_id="ios", user_agent="agent", ip_address="127.0.0.1"),
        )
    )

    assert account_repository.created_identity.subject == "test@example.com"
    assert account_repository.created_identity.password_hash != "super-secret"
    assert verify_password("super-secret", account_repository.created_identity.password_hash)
    assert session_service.created_user_id == account_repository.created_user.id
    assert session_service.created_user_agent_hash
    assert session_service.created_ip_hash
    assert issued.access_token
    assert issued.refresh_token == "refresh-token"


def test_signup_rejects_duplicate_email() -> None:
    existing = AuthIdentity(provider="email", subject="test@example.com", password_hash=hash_password("secret"))
    service = AuthAccountService(
        account_repository=FakeAccountRepository(existing_identity=existing),
        session_service=FakeSessionService(),
        settings=_settings(),
    )

    with pytest.raises(ApiError, match="already registered"):
        asyncio.run(service.signup(email="test@example.com", password="secret123"))


def test_login_rejects_invalid_password_without_creating_session() -> None:
    user = User(id=uuid4(), display_name="Test", status="active")
    identity = AuthIdentity(
        user_id=user.id,
        user=user,
        provider="email",
        subject="test@example.com",
        password_hash=hash_password("right-password"),
    )
    session_service = FakeSessionService()
    service = AuthAccountService(
        account_repository=FakeAccountRepository(existing_identity=identity),
        session_service=session_service,
        settings=_settings(),
    )

    with pytest.raises(ApiError, match="Email or password is invalid"):
        asyncio.run(service.login(email="test@example.com", password="wrong-password"))

    assert session_service.created_user_id is None


def test_refresh_rotates_token_and_issues_access_for_session_user() -> None:
    user = User(id=uuid4(), display_name="Test", status="active")
    session_id = uuid4()
    session_service = FakeSessionService(device_session=DeviceSession(id=session_id, user_id=user.id, status="active"))
    service = AuthAccountService(
        account_repository=FakeAccountRepository(existing_user=user),
        session_service=session_service,
        settings=_settings(),
    )

    issued = asyncio.run(service.refresh(refresh_token="old-refresh"))

    assert session_service.rotated_raw_token == "old-refresh"
    assert issued.user.id == user.id
    assert issued.refresh_token == "rotated-refresh-token"


def _settings() -> Settings:
    return Settings(
        app_env="test",
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
        auth_jwt_issuer="momcozy-test",
        auth_jwt_audience="momcozy-app",
    )


class FakeAccountRepository:
    def __init__(self, *, existing_identity=None, existing_user=None) -> None:
        self.existing_identity = existing_identity
        self.existing_user = existing_user
        self.created_user = None
        self.created_identity = None

    async def get_identity(self, *, provider: str, subject: str):
        if self.existing_identity and self.existing_identity.provider == provider and self.existing_identity.subject == subject:
            return self.existing_identity
        return None

    async def get_user(self, *, user_id):
        if self.existing_user and self.existing_user.id == user_id:
            return self.existing_user
        return None

    async def create_email_user(self, *, email: str, password_hash: str, display_name: str):
        self.created_user = User(id=uuid4(), display_name=display_name, status="active")
        self.created_identity = AuthIdentity(
            user_id=self.created_user.id,
            user=self.created_user,
            provider="email",
            subject=email,
            email=email,
            password_hash=password_hash,
        )
        return self.created_user, self.created_identity


class FakeSessionService:
    def __init__(self, *, device_session=None) -> None:
        self.repository = FakeSessionRepository(device_session=device_session)
        self.created_user_id = None
        self.created_user_agent_hash = ""
        self.created_ip_hash = ""
        self.rotated_raw_token = ""

    async def create_session(self, *, user_id, device_id: str = "", user_agent_hash: str = "", ip_hash: str = ""):
        self.created_user_id = user_id
        self.created_user_agent_hash = user_agent_hash
        self.created_ip_hash = ip_hash
        session = DeviceSession(id=uuid4(), user_id=user_id, device_id=device_id)
        refresh = RefreshToken(
            session_id=session.id,
            token_hash=refresh_token_hash("refresh-token"),
            family_id=uuid4(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        return CreatedAuthSession(
            device_session=session,
            refresh_token=IssuedRefreshToken(raw_token="refresh-token", record=refresh),
        )

    async def rotate_refresh_token(self, *, raw_token: str):
        self.rotated_raw_token = raw_token
        session_id = self.repository.device_session.id
        refresh = RefreshToken(
            session_id=session_id,
            token_hash=refresh_token_hash("rotated-refresh-token"),
            family_id=uuid4(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        return IssuedRefreshToken(raw_token="rotated-refresh-token", record=refresh)

    async def revoke_session(self, *, session_id):
        self.repository.revoked_session_id = session_id


class FakeSessionRepository:
    def __init__(self, *, device_session=None) -> None:
        self.device_session = device_session
        self.revoked_session_id = None

    async def get_device_session(self, *, session_id):
        if self.device_session and self.device_session.id == session_id:
            return self.device_session
        return None
