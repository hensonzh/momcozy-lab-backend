
from app.modules.baby.profile_models import BabyProfile
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import ApiError
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService, IdempotencyService
from app.modules.baby.models import BabyRecord
from app.modules.baby.repository import BabyRecordRepository
from app.modules.baby.schemas import BabyRecordBatchWrite, BabyRecordUpdate, BabyRecordWrite
from app.modules.baby.service import BabyRecordService
from test_care_booking import database, postgres

AT = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
FEED = {'kind': 'feeding', 'occurred_at': AT.isoformat(), 'method': 'breastfeeding', 'side': 'left'}


@pytest.mark.parametrize('observation', [
    {**FEED, 'volume_ml': 60}, {**FEED, 'side': None}, {**FEED, 'method': 'pump'},
    {**FEED, 'duration_minutes': 0}, {**FEED, 'duration_minutes': 241},
    {**FEED, 'occurred_at': '2026-09-01T08:00:00'}, {**FEED, 'baby_id': str(uuid4())},
    {'kind': 'feeding', 'occurred_at': AT, 'method': 'formula', 'volume_ml': 1001},
    {'kind': 'diaper', 'occurred_at': AT, 'diaper_kind': 'wet', 'color': 'red'},
    {'kind': 'sleep', 'occurred_at': AT, 'ended_at': AT},
    {'kind': 'growth', 'recorded_on': AT.date(), 'timezone': 'UTC', 'metric': 'weight', 'value': float('nan')},
    {'kind': 'growth', 'recorded_on': AT.date(), 'timezone': 'UTC', 'metric': 'weight', 'value': 51},
    {'kind': 'development', 'recorded_on': AT.date(), 'timezone': 'UTC', 'item_id': 'invented-score', 'status': 'observed'},
])
def test_baby_record_contract_rejects_ambiguous_values_and_old_fields(observation):
    with pytest.raises(ValidationError):
        BabyRecordWrite.model_validate({'observation': observation})


def service(session, now):
    audit = AuditRepository(session)
    return BabyRecordService(BabyRecordRepository(session), AuditService(repository=audit), IdempotencyService(repository=audit), now=lambda: now)


async def babies(sessions, owners):
    async with sessions.begin() as session:
        values = [BabyProfile(owner_user_id=owners[0], name='Test baby A'), BabyProfile(owner_user_id=owners[0], name='Test baby B'), BabyProfile(owner_user_id=owners[1], name='Other baby')]
        session.add_all(values)
        await session.flush()
        return [value.id for value in values]


@postgres
def test_records_preserve_unknown_values_and_isolate_babies_through_delete_restore_and_replay():
    async def run():
        async with database() as (sessions, booking, now, provider, owners, episodes):
            first, second, foreign = await babies(sessions, owners)
            body = BabyRecordWrite.model_validate({'observation': FEED})
            async with sessions.begin() as session:
                saved = await service(session, now).create(owners[0], first, body.observation, 'feeding-once', 'create')
                record_id = saved.id
                assert saved.observation.volume_ml is None and saved.observation.duration_minutes is None
            async with sessions.begin() as session:
                api = service(session, now)
                repeated = await api.create(owners[0], first, body.observation, 'feeding-once', 'retry')
                assert repeated.id == record_id and repeated.version == 1
                assert (await api.list(owners[0], second, AT.date(), AT.date() + timedelta(days=1), timezone_name='UTC', kind=None, offset=0, limit=100)).items == []
                with pytest.raises(ApiError) as other_baby:
                    await api.set_deleted(owners[0], second, record_id, 1, True, 'wrong-baby')
                assert other_baby.value.status == 404
                with pytest.raises(ApiError) as other_owner:
                    await api.list(owners[0], foreign, AT.date(), AT.date() + timedelta(days=1), timezone_name='UTC', kind=None, offset=0, limit=100)
                assert other_owner.value.status == 404
                update = BabyRecordUpdate.model_validate({'expected_version': 1, 'observation': {'kind': 'feeding', 'occurred_at': AT, 'method': 'expressed_milk', 'volume_ml': 60}})
                changed = await api.update(owners[0], first, record_id, update, 'edit')
                assert changed.version == 2 and changed.observation.side is None
                assert (await api.update(owners[0], first, record_id, update, 'retry-edit')).version == 2
                deleted = await api.set_deleted(owners[0], first, record_id, 2, True, 'delete')
                assert deleted.version == 3
                assert (await api.list(owners[0], first, AT.date(), AT.date() + timedelta(days=1), timezone_name='UTC', kind=None, offset=0, limit=100)).items == []
                with pytest.raises(ApiError) as stale_create:
                    await api.create(owners[0], first, body.observation, 'feeding-once', 'old-create')
                assert stale_create.value.code == 'record_deleted'
                restored = await api.set_deleted(owners[0], first, record_id, 3, False, 'restore')
                assert restored.version == 4 and restored.observation.volume_ml == 60
                assert (await api.set_deleted(owners[0], first, record_id, 3, False, 'retry-restore')).version == 4
            async with sessions.begin() as session:
                assert len(list(await session.scalars(select(BabyRecord)))) == 1
                result = await service(session, now).list(owners[0], first, AT.date(), AT.date() + timedelta(days=1), timezone_name='UTC', kind='feeding', offset=0, limit=100)
                assert result.total == 1 and result.items[0].version == 4
    asyncio.run(run())


