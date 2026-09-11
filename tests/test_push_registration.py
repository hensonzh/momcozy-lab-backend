import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.modules.auth.current_user import CurrentUser
from app.modules.auth.models import DeviceSession
from app.modules.notifications.models import PushInstallation
from app.modules.notifications.push_registration import PushRegistrationService, active_installations
from app.modules.notifications.schemas import PushInstallationWrite
from app.modules.users.models import User
from tests.test_account_lifecycle import account_case

KEY = 'isolated-push-test-encryption-key-' + 'x' * 32
INSTALLATION_SECRET = 'isolated-installation-secret-' + 's' * 32


@asynccontextmanager
async def registration_case():
    async with account_case() as (sessions, _lifecycle, _mail, clock, _settings):
        async with sessions.kw['bind'].begin() as connection:
            await connection.run_sync(PushInstallation.__table__.create)
        actors = []
        async with sessions.begin() as session:
            for _ in range(2):
                user = User()
                session.add(user)
                await session.flush()
                device = DeviceSession(user_id=user.id, device_id=str(uuid4()))
                session.add(device)
                await session.flush()
                actors.append(CurrentUser(user_id=user.id, subject=str(user.id), session_id=str(device.id),
                    token_id=str(uuid4()), roles=frozenset({'user'}), permissions=frozenset()))
        def service(session):
            return PushRegistrationService(session, token_key=KEY, now=lambda: clock[0])
        yield sessions, service, actors, clock


def registration(installation_id, *, revision=1, permission='authorized', token='synthetic-device-token', secret=INSTALLATION_SECRET):
    return PushInstallationWrite(installation_id=installation_id, installation_secret=secret, revision=revision,
        platform='android', permission=permission, token=token, locale='en')


def test_token_rotation_and_out_of_order_permission_sync_preserve_current_state():
    async def run():
        async with registration_case() as (sessions, service, actors, clock):
            installation_id = uuid4()
            async with sessions.begin() as session:
                first = await service(session).register(actors[0], registration(installation_id))
                assert first.token_registered
                row = await session.get(PushInstallation, installation_id)
                assert 'synthetic-device-token' not in row.encrypted_token
                initial_binding = first.binding_id
                await service(session).register(actors[0], registration(installation_id, revision=3, permission='denied', token=None))
                await service(session).register(actors[0], registration(installation_id, revision=2))
                assert not await active_installations(session, actors[0].user_id, now=clock[0])
                current = await service(session).register(actors[0], registration(installation_id, revision=4, token='rotated-synthetic-token'))
                assert current.binding_id == initial_binding
                assert len(await active_installations(session, actors[0].user_id, now=clock[0])) == 1
                assert len(list(await session.scalars(select(PushInstallation)))) == 1
    asyncio.run(run())


def test_account_switch_unbinds_old_user_and_rejects_installation_takeover():
    async def run():
        async with registration_case() as (sessions, service, actors, clock):
            installation_id = uuid4()
            async with sessions.begin() as session:
                first = await service(session).register(actors[0], registration(installation_id))
                with pytest.raises(ApiError) as denied:
                    await service(session).register(actors[1], registration(installation_id, revision=2, secret='wrong-' + 'x' * 48))
                assert denied.value.status == 403
                second = await service(session).register(actors[1], registration(installation_id, revision=2))
                assert second.binding_id != first.binding_id
                assert not await active_installations(session, actors[0].user_id, now=clock[0])
                assert len(await active_installations(session, actors[1].user_id, now=clock[0])) == 1
                with pytest.raises(ApiError):
                    await service(session).detach(actors[0], installation_id, INSTALLATION_SECRET)
    asyncio.run(run())


def test_logout_keeps_device_token_and_only_unbinds_current_installation():
    async def run():
        async with registration_case() as (sessions, service, actors, clock):
            first_id, second_id = uuid4(), uuid4()
            async with sessions.begin() as session:
                await service(session).register(actors[0], registration(first_id))
                # A second active login of the same account is independent.
                device = DeviceSession(user_id=actors[0].user_id, device_id=str(uuid4()))
                session.add(device)
                await session.flush()
                second_actor = CurrentUser(user_id=actors[0].user_id, subject=str(actors[0].user_id), session_id=str(device.id),
                    token_id=str(uuid4()), roles=frozenset({'user'}), permissions=frozenset())
                await service(session).register(second_actor, registration(second_id, token='second-device-token'))
                await service(session).detach(actors[0], first_id, INSTALLATION_SECRET)
                row = await session.get(PushInstallation, first_id)
                assert row.encrypted_token and row.owner_user_id is None and row.session_id is None
                active = await active_installations(session, actors[0].user_id, now=clock[0])
                assert [device.id for device in active] == [second_id]
                (await session.get(DeviceSession, device.id)).status = 'revoked'
                await session.flush()
                assert not await active_installations(session, actors[0].user_id, now=clock[0])
    asyncio.run(run())


@pytest.mark.parametrize('permission', ['not_determined', 'denied', 'unavailable'])
def test_permission_or_missing_token_never_grants_send_eligibility(permission):
    async def run():
        async with registration_case() as (sessions, service, actors, clock):
            async with sessions.begin() as session:
                await service(session).register(actors[0], registration(uuid4(), permission=permission))
                await service(session).register(actors[0], registration(uuid4(), token=None))
                assert not await active_installations(session, actors[0].user_id, now=clock[0])
    asyncio.run(run())


def test_reinstall_transfers_a_duplicate_token_and_missing_token_does_not_restore_an_invalid_one():
    async def run():
        async with registration_case() as (sessions, service, actors, clock):
            previous, fresh = uuid4(), uuid4()
            async with sessions.begin() as session:
                await service(session).register(actors[0], registration(previous))
                await service(session).register(actors[0], registration(fresh))
                old = await session.get(PushInstallation, previous)
                assert old.encrypted_token is None and old.token_hash is None
                assert [item.id for item in await active_installations(session, actors[0].user_id, now=clock[0])] == [fresh]
                current = await session.get(PushInstallation, fresh)
                current.invalidated_at = clock[0]
                synced = await service(session).register(actors[0], registration(fresh, revision=2, token=None))
                assert not synced.token_registered
                rotated = await service(session).register(actors[0], registration(fresh, revision=3, token='new-installation-provider-token'))
                assert rotated.token_registered
    asyncio.run(run())
