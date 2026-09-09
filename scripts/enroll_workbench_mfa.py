"""Provision an authenticator for an existing provider account; no public enrollment endpoint."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pyotp  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.errors import ApiError  # noqa: E402
from app.core.settings import Settings  # noqa: E402
from app.infrastructure.db.session import create_db_engine, create_session_factory  # noqa: E402
from app.modules.audit.repository import AuditRepository  # noqa: E402
from app.modules.audit.service import AuditService  # noqa: E402
from app.modules.auth.account_service import normalize_email  # noqa: E402
from app.modules.auth.models import DeviceSession  # noqa: E402
from app.modules.auth.repository import AuthAccountRepository, AuthSessionRepository  # noqa: E402
from app.modules.auth.workbench_models import WorkbenchMfaCredential  # noqa: E402
from app.modules.auth.workbench_repository import WorkbenchAuthRepository  # noqa: E402
from app.modules.auth.workbench_service import mfa_cipher  # noqa: E402


async def enroll(email: str, *, rotate: bool, settings: Settings) -> str:
    cipher = mfa_cipher(settings)
    engine = create_db_engine(settings)
    try:
        async with create_session_factory(engine).begin() as session:
            identity = await AuthAccountRepository(session).get_identity(provider="email", subject=normalize_email(email))
            if identity is None:
                raise ValueError("Create the email account and provider profile first.")
            repository = WorkbenchAuthRepository(session)
            credential = await repository.credential(identity.user_id)
            if await repository.provider(identity.user_id) is None:
                raise ValueError("Create the provider profile before enrolling MFA.")
            if credential is not None and not rotate:
                raise ValueError("MFA is already enrolled. Use --rotate to replace it and revoke workbench sessions.")
            secret = pyotp.random_base32()
            encrypted = cipher.encrypt(secret.encode("ascii")).decode("ascii")
            if credential is None:
                credential = WorkbenchMfaCredential(provider_id=identity.user_id, encrypted_secret=encrypted)
                session.add(credential)
            else:
                credential.encrypted_secret, credential.last_counter, credential.failed_attempts, credential.locked_until = encrypted, -1, 0, None
                credential.version += 1
                credential.updated_at = datetime.now(timezone.utc)
                devices = list(await session.scalars(select(DeviceSession).where(DeviceSession.user_id == identity.user_id,
                    DeviceSession.mfa_verified_at.is_not(None), DeviceSession.status == "active")))
                for device in devices:
                    await AuthSessionRepository(session).revoke_device_session(session_id=device.id, revoked_at=datetime.now(timezone.utc))
            await session.flush()
            await AuditService(repository=AuditRepository(session)).record(actor_user_id=None, actor_type="system", actor_service="workbench-enrollment",
                action="workbench.auth.mfa_rotated" if rotate else "workbench.auth.mfa_enrolled", resource_type="care_provider", resource_id=str(identity.user_id))
        return pyotp.TOTP(secret).provisioning_uri(name=normalize_email(email), issuer_name="MomCozy IBCLC")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    try:
        uri = asyncio.run(enroll(args.email, rotate=args.rotate, settings=Settings.from_env()))
    except (ValueError, ApiError) as exc:
        parser.exit(1, f"{exc}\n")
    print("Add this secret enrollment URI to the provider's authenticator. Do not put it in application logs.", file=sys.stderr)
    print(uri)


if __name__ == "__main__":
    main()
