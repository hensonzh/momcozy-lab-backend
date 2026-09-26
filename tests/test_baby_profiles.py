import asyncio
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService, IdempotencyService
from app.modules.baby.profile_schemas import BabyProfileUpdate, BabyProfileWrite
from app.modules.baby.profile_service import BabyProfileService
from app.modules.baby.repository import BabyRecordRepository
from product_database import database, postgres


def service(session, now):
    audit = AuditRepository(session)
    return BabyProfileService(BabyRecordRepository(session), AuditService(repository=audit), IdempotencyService(repository=audit), now=lambda: now)


@pytest.mark.parametrize('extra', [
    {'sex_at_birth': 'female'}, {'birth_weight_kg': 3.2}, {'gestational_age_at_birth_days': 258},
    {'sex': 'intersex'}, {'age_label': '2 months'}, {'feeding_mode': 'pumping'}, {'timezone': 'GMT+8'},
])
def test_profile_contract_rejects_retired_fields_and_values(extra):
    with pytest.raises(ValidationError):
        BabyProfileWrite.model_validate({'name': 'Baby', 'timezone': 'UTC', **extra})


@postgres
def test_profiles_are_owner_scoped_idempotent_and_versioned_with_calendar_birth_dates():
    async def run():
        async with database() as (sessions, booking, clock, provider, owners, episodes):
            now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)
            body = BabyProfileWrite(name=' Baby ', birth_date=date(2026, 9, 11), sex='female', timezone='Pacific/Kiritimati')
            async with sessions.begin() as session:
                saved = await service(session, now).create(owners[0], body, 'create-once', 'create')
                baby_id = saved.id
                assert saved.name == 'Baby' and saved.birth_date == date(2026, 9, 11)
                assert saved.feeding_mode == 'unknown' and saved.version == 1
            async with sessions.begin() as session:
                api = service(session, now)
                assert (await api.create(owners[0], body, 'create-once', 'retry')).id == baby_id
                assert len((await api.list(owners[0])).items) == 1
                assert (await api.list(owners[1])).items == []
            update = BabyProfileUpdate(**{**body.model_dump(), 'name': 'Luna'}, expected_version=1)
            async with sessions.begin() as session:
                api = service(session, now)
                with pytest.raises(ApiError) as foreign:
                    await api.update(owners[1], baby_id, update, 'foreign')
                assert foreign.value.status == 404
            async def edit(name):
                async with sessions.begin() as session:
                    return await service(session, now).update(owners[0], baby_id, update.model_copy(update={'name': name}), 'edit')
            results = await asyncio.gather(edit('Luna'), edit('Milo'), return_exceptions=True)
            assert sum(isinstance(value, ApiError) and value.code == 'version_conflict' for value in results) == 1
            winner = next(value for value in results if not isinstance(value, Exception))
            async with sessions.begin() as session:
                repeated = await service(session, now).update(owners[0], baby_id, update.model_copy(update={'name': winner.name}), 'retry-edit')
                assert repeated.version == 2
                with pytest.raises(ApiError) as future:
                    await service(session, now).create(owners[0], body.model_copy(update={'timezone': 'Pacific/Honolulu'}), 'future', 'future')
                assert future.value.status == 422
    asyncio.run(run())


@postgres
def test_baby_profile_http_contract_uses_session_owner_and_private_responses():
    import httpx
    from app.api.dependencies import require_current_user
    from app.core.settings import Settings
    from app.factory import create_app
    from app.infrastructure.db import get_session
    from app.modules.auth import CurrentUser

    async def run():
        async with database() as (sessions, booking, clock, provider, owners, episodes):
            app = create_app(Settings(app_env='test'))
            actor = CurrentUser(user_id=owners[0], subject=str(owners[0]), session_id='test', token_id='test', roles=frozenset(), permissions=frozenset())
            async def session_dependency():
                async with sessions.begin() as session:
                    yield session
            app.dependency_overrides[get_session] = session_dependency
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.get('/v1/babies')).status_code == 401
                app.dependency_overrides[require_current_user] = lambda: actor
                payload = {'name': '宝宝', 'sex': 'female', 'birth_date': '2026-08-18', 'timezone': 'Asia/Shanghai'}
                assert (await client.post('/v1/babies', json=payload)).status_code == 422
                saved = await client.post('/v1/babies', json=payload, headers={'Idempotency-Key': 'http-profile'})
                assert saved.status_code == 201 and saved.headers['cache-control'] == 'private, no-store'
                profile = saved.json()
                assert set(profile) == {'id', 'name', 'sex', 'birth_date', 'feeding_mode', 'version', 'created_at', 'updated_at'}
                listing = await client.get('/v1/babies')
                assert listing.json()['items'] == [profile] and listing.headers['cache-control'] == 'private, no-store'
                assert (await client.get('/v1/profile/infants')).status_code == 404
                assert (await client.post('/v1/profile/infants', json=payload)).status_code == 404
                changed = await client.put(f"/v1/babies/{profile['id']}", json={**payload, 'name': 'Luna', 'expected_version': 1})
                assert changed.status_code == 200 and changed.json()['version'] == 2
                stale = await client.put(f"/v1/babies/{profile['id']}", json={**payload, 'name': 'Milo', 'expected_version': 1})
                assert stale.status_code == 409 and stale.json()['error']['code'] == 'version_conflict'
    asyncio.run(run())
