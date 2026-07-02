from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from ...core.errors import ApiError
from ...core.settings import Settings
from ..users.models import User
from .jwt import issue_access_token
from .passwords import hash_password, verify_password
from .repository import AuthAccountRepository
from .service import AuthSessionService, IssuedRefreshToken


EMAIL_PROVIDER = "email"


@dataclass(frozen=True)
class DeviceContext:
    device_id: str = ""
    user_agent: str = ""
    ip_address: str = ""


@dataclass(frozen=True)
class IssuedTokenPair:
    user: User
    access_token: str
    refresh_token: str
    expires_in: int


class AuthAccountService:
    def __init__(
        self,
        *,
        account_repository: AuthAccountRepository,
        session_service: AuthSessionService,
        settings: Settings,
    ) -> None:
        self.account_repository = account_repository
        self.session_service = session_service
        self.settings = settings

    async def signup(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "",
        device_context: DeviceContext | None = None,
    ) -> IssuedTokenPair:
        normalized_email = normalize_email(email)
        existing = await self.account_repository.get_identity(provider=EMAIL_PROVIDER, subject=normalized_email)
        if existing is not None:
            raise ApiError(code="conflict", message="Email is already registered.", status=409)

        user, _identity = await self.account_repository.create_email_user(
            email=normalized_email,
            password_hash=hash_password(password),
            display_name=display_name.strip(),
        )
        return await self._issue_pair(user=user, device_context=device_context)

    async def login(
        self,
        *,
        email: str,
        password: str,
        device_context: DeviceContext | None = None,
    ) -> IssuedTokenPair:
        normalized_email = normalize_email(email)
        identity = await self.account_repository.get_identity(provider=EMAIL_PROVIDER, subject=normalized_email)
        if identity is None or not verify_password(password, identity.password_hash):
            raise ApiError(code="authentication_required", message="Email or password is invalid.", status=401)

        user = identity.user or await self.account_repository.get_user(user_id=identity.user_id)
        if user is None or user.status != "active":
            raise ApiError(code="permission_denied", message="User account is not active.", status=403)

        return await self._issue_pair(user=user, device_context=device_context)

    async def refresh(self, *, refresh_token: str) -> IssuedTokenPair:
        issued_refresh = await self.session_service.rotate_refresh_token(raw_token=refresh_token)
        device_session = await self.session_service.repository.get_device_session(
            session_id=issued_refresh.record.session_id
        )
        if device_session is None or device_session.status != "active":
            raise ApiError(code="authentication_required", message="Session is no longer active.", status=401)
        user = await self.account_repository.get_user(user_id=device_session.user_id)
        if user is None or user.status != "active":
            raise ApiError(code="permission_denied", message="User account is not active.", status=403)
        return self._tokens_for(user=user, session_id=device_session.id, issued_refresh=issued_refresh)

    async def logout(self, *, session_id: UUID) -> None:
        await self.session_service.revoke_session(session_id=session_id)

    async def _issue_pair(self, *, user: User, device_context: DeviceContext | None) -> IssuedTokenPair:
        context = device_context or DeviceContext()
        created = await self.session_service.create_session(
            user_id=user.id,
            device_id=context.device_id,
            user_agent_hash=_sha256(context.user_agent),
            ip_hash=_sha256(context.ip_address),
        )
        return self._tokens_for(
            user=user,
            session_id=created.device_session.id,
            issued_refresh=created.refresh_token,
        )

    def _tokens_for(self, *, user: User, session_id: UUID, issued_refresh: IssuedRefreshToken) -> IssuedTokenPair:
        access_token, expires_in = issue_access_token(
            user_id=user.id,
            session_id=session_id,
            settings=self.settings,
            roles=frozenset({"user"}),
        )
        return IssuedTokenPair(
            user=user,
            access_token=access_token,
            refresh_token=issued_refresh.raw_token,
            expires_in=expires_in,
        )


def normalize_email(email: str) -> str:
    normalized = str(email or "").strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ApiError(code="validation_failed", message="Email is invalid.", status=422)
    return normalized


def _sha256(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
