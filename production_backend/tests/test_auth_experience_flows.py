import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.modules.auth import authenticate_access_token
from production_backend.app.modules.auth.account_service import AuthAccountService, DeviceContext
from production_backend.app.modules.auth.models import DeviceSession, RefreshToken
from production_backend.app.modules.auth.passwords import verify_password
from production_backend.app.modules.auth.service import AuthSessionService, refresh_token_hash
from production_backend.app.modules.users.models import AuthIdentity, User


def test_email_auth_main_flow_rotates_refresh_tokens_and_revokes_reuse() -> None:
    asyncio.run(_run_email_auth_main_flow())


def test_invite_auth_main_flow_reuses_device_identity_and_rotates_refresh() -> None:
    asyncio.run(_run_invite_auth_main_flow())


async def _run_email_auth_main_flow() -> None:
    settings = Settings(
        app_env="test",
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
        auth_jwt_issuer="momcozy-test",
        auth_jwt_audience="momcozy-app",
    )
    account_repository = InMemoryAuthAccountRepository()
    session_repository = InMemoryAuthSessionRepository()
    session_service = AuthSessionService(
        repository=session_repository,
        token_factory=TokenFactory(["refresh-1", "refresh-2", "refresh-3"]),
        clock=_clock,
    )
    service = AuthAccountService(
        account_repository=account_repository,
        session_service=session_service,
        settings=settings,
    )

    signup = await service.signup(
        email=" Mia@Example.COM ",
        password="right-password",
        display_name="Mia",
        device_context=DeviceContext(device_id="ios-1", user_agent="MomCozy iOS", ip_address="127.0.0.1"),
    )
    signed_up_user = authenticate_access_token(signup.access_token, settings)

    with pytest.raises(ApiError) as duplicate_signup:
        await service.signup(email="mia@example.com", password="right-password")

    refreshed = await service.refresh(refresh_token=signup.refresh_token)
    refreshed_user = authenticate_access_token(refreshed.access_token, settings)

    with pytest.raises(ApiError) as reuse_detected:
        await service.refresh(refresh_token=signup.refresh_token)

    login = await service.login(
        email="mia@example.com",
        password="right-password",
        device_context=DeviceContext(device_id="ios-2", user_agent="MomCozy iOS 2", ip_address="127.0.0.2"),
    )
    logged_in_user = authenticate_access_token(login.access_token, settings)
    await service.logout(session_id=UUID(logged_in_user.session_id))

    identity = account_repository.identities[0]
    first_session = session_repository.sessions[UUID(signed_up_user.session_id)]
    second_session = session_repository.sessions[UUID(logged_in_user.session_id)]

    assert signup.user.display_name == "Mia"
    assert identity.subject == "mia@example.com"
    assert identity.password_hash != "right-password"
    assert verify_password("right-password", identity.password_hash)
    assert signed_up_user.user_id == signup.user.id
    assert refreshed_user.session_id == signed_up_user.session_id
    assert refreshed.refresh_token == "refresh-2"
    assert duplicate_signup.value.code == "conflict"
    assert reuse_detected.value.code == "refresh_token_reuse_detected"
    assert first_session.status == "revoked"
    assert second_session.status == "revoked"
    assert [token.status for token in session_repository.refresh_tokens[:2]] == ["revoked", "revoked"]
    assert session_repository.refresh_tokens[0].token_hash == refresh_token_hash("refresh-1")
    assert "refresh-1" not in {token.token_hash for token in session_repository.refresh_tokens}
    assert login.refresh_token == "refresh-3"


async def _run_invite_auth_main_flow() -> None:
    settings = Settings(
        app_env="test",
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
        auth_jwt_issuer="momcozy-test",
        auth_jwt_audience="momcozy-app",
        auth_invite_codes=("MOMCOZY-BETA",),
    )
    account_repository = InMemoryAuthAccountRepository()
    session_repository = InMemoryAuthSessionRepository()
    session_service = AuthSessionService(
        repository=session_repository,
        token_factory=TokenFactory(["invite-refresh-1", "invite-refresh-2", "invite-refresh-3"]),
        clock=_clock,
    )
    service = AuthAccountService(
        account_repository=account_repository,
        session_service=session_service,
        settings=settings,
    )

    first = await service.invite_login(
        invite_code="momcozy-beta",
        device_context=DeviceContext(device_id="flutter-device-001", user_agent="MomCozy Android", ip_address="127.0.0.1"),
    )
    second = await service.invite_login(
        invite_code="MOMCOZY-BETA",
        device_context=DeviceContext(device_id="flutter-device-001", user_agent="MomCozy Android", ip_address="127.0.0.1"),
    )
    with pytest.raises(ApiError) as bound_to_other_device:
        await service.invite_login(
            invite_code="MOMCOZY-BETA",
            device_context=DeviceContext(device_id="flutter-device-002", user_agent="MomCozy Android", ip_address="127.0.0.2"),
        )
    refreshed = await service.refresh(refresh_token=first.refresh_token)
    first_access = authenticate_access_token(first.access_token, settings)
    refreshed_access = authenticate_access_token(refreshed.access_token, settings)

    with pytest.raises(ApiError) as invalid_code:
        await service.invite_login(
            invite_code="WRONG-CODE",
            device_context=DeviceContext(device_id="flutter-device-001"),
        )

    identity = account_repository.identities[0]

    assert first.user.id == second.user.id
    assert first.refresh_token == "invite-refresh-1"
    assert second.refresh_token == "invite-refresh-2"
    assert refreshed.refresh_token == "invite-refresh-3"
    assert first_access.user_id == first.user.id
    assert refreshed_access.user_id == first.user.id
    assert identity.provider == "invite"
    assert identity.subject == "MOMCOZY-BETA"
    assert identity.device_id == "flutter-device-001"
    assert identity.password_hash == ""
    assert bound_to_other_device.value.code == "permission_denied"
    assert invalid_code.value.code == "authentication_required"


