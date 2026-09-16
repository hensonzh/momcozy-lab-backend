import asyncio
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import func, select

from app.modules.auth.account_service import DeviceContext
from app.modules.auth.passwords import hash_password, verify_password
from app.modules.users.models import AuthIdentity, User
from app.infrastructure.db.base import Base
from app.modules.profiles.models import MaternalProfile, UserProfile
from scripts.seed_local_test_account import LOCAL_TEST_EMAIL, LOCAL_TEST_PASSWORD, ensure_local_test_account, require_local
from tests.auth_key_material import auth_settings
from tests.test_account_lifecycle import account_case


@pytest.mark.parametrize("environment", ["test", "staging", "prod", "production"])
def test_test_account_rejects_non_local_environments(environment):
    with pytest.raises(RuntimeError, match="APP_ENV=local"):
        require_local(auth_settings(app_env=environment))


def test_local_account_is_verified_can_login_and_repeated_seed_preserves_it():
    async def run():
        async with account_case() as (sessions, lifecycle, _mail, _clock, settings):
            async with sessions.kw["bind"].begin() as connection:
                await connection.run_sync(lambda sync: Base.metadata.create_all(
                    sync, tables=[UserProfile.__table__, MaternalProfile.__table__],
                ))
            local = replace(settings, app_env="local")
            async with sessions.begin() as session:
                user_id = await ensure_local_test_account(session, local, today=date(2026, 9, 12))
                profile = await session.scalar(select(UserProfile))
                maternal = await session.scalar(select(MaternalProfile))
                assert profile.preferred_name == "Mia"
                assert maternal.latest_delivery_date == date(2026, 8, 22)
                profile.preferred_name = "Edited name"
            async with sessions.begin() as session:
                assert await ensure_local_test_account(session, local, today=date(2026, 9, 13)) == user_id
                profile = await session.scalar(select(UserProfile))
                maternal = await session.scalar(select(MaternalProfile))
                assert profile.preferred_name == "Edited name"
                assert maternal.latest_delivery_date == date(2026, 8, 22)
            async with sessions.begin() as session:
                issued = await lifecycle(session).auth.login(
                    email=LOCAL_TEST_EMAIL, password=LOCAL_TEST_PASSWORD,
                    device_context=DeviceContext(device_id="local-test"),
                )
                assert issued.user.id == user_id
                assert issued.user.email_verified_at is not None
                assert issued.user.status == "active"
                assert issued.access_token and issued.refresh_token
                user = await session.get(User, user_id)
                identity = await session.scalar(select(AuthIdentity).where(AuthIdentity.user_id == user_id))
                user.status = "disabled"
                identity.password_hash = hash_password("changed-password-123")
            async with sessions.begin() as session:
                assert await ensure_local_test_account(session, local) == user_id
                assert await session.scalar(select(func.count()).select_from(User)) == 1
                user = await session.get(User, user_id)
                identity = await session.scalar(select(AuthIdentity).where(AuthIdentity.user_id == user_id))
                assert user.status == "disabled"
                assert verify_password("changed-password-123", identity.password_hash)
    asyncio.run(run())
