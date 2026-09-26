"""Prepare a verified email account for the local Docker development stack."""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.modules.auth.passwords import hash_password
from app.modules.auth.repository import AuthAccountRepository
from app.modules.profiles.repository import ProfileRepository
from app.modules.users.models import AccountStatus, User


LOCAL_TEST_EMAIL = "dev@example.test"
LOCAL_TEST_PASSWORD = "MomcozyLocal123!"


def require_local(settings: Settings) -> None:
    if settings.app_env != "local":
        raise RuntimeError("The development account can only be seeded with APP_ENV=local.")


async def ensure_local_test_account(session: AsyncSession, settings: Settings, *, today: date | None = None) -> UUID:
    require_local(settings)
    accounts = AuthAccountRepository(session)
    await accounts.lock_email(LOCAL_TEST_EMAIL)
    user = await accounts.get_user_by_email(email=LOCAL_TEST_EMAIL)
    if user is None:
        user, _identity = await accounts.create_email_user(
            email=LOCAL_TEST_EMAIL,
            password_hash=hash_password(LOCAL_TEST_PASSWORD),
        )
        user.status = AccountStatus.ACTIVE
        user.email_verified_at = datetime.now(timezone.utc)
    await _fill_missing_demo_profile(session, accounts, user, today=today)
    return user.id


async def create_fresh_local_test_account(session: AsyncSession, settings: Settings, *, today: date | None = None) -> tuple[UUID, str, str]:
    require_local(settings)
    email = f"emulator-{secrets.token_hex(16)}@example.test"
    password = f"Mc{secrets.token_hex(16)}A9"
    accounts = AuthAccountRepository(session)
    user, _identity = await accounts.create_email_user(email=email, password_hash=hash_password(password))
    user.status = AccountStatus.ACTIVE
    user.email_verified_at = datetime.now(timezone.utc)
    await _fill_missing_demo_profile(session, accounts, user, today=today)
    return user.id, email, password


async def _fill_missing_demo_profile(session: AsyncSession, accounts: AuthAccountRepository, user: User, *, today: date | None = None) -> None:
    # Fill only missing demo facts; preserve edits, status, sessions and dates.
    await accounts.lock_user(user.id)
    if user.status == AccountStatus.ACTIVE:
        profiles = ProfileRepository(session)
        personal = await profiles.get_user_profile(user_id=user.id)
        if personal is None or personal.preferred_name is None:
            await profiles.upsert_user_profile(user_id=user.id, values={"preferred_name": "Mia"})
        maternal = await profiles.get_maternal_profile(owner_user_id=user.id)
        if maternal is None or maternal.latest_delivery_date is None:
            local_today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
            await profiles.upsert_maternal_profile(
                owner_user_id=user.id,
                values={"latest_delivery_date": local_today - timedelta(days=21)},
            )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="Create a unique, verified emulator account")
    args = parser.parse_args()
    settings = Settings.from_env()
    require_local(settings)
    engine = create_db_engine(settings)
    try:
        async with create_session_factory(engine).begin() as session:
            if args.fresh:
                user_id, email, password = await create_fresh_local_test_account(session, settings)
            else:
                user_id = await ensure_local_test_account(session, settings)
        if args.fresh:
            print(json.dumps({"user_id": str(user_id), "email": email, "password": password}))
        else:
            print(f"Local test account ready: {LOCAL_TEST_EMAIL} (user {user_id})")
            print(f"Initial password: {LOCAL_TEST_PASSWORD} (existing credentials are preserved)")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