def _clock() -> datetime:
    return datetime(2026, 7, 3, tzinfo=timezone.utc)


class TokenFactory:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens

    def __call__(self) -> str:
        return self.tokens.pop(0)


class InMemoryAuthAccountRepository:
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.identities: list[AuthIdentity] = []

    async def get_identity(self, *, provider: str, subject: str):
        return next(
            (identity for identity in self.identities if identity.provider == provider and identity.subject == subject),
            None,
        )

    async def get_invite_identity(self, *, invite_code: str):
        return next(
            (
                identity
                for identity in self.identities
                if identity.provider == "invite" and (identity.subject == invite_code or identity.subject.startswith(f"{invite_code}:"))
            ),
            None,
        )

    async def get_user(self, *, user_id: UUID):
        return self.users.get(user_id)

    async def create_email_user(self, *, email: str, password_hash: str, display_name: str):
        user = User(id=uuid4(), display_name=display_name, status="active")
        identity = AuthIdentity(
            id=uuid4(),
            user_id=user.id,
            user=user,
            provider="email",
            subject=email,
            email=email,
            password_hash=password_hash,
        )
        self.users[user.id] = user
        self.identities.append(identity)
        return user, identity

    async def bind_invite_identity_device(self, *, identity, device_id: str):
        identity.device_id = device_id
        return identity

    async def create_invite_user(self, *, invite_code: str, device_id: str, display_name: str):
        user = User(id=uuid4(), display_name=display_name, status="active")
        identity = AuthIdentity(
            id=uuid4(),
            user_id=user.id,
            user=user,
            provider="invite",
            subject=invite_code,
            device_id=device_id,
            email="",
            password_hash="",
        )
        self.users[user.id] = user
        self.identities.append(identity)
        return user, identity


class InMemoryAuthSessionRepository:
    def __init__(self) -> None:
        self.sessions: dict[UUID, DeviceSession] = {}
        self.refresh_tokens: list[RefreshToken] = []

    async def create_device_session(self, *, user_id: UUID, device_id: str, user_agent_hash: str, ip_hash: str):
        session = DeviceSession(
            id=uuid4(),
            user_id=user_id,
            device_id=device_id,
            user_agent_hash=user_agent_hash,
            ip_hash=ip_hash,
            status="active",
        )
        self.sessions[session.id] = session
        return session

    async def create_refresh_token(self, *, session_id: UUID, token_hash: str, family_id: UUID | None, expires_at: datetime):
        refresh_token = RefreshToken(
            id=uuid4(),
            session_id=session_id,
            token_hash=token_hash,
            family_id=family_id or uuid4(),
            status="active",
            expires_at=expires_at,
        )
        self.refresh_tokens.append(refresh_token)
        return refresh_token

    async def get_refresh_token_by_hash(self, *, token_hash: str):
        return next((token for token in self.refresh_tokens if token.token_hash == token_hash), None)

    async def get_device_session(self, *, session_id: UUID):
        return self.sessions.get(session_id)

    async def rotate_refresh_token(self, *, refresh_token, new_token_hash: str, expires_at: datetime, rotated_at: datetime):
        refresh_token.status = "rotated"
        refresh_token.rotated_at = rotated_at
        replacement = RefreshToken(
            id=uuid4(),
            session_id=refresh_token.session_id,
            token_hash=new_token_hash,
            family_id=refresh_token.family_id,
            status="active",
            expires_at=expires_at,
        )
        self.refresh_tokens.append(replacement)
        return replacement

    async def revoke_refresh_token(self, *, refresh_token, revoked_at: datetime):
        refresh_token.status = "revoked"
        refresh_token.revoked_at = revoked_at
        return refresh_token

    async def revoke_refresh_token_family(self, *, family_id: UUID, revoked_at: datetime):
        for refresh_token in self.refresh_tokens:
            if refresh_token.family_id == family_id:
                refresh_token.status = "revoked"
                refresh_token.revoked_at = revoked_at

    async def revoke_device_session(self, *, session_id: UUID, revoked_at: datetime):
        session = self.sessions.get(session_id)
        if session is None:
            return None
        session.status = "revoked"
        session.revoked_at = revoked_at
        return session