@postgres
def test_active_sleep_is_single_per_baby_and_midnight_queries_include_overlap():
    async def run():
        async with database() as (sessions, booking, now, provider, owners, episodes):
            first, second, foreign = await babies(sessions, owners)
            start = AT.replace(hour=23)
            end = start + timedelta(hours=2)
            observation = BabyRecordWrite.model_validate({'observation': {'kind': 'sleep', 'occurred_at': start}}).observation
            async def create(key):
                async with sessions.begin() as session:
                    return await service(session, now).create(owners[0], first, observation, key, key)
            results = await asyncio.gather(create('sleep-a'), create('sleep-b'), return_exceptions=True)
            saved = next(value for value in results if not isinstance(value, Exception))
            conflicts = [value for value in results if isinstance(value, ApiError)]
            assert len(conflicts) == 1 and conflicts[0].code == 'active_sleep_exists'
            async with sessions.begin() as session:
                api = service(session, now)
                future_day = now.date() + timedelta(days=1)
                assert (await api.list(owners[0], first, future_day, future_day + timedelta(days=1), timezone_name='UTC', kind='sleep', offset=0, limit=100)).total == 0
                completed = await api.update(owners[0], first, saved.id, BabyRecordUpdate.model_validate({'expected_version': 1, 'observation': {'kind': 'sleep', 'occurred_at': start, 'ended_at': end}}), 'wake')
                midnight = start.replace(hour=0) + timedelta(days=1)
                result = await api.list(owners[0], first, midnight.date(), midnight.date() + timedelta(days=1), timezone_name='UTC', kind='sleep', offset=0, limit=100)
                assert result.total == 1 and result.items[0].id == saved.id
                assert result.items[0].observation.ended_at == end and completed.version == 2
                await api.update(owners[0], first, saved.id, BabyRecordUpdate.model_validate({'expected_version': 2, 'observation': {'kind': 'sleep', 'occurred_at': start, 'ended_at': midnight}}), 'midnight-end')
                assert (await api.list(owners[0], first, midnight.date(), midnight.date() + timedelta(days=1), timezone_name='UTC', kind='sleep', offset=0, limit=100)).total == 0
    asyncio.run(run())


@postgres
def test_growth_form_saves_once_as_a_batch_and_rejects_duplicate_metrics():
    async def run():
        async with database() as (sessions, booking, now, provider, owners, episodes):
            first, second, foreign = await babies(sessions, owners)
            observations = [{'kind': 'growth', 'recorded_on': AT.date(), 'timezone': 'UTC', 'metric': metric, 'value': value}
                for metric, value in [('weight', 4.2), ('length', 56), ('head_circumference', 37)]]
            body = BabyRecordBatchWrite(observations=observations)
            async def save():
                async with sessions.begin() as session:
                    values = await service(session, now).create_batch(owners[0], first, body, 'growth-form-once', 'save')
                    return [value.id for value in values]
            one, two = await asyncio.gather(save(), save())
            assert one == two and len(set(one)) == 3
            for invalid in [observations + [observations[0]], [observations[0], observations[0]], [observations[0], {**observations[1], 'recorded_on': '2026-09-02'}]]:
                with pytest.raises(ValidationError):
                    BabyRecordBatchWrite(observations=invalid)
            async with sessions.begin() as session:
                api = service(session, now)
                page = await api.list(owners[0], first, AT.date(), AT.date() + timedelta(days=1), timezone_name='UTC', kind='growth', offset=0, limit=100)
                assert page.total == 3
                assert {value.observation.metric for value in await api.latest_growth(owners[0], first)} == {'weight', 'length', 'head_circumference'}
                assert await api.latest_growth(owners[0], second) == []
                await api.set_deleted(owners[0], first, one[0], 1, True, 'delete')
                assert len(await api.latest_growth(owners[0], first)) == 2
                with pytest.raises(ApiError) as replay_deleted:
                    await api.create_batch(owners[0], first, body, 'growth-form-once', 'retry')
                assert replay_deleted.value.code == 'record_deleted'
    asyncio.run(run())


@postgres
def test_calendar_observations_keep_their_date_when_read_in_another_timezone():
    async def run():
        async with database() as (sessions, booking, now, provider, owners, episodes):
            first, second, foreign = await babies(sessions, owners)
            clock = AT.replace(hour=14, minute=30)
            local_day = clock.date() + timedelta(days=1)
            async with sessions.begin() as session:
                baby = await session.get(BabyProfile, first)
                baby.birth_date = clock.date()
                body = BabyRecordWrite.model_validate({'observation': {'kind': 'growth', 'recorded_on': local_day, 'timezone': 'Pacific/Kiritimati', 'metric': 'weight', 'value': 3.6}})
                saved = await service(session, clock).create(owners[0], first, body.observation, 'growth-date', 'create')
                assert saved.occurred_at is None and saved.recorded_on == local_day
                for zone in ['Pacific/Kiritimati', 'Pacific/Honolulu']:
                    page = await service(session, clock).list(owners[0], first, local_day, local_day + timedelta(days=1), timezone_name=zone, kind='growth', offset=0, limit=100)
                    assert page.total == 1 and page.items[0].observation.recorded_on == local_day
                    assert 'occurred_at' not in page.items[0].observation.model_dump()
                for day in [local_day + timedelta(days=1), baby.birth_date - timedelta(days=1)]:
                    invalid = BabyRecordWrite.model_validate({'observation': {'kind': 'development', 'recorded_on': day, 'timezone': 'Pacific/Kiritimati', 'item_id': 'looks-at-face', 'status': 'unsure'}})
                    with pytest.raises(ApiError) as rejected:
                        await service(session, clock).create(owners[0], first, invalid.observation, f'date-{day}', 'invalid-date')
                    assert rejected.value.code == 'validation_failed'
    asyncio.run(run())
