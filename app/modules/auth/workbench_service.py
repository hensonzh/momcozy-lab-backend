from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pyotp
from cryptography.fernet import Fernet, InvalidToken

from ...core.errors import ApiError, ErrorEnvelope
from ...core.settings import Settings
from ..audit.service import AuditService
from .account_service import AuthAccountService, DeviceContext, IssuedTokenPair
from .jwt import issue_access_token
from .permissions import STANDARD_USER_PERMISSIONS
from .service import AuthSessionService
from .workbench_models import WorkbenchLoginChallenge
from .workbench_repository import WorkbenchAuthRepository
from .workbench_schemas import WorkbenchChallengeRead

CHALLENGE_TTL = timedelta(minutes=5)
LOCKOUT = timedelta(minutes=10)


def mfa_cipher(settings: Settings) -> Fernet:
    try:
        return Fernet(settings.ibclc_mfa_encryption_key.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ApiError(code="mfa_not_configured", message="Workbench verification is not configured.", status=503) from exc


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkbenchVerification:
    issued: IssuedTokenPair | None = field(default=None, repr=False)
    error: ErrorEnvelope | None = None


class WorkbenchAuthService:
    def __init__(self, repository: WorkbenchAuthRepository, accounts: AuthAccountService, sessions: AuthSessionService,
        audit: AuditService, settings: Settings, *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.repository, self.accounts, self.sessions, self.audit, self.settings, self.now = repository, accounts, sessions, audit, settings, now

    async def begin(self, *, email: str, password: str, device: DeviceContext, request_id: str) -> WorkbenchChallengeRead:
        mfa_cipher(self.settings)
        user = await self.accounts.authenticate_email(email=email, password=password)
        credential = await self.repository.credential(user.id)
        provider = await self.repository.provider(user.id)
        if provider is None or not provider.active or credential is None:
            raise ApiError(code="workbench_not_enabled", message="This account has no configured workbench access.", status=403)
        if credential.locked_until and credential.locked_until > self.now():
            raise ApiError(code="mfa_locked", message="Too many verification attempts. Try again later.", status=429)
        if await self.repository.recent_challenges(user.id, self.now() - CHALLENGE_TTL) >= 5:
            raise ApiError(code="mfa_rate_limited", message="Too many sign-in attempts. Try again later.", status=429)
        if credential.locked_until is not None:
            credential.failed_attempts, credential.locked_until = 0, None
        token = secrets.token_urlsafe(32)
        challenge = WorkbenchLoginChallenge(provider_id=user.id, token_hash=_hash(token), credential_version=credential.version,
            device_id=device.device_id, user_agent_hash=_hash(device.user_agent), ip_hash=_hash(device.ip_address),
            created_at=self.now(), expires_at=self.now() + CHALLENGE_TTL)
        await self.repository.add(challenge)
        await self.audit.record(actor_user_id=user.id, action="workbench.auth.challenge_created", resource_type="workbench_login",
            resource_id=str(challenge.id), request_id=request_id)
        return WorkbenchChallengeRead(challenge=token, expires_at=challenge.expires_at)

    async def verify(self, *, challenge_token: str, code: str, request_id: str) -> WorkbenchVerification:
        cipher = mfa_cipher(self.settings)
        challenge = await self.repository.challenge(_hash(challenge_token))
        if challenge is None:
            raise ApiError(code="mfa_challenge_expired", message="Start a new sign-in attempt.", status=401)
        # Match login/reset/rotation: account first, then MFA credential. Re-read
        # the challenge below after waiting so password reset can invalidate it.
        user = await self.accounts.account_repository.lock_user(challenge.provider_id)
        credential = await self.repository.credential(challenge.provider_id)
        provider = await self.repository.provider(challenge.provider_id)
        # All OTP verification for a provider shares the same credential row lock.
        challenge = await self.repository.challenge(_hash(challenge_token))
        assert challenge is not None
        if credential is None or provider is None or not provider.active:
            raise ApiError(code="workbench_not_enabled", message="Workbench access is not active.", status=403)
        if challenge.consumed_at is not None or challenge.expires_at <= self.now() or challenge.credential_version != credential.version:
            raise ApiError(code="mfa_challenge_expired", message="Start a new sign-in attempt.", status=401)
        if credential.locked_until and credential.locked_until > self.now():
            raise ApiError(code="mfa_locked", message="Too many verification attempts. Try again later.", status=429)
        try:
            secret = cipher.decrypt(credential.encrypted_secret.encode("ascii")).decode("ascii")
            totp = pyotp.TOTP(secret)
            counter = int(self.now().timestamp()) // totp.interval
            matched = next((value for value in (counter, counter - 1, counter + 1) if value > credential.last_counter
                and pyotp.utils.strings_equal(totp.generate_otp(value), code)), None)
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise ApiError(code="mfa_not_configured", message="Workbench verification is unavailable.", status=503) from exc
        if matched is None:
            credential.failed_attempts += 1
            locked = credential.failed_attempts >= 5
            if locked:
                credential.locked_until = self.now() + LOCKOUT
            await self.repository.flush()
            await self.audit.record(actor_user_id=provider.user_id, action="workbench.auth.verification_failed", resource_type="workbench_login",
                resource_id=str(challenge.id), request_id=request_id, outcome="denied", details={"locked": locked})
            # Return a normal result so the API transaction commits the failure counter.
            # Raising here would roll back lockout state in the session dependency.
            return WorkbenchVerification(error=ErrorEnvelope(code="mfa_locked" if locked else "mfa_invalid",
                message="Try again later." if locked else "The code is invalid or was already used.", status=429 if locked else 401, request_id=request_id))
        if user is None or user.status != "active":
            raise ApiError(code="permission_denied", message="User account is not active.", status=403)
        credential.last_counter, credential.failed_attempts, credential.locked_until = matched, 0, None
        challenge.consumed_at = self.now()
        created = await self.sessions.create_session(user_id=user.id, device_id=challenge.device_id,
            user_agent_hash=challenge.user_agent_hash, ip_hash=challenge.ip_hash)
        created.device_session.mfa_verified_at = self.now()
        await self.repository.flush()
        access_token, expires_in = issue_access_token(user_id=user.id, session_id=created.device_session.id, settings=self.settings,
            roles=frozenset({"ibclc"}), permissions=STANDARD_USER_PERMISSIONS)
        await self.audit.record(actor_user_id=user.id, action="workbench.auth.signed_in", resource_type="device_session",
            resource_id=str(created.device_session.id), request_id=request_id)
        return WorkbenchVerification(issued=IssuedTokenPair(user=user, access_token=access_token,
            refresh_token=created.refresh_token.raw_token, expires_in=expires_in))
