from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ...core.errors import ApiError
from ..users.models import AccountStatus, AuthIdentity, User
from .account_service import AuthAccountService, DeviceContext, IssuedTokenPair, normalize_email
from .email import AuthChallengeEmailSender
from .passwords import hash_password
from .repository import AuthAccountRepository

CHALLENGE_TTL = timedelta(minutes=15)
MAX_CHALLENGE_ATTEMPTS = 5
PURPOSE_VERIFY = "verify_email"
PURPOSE_RESET = "reset_password"


class ChallengeRejected(ApiError):
    """The router must commit the failed attempt before returning this error."""

    def __init__(self) -> None:
        super().__init__(code="invalid_or_expired_code", message="Code is invalid or expired. Request a new code.", status=401)


@dataclass(frozen=True)
class RegistrationResult:
    verification_required: bool = True


class AccountLifecycleService:
    def __init__(self, *, accounts: AuthAccountRepository, auth: AuthAccountService,
                 email_sender: AuthChallengeEmailSender, token_key: str, now: Callable[[], datetime] | None = None) -> None:
        self.accounts, self.auth, self.email_sender = accounts, auth, email_sender
        self.token_key = token_key
        self.now = now or (lambda: datetime.now(timezone.utc))

    async def register(self, *, email: str, password: str) -> RegistrationResult:
        self._require_email_configuration()
        normalized = normalize_email(email)
        validate_password(password)
        # Equal expensive password work for existing and new emails.
        password_hash = hash_password(password)
        await self.accounts.lock_email(normalized)
        user = await self.accounts.get_user_by_email(email=normalized)
        if user is None:
            user = await self.accounts.create_pending_email_user(email=normalized, password_hash=password_hash)
        else:
            user = await self.accounts.lock_user(user.id)
        if user is not None and user.status == AccountStatus.EMAIL_UNVERIFIED:
            await self._issue_email_challenge(user=user, purpose=PURPOSE_VERIFY)
        return RegistrationResult()

    async def start_registration(self, *, email: str) -> RegistrationResult:
        self._require_email_configuration()
        normalized = normalize_email(email)
        # No user-chosen password exists until mailbox proof is consumed.
        password_hash = hash_password(secrets.token_urlsafe(32))
        await self.accounts.lock_email(normalized)
        user = await self.accounts.get_user_by_email(email=normalized)
        if user is None:
            user = await self.accounts.create_pending_email_user(email=normalized, password_hash=password_hash)
        else:
            user = await self.accounts.lock_user(user.id)
        if user is not None and user.status == AccountStatus.EMAIL_UNVERIFIED:
            await self._issue_email_challenge(user=user, purpose=PURPOSE_VERIFY)
        return RegistrationResult()

    async def check_registration_code(self, *, email: str, token: str) -> None:
        user = await self._user_for_email(email)
        if user.status != AccountStatus.EMAIL_UNVERIFIED:
            raise ChallengeRejected()
        challenge = await self.accounts.get_email_challenge(user_id=user.id, purpose=PURPOSE_VERIFY)
        now = self.now()
        if challenge is None or challenge.consumed_at is not None or _aware(challenge.expires_at) <= now or challenge.attempts >= MAX_CHALLENGE_ATTEMPTS:
            raise ChallengeRejected()
        if not hmac.compare_digest(challenge.token_hash, self._digest(user, PURPOSE_VERIFY, token.strip())):
            challenge.attempts += 1
            await self.accounts.session.flush()
            raise ChallengeRejected()
        # Only verify_email consumes proof and activates the account.

    async def verify_email(self, *, email: str, token: str, password: str, device: DeviceContext) -> IssuedTokenPair:
        validate_password(password)
        user = await self._user_for_email(email)
        if user.status != AccountStatus.EMAIL_UNVERIFIED:
            raise ChallengeRejected()
        await self._consume_challenge(user=user, purpose=PURPOSE_VERIFY, token=token)
        # Only mailbox proof may change the pending credential; repeat registration cannot.
        identity = next(identity for identity in user.identities if identity.provider == "email")
        identity.password_hash = hash_password(password)
        user.status, user.email_verified_at = AccountStatus.ACTIVE, self.now()
        await self.accounts.invalidate_challenges(user_id=user.id, now=self.now())
        await self.auth.session_service.revoke_user_sessions(user_id=user.id)
        return await self.auth._issue_pair(user=user, device_context=device)

    async def resend_verification(self, *, email: str) -> RegistrationResult:
        self._require_email_configuration()
        user = await self.accounts.get_user_by_email(email=normalize_email(email))
        if user is not None:
            user = await self.accounts.lock_user(user.id)
            if user is not None and user.status == AccountStatus.EMAIL_UNVERIFIED:
                await self._issue_email_challenge(user=user, purpose=PURPOSE_VERIFY)
        return RegistrationResult()

    async def request_password_reset(self, *, email: str) -> RegistrationResult:
        self._require_email_configuration()
        user = await self.accounts.get_user_by_email(email=normalize_email(email))
        if user is not None:
            user = await self.accounts.lock_user(user.id)
            if user is not None and user.status in {AccountStatus.ACTIVE, AccountStatus.EMAIL_UNVERIFIED} and self._can_reset_password(user):
                await self._issue_email_challenge(user=user, purpose=PURPOSE_RESET)
        return RegistrationResult()

    async def reset_password(self, *, email: str, token: str, new_password: str) -> RegistrationResult:
        validate_password(new_password)
        user = await self._user_for_email(email, allow_legacy_reset=True)
        if user.status not in {AccountStatus.ACTIVE, AccountStatus.EMAIL_UNVERIFIED}:
            raise ChallengeRejected()
        await self._consume_challenge(user=user, purpose=PURPOSE_RESET, token=token)
        identity = next((identity for identity in user.identities if identity.provider == "email"), None)
        if identity is None:
            identity = AuthIdentity(user=user, provider="email", subject=user.email, email=user.email)
            self.accounts.session.add(identity)
        identity.password_hash = hash_password(new_password)
        user.status, user.email_verified_at = AccountStatus.ACTIVE, self.now()
        await self.accounts.invalidate_challenges(user_id=user.id, now=self.now())
        await self.auth.session_service.revoke_user_sessions(user_id=user.id)
        return RegistrationResult(verification_required=False)

    async def delete_account(self, *, user: User) -> RegistrationResult:
        locked = await self.accounts.lock_user(user.id)
        if locked is None or locked.status == AccountStatus.DELETED:
            return RegistrationResult(False)
        await self.auth.session_service.revoke_user_sessions(user_id=locked.id)
        await self.accounts.invalidate_challenges(user_id=locked.id, now=self.now())
        await self.accounts.create_deletion_request(user_id=locked.id)
        await self.accounts.anonymize_user(user=locked, marker=secrets.token_hex(8), now=self.now())
        return RegistrationResult(False)

    def _require_email_configuration(self) -> None:
        if len(self.token_key.encode()) < 32:
            raise ApiError(code="auth_email_unavailable", message="Email authentication is temporarily unavailable.", status=503)

    async def _issue_email_challenge(self, *, user: User, purpose: str) -> None:
        self._require_email_configuration()
        now = self.now()
        existing = await self.accounts.get_email_challenge(user_id=user.id, purpose=purpose)
        if existing is not None and now - _aware(existing.sent_at) < timedelta(seconds=60):
            return
        token = f"{secrets.randbelow(100_000_000):08d}"
        challenge = await self.accounts.save_email_challenge(user_id=user.id, purpose=purpose,
            token_hash=self._digest(user, purpose, token), expires_at=now + CHALLENGE_TTL, sent_at=now)
        subject = "Verify your Momcozy email" if purpose == PURPOSE_VERIFY else "Reset your Momcozy password"
        await self.email_sender.send(challenge=challenge, recipient=user.email or "", subject=subject,
            body=f"Your Momcozy security code is:\n\n{token}\n\nIt expires in 15 minutes. If you did not request this, ignore this email.")

    def _can_reset_password(self, user: User) -> bool:
        # Preserve access to accounts created before external sign-in was retired.
        return any(i.provider == "email" for i in user.identities) or (
            user.status == AccountStatus.ACTIVE and user.email_verified_at is not None
            and any(i.provider == "google" for i in user.identities)
        )

    async def _user_for_email(self, email: str, *, allow_legacy_reset: bool = False) -> User:
        self._require_email_configuration()
        user = await self.accounts.get_user_by_email(email=normalize_email(email))
        locked = await self.accounts.lock_user(user.id) if user is not None else None
        if locked is None or not (
            self._can_reset_password(locked) if allow_legacy_reset
            else any(i.provider == "email" for i in locked.identities)
        ):
            raise ChallengeRejected()
        return locked

    async def _consume_challenge(self, *, user: User, purpose: str, token: str) -> None:
        challenge = await self.accounts.get_email_challenge(user_id=user.id, purpose=purpose)
        now = self.now()
        if challenge is None or challenge.consumed_at is not None or _aware(challenge.expires_at) <= now or challenge.attempts >= MAX_CHALLENGE_ATTEMPTS:
            raise ChallengeRejected()
        challenge.attempts += 1
        if not hmac.compare_digest(challenge.token_hash, self._digest(user, purpose, token.strip())):
            await self.accounts.session.flush()
            raise ChallengeRejected()
        challenge.consumed_at = now
        await self.accounts.session.flush()

    def _digest(self, user: User, purpose: str, token: str) -> str:
        value = f"{user.id}:{purpose}:{token}"
        return hmac.new(self.token_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def validate_password(password: str) -> None:
    if not 8 <= len(password) <= 128 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise ApiError(code="validation_failed", message="Use 8–128 characters including a letter and a number.", status=422)
