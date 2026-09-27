from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.baby.models import BabyRecord
from app.modules.baby.profile_models import BabyProfile
from app.modules.plans.models import PlanTask
from app.modules.profiles.agent_mutation import AgentBatchService, RecordBatch, ScheduleBatch
from app.modules.profiles.agent_router import get_agent_batch_service, get_agent_schedule_service
from app.modules.profiles.models import MaternalCurrentDeliveryInfant, MaternalProfile
from product_database import database, postgres

SERVICE_KEY = 'agent-runtime-service-key-with-32-bytes'


@postgres
def test_missing_pumping_side_returns_safe_issue_and_writes_nothing():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            batch = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'Asia/Shanghai', 'operations': [
                {'op': 'create', 'topic': 'pumping', 'fields': {
                    'occurred_at': '2026-09-23T10:00:00+08:00', 'volume_ml': 60,
                }},
            ]})
            with pytest.raises(ApiError) as error:
                async with sessions.begin() as session:
                    await AgentBatchService(session).records(batch, key=str(uuid4()), request_id='invalid-pump')
            assert error.value.code == 'validation_failed'
            assert error.value.details == {'operation_index': 0, 'field_path': 'fields.side', 'reason': 'required'}
    asyncio.run(run())


@postgres
def test_invalid_schedule_title_returns_field_issue_without_partial_write():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            batch = ScheduleBatch.model_validate({'actor_user_id': owner, 'operations': [
                {'op': 'create', 'fields': {'title': '  ', 'date': '2026-09-28', 'start_time': '09:00'}},
            ]})
            with pytest.raises(ApiError) as error:
                async with sessions.begin() as session:
                    await AgentBatchService(session).schedule(batch, key=str(uuid4()), request_id='invalid-title')
            assert error.value.code == 'validation_failed'
            assert error.value.details == {'operation_index': 0, 'field_path': 'fields.title', 'reason': 'invalid_value'}
            async with sessions.begin() as session:
                assert not (await session.scalars(select(PlanTask).where(PlanTask.owner_user_id == owner))).all()
    asyncio.run(run())


def _baby_create(baby):
    return {'op': 'create', 'topic': 'growth', 'infant_id': str(baby),
        'fields': {'recorded_on': '2026-09-23', 'metric': 'weight', 'value': 4.5}}


def test_batch_contract_does_not_accept_other_topics_or_ambiguous_updates():
    owner, baby = uuid4(), uuid4()
    base = {'actor_user_id': owner, 'timezone': 'UTC', 'operations': [_baby_create(baby)]}
    assert RecordBatch.model_validate(base).operations[0].topic == 'growth'
    for payload in (
        {**base, 'operations': [{**_baby_create(baby), 'topic': 'sleep'}]},
        {**base, 'operations': [{**_baby_create(baby), 'op': 'update'}]},
        {**base, 'operations': [{**_baby_create(baby), 'fields': {'recorded_on': '2026-09-23', 'note': 'private'}}]},
    ):
        with pytest.raises(ValidationError):
            RecordBatch.model_validate(payload)
    with pytest.raises(ValidationError):
        ScheduleBatch.model_validate({'actor_user_id': owner,
            'operations': [{'op': 'update', 'fields': {'title': 'Changed'}}]})


def test_batch_routes_require_service_identity_and_preserve_owner():
    actor = uuid4()
    class FakeService:
        owner = None
        async def schedule(self, body, *, key, request_id):
            self.owner = body.actor_user_id
            return {'batch_id': uuid4(), 'items': []}
    service = FakeService()
    app = create_app(Settings(app_env='test', agent_runtime_service_api_key=SERVICE_KEY))
    app.dependency_overrides[get_agent_batch_service] = lambda: service
    client = TestClient(app)
    url = '/v1/internal/agent/schedule/batch'
    body = {'actor_user_id': str(actor), 'operations': [{'op': 'create',
        'fields': {'title': 'Checkup', 'date': '2026-09-28', 'start_time': '10:00'}}]}
    assert client.post(url, json=body, headers={'Idempotency-Key': str(uuid4())}).status_code == 401
    assert client.post(url, json=body, headers={'Idempotency-Key': str(uuid4()), 'X-Service-Key': SERVICE_KEY}).status_code == 200
    assert service.owner == actor
    assert client.post(url, json={**body, 'operations': []}, headers={
        'Idempotency-Key': str(uuid4()), 'X-Service-Key': SERVICE_KEY}).status_code == 422


def test_schedule_read_is_personal_and_owner_scoped_at_service_boundary():
    actor = uuid4()
    class FakeService:
        args = None
        async def read(self, *args):
            self.args = args
            return {'personal': [], 'server_time': datetime.now(timezone.utc).isoformat(), 'has_more': False}
    service = FakeService()
    app = create_app(Settings(app_env='test', agent_runtime_service_api_key=SERVICE_KEY))
    app.dependency_overrides[get_agent_schedule_service] = lambda: service
    client = TestClient(app)
    params = {'actor_user_id': str(actor), 'start_date': '2026-09-26', 'end_date': '2026-09-29', 'timezone': 'UTC'}
    assert client.get('/v1/internal/agent/schedule', params=params).status_code == 401
    assert client.get('/v1/internal/agent/schedule', params=params, headers={'X-Service-Key': SERVICE_KEY}).status_code == 200
    assert service.args[0] == actor
    for rejected in ({**params, 'limit': '101'}, {**params, 'limit': '0'},
                     {**params, 'offset': '-1'}, {**params, 'offset': '10001'}):
        assert client.get('/v1/internal/agent/schedule', params=rejected,
            headers={'X-Service-Key': SERVICE_KEY}).status_code == 422


@postgres
def test_mixed_record_batch_is_atomic_idempotent_and_preserves_daily_status():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            baby_id = uuid4()
            async with sessions.begin() as session:
                session.add(BabyProfile(id=baby_id, owner_user_id=owner, name='Baby'))
                mother = MaternalProfile(owner_user_id=owner, latest_delivery_date=datetime(2026, 8, 1).date())
                session.add(mother)
                await session.flush()
                session.add(MaternalCurrentDeliveryInfant(maternal_profile_id=mother.id, infant_id=baby_id, birth_order=1))
                original = BabyRecord(owner_user_id=owner, baby_id=baby_id, kind='daily_status',
                    recorded_on=datetime(2026, 9, 23).date(), version=1,
                    data={'mental_state': 'content', 'wet_count': 3, 'timezone': 'UTC'})
                session.add(original)
                await session.flush()
                record_id = original.id
            updates = [
                {'op': 'update', 'topic': 'diaper', 'record_type': 'daily_summary', 'infant_id': str(baby_id),
                    'record_source': 'baby_records', 'record_id': str(record_id), 'revision': '1', 'fields': {'wet_count': 5}},
                {'op': 'update', 'topic': 'after_feeding_mood', 'infant_id': str(baby_id),
                    'record_source': 'baby_records', 'record_id': str(record_id), 'revision': '1', 'fields': {'mental_state': 'active'}},
                _baby_create(baby_id),
            ]
            payload = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': updates})
            key = str(uuid4())
            async with sessions.begin() as session:
                saved = await AgentBatchService(session).records(payload, key=key, request_id='initial')
            assert len(saved.items) == 3
            async with sessions.begin() as session:
                replay = await AgentBatchService(session).records(payload, key=key, request_id='retry')
                assert replay == saved
                status = await session.get(BabyRecord, record_id)
                assert status.version == 2 and status.data['wet_count'] == 5 and status.data['mental_state'] == 'active'
                assert len((await session.scalars(select(BabyRecord).where(BabyRecord.baby_id == baby_id))).all()) == 2
            invalid = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': [
                _baby_create(baby_id), {**updates[0], 'revision': '1'},
            ]})
            with pytest.raises(ApiError) as exc:
                async with sessions.begin() as session:
                    await AgentBatchService(session).records(invalid, key=str(uuid4()), request_id='conflict')
            assert exc.value.code == 'version_conflict'
            async with sessions.begin() as session:
                assert len((await session.scalars(select(BabyRecord).where(BabyRecord.baby_id == baby_id))).all()) == 2
    asyncio.run(run())


@postgres
def test_schedule_batch_rolls_back_if_any_update_has_stale_revision():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            async with sessions.begin() as session:
                event = PlanTask(owner_user_id=owner, task_date=datetime(2026, 9, 28).date(),
                    task_time='10:00', title='Checkup', description='', payload={'schedule_kind': 'personal'})
                session.add(event)
                await session.flush()
                event_id = event.id
            batch = ScheduleBatch.model_validate({'actor_user_id': owner, 'operations': [
                {'op': 'create', 'fields': {'title': 'Pump', 'date': '2026-09-29', 'start_time': '11:00'}},
                {'op': 'update', 'task_id': str(event_id), 'expected_updated_at': '2026-09-01T00:00:00Z',
                    'fields': {'title': 'Moved checkup'}},
            ]})
            with pytest.raises(ApiError) as exc:
                async with sessions.begin() as session:
                    await AgentBatchService(session).schedule(batch, key=str(uuid4()), request_id='batch')
            assert exc.value.code == 'version_conflict'
            async with sessions.begin() as session:
                assert len((await session.scalars(select(PlanTask).where(PlanTask.owner_user_id == owner))).all()) == 1
                assert (await session.get(PlanTask, event_id)).title == 'Checkup'
    asyncio.run(run())

@postgres
def test_all_app_record_topics_create_update_and_read_back():
    """Each writable topic remains readable under the same owner and target."""
    from app.modules.profiles.topical_records import TopicalRecordsQuery, TopicalRecordsService
    from app.modules.profiles.repository import ProfileRepository
    from app.modules.profiles.me_models import MotherObservation
    from app.modules.records.models import PumpingRecord

    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            baby = uuid4()
            async with sessions.begin() as session:
                session.add(BabyProfile(id=baby, owner_user_id=owner, name='Baby', birth_date=datetime(2026, 8, 1).date()))
                mother = MaternalProfile(owner_user_id=owner, latest_delivery_date=datetime(2026, 8, 1).date())
                session.add(mother)
                await session.flush()
                session.add(MaternalCurrentDeliveryInfant(maternal_profile_id=mother.id, infant_id=baby, birth_order=1))
            occurred = '2026-09-23T10:00:00Z'
            created = [
                {'op': 'create', 'topic': 'feeding', 'infant_id': str(baby),
                 'fields': {'occurred_at': occurred, 'method': 'breastfeeding', 'side': 'left', 'duration_minutes': 12}},
                {'op': 'create', 'topic': 'pumping',
                 'fields': {'occurred_at': occurred, 'side': 'Left side', 'volume_ml': 45}},
                {'op': 'create', 'topic': 'diaper', 'record_type': 'event', 'infant_id': str(baby),
                 'fields': {'occurred_at': occurred, 'diaper_kind': 'both', 'color': 'yellow'}},
                {'op': 'create', 'topic': 'diaper', 'record_type': 'daily_summary', 'infant_id': str(baby),
                 'fields': {'recorded_on': '2026-09-22', 'wet_count': 2}},
                {'op': 'create', 'topic': 'pain', 'fields': {'occurred_at': occurred, 'pain_score': 2,
                 'side': 'Left side', 'phase': 'When latching', 'impact': 'Could continue'}},
                {'op': 'create', 'topic': 'latch', 'fields': {'occurred_at': occurred, 'latch_status': 'Stayed latched'}},
                {'op': 'create', 'topic': 'growth', 'infant_id': str(baby),
                 'fields': {'recorded_on': '2026-09-23', 'metric': 'weight', 'value': 4.5}},
                {'op': 'create', 'topic': 'after_feeding_mood', 'infant_id': str(baby),
                 'fields': {'recorded_on': '2026-09-23', 'mental_state': 'content'}},
            ]
            async with sessions.begin() as session:
                response = await AgentBatchService(session).records(RecordBatch.model_validate({
                    'actor_user_id': owner, 'timezone': 'UTC', 'operations': created,
                }), key=str(uuid4()), request_id='all-topics')
            assert len(response.items) == len(created)
            expectations = (
                ('feeding', 'baby_records', {'duration_minutes': 15}),
                ('pumping', 'pumping_records', {'volume_ml': 50, 'side': 'Right side'}),
                ('diaper', 'baby_records', {'color': 'green'}),
                ('diaper', 'baby_records', {'wet_count': 3}),
                ('pain', 'mother_observations', {'pain_score': 3}),
                ('latch', 'mother_observations', {'latch_status': 'Came off easily'}),
                ('growth', 'baby_records', {'value': 4.6}),
                ('after_feeding_mood', 'baby_records', {'mental_state': 'active'}),
            )
            updates = [
                {**operation, 'op': 'update', 'record_source': source,
                 'record_id': str(receipt.resource_id), 'revision': receipt.revision, 'fields': fields}
                for operation, receipt, (_, source, fields) in zip(created, response.items, expectations, strict=True)
            ]
            async with sessions.begin() as session:
                result = await AgentBatchService(session).records(RecordBatch.model_validate({
                    'actor_user_id': owner, 'timezone': 'UTC', 'operations': updates,
                }), key=str(uuid4()), request_id='all-updates')
            assert [item.resource_id for item in result.items] == [item.resource_id for item in response.items]
            async with sessions.begin() as session:
                service = TopicalRecordsService(session, ProfileRepository(session))
                for receipt, (topic, _, changed) in zip(result.items, expectations, strict=True):
                    query = TopicalRecordsQuery(actor_user_id=owner, topic=topic,
                        infant_id=baby if topic in {'feeding', 'diaper', 'growth', 'after_feeding_mood'} else None,
                        start_date=datetime(2026, 9, 22).date(), end_date=datetime(2026, 9, 23).date(), timezone='UTC')
                    found = next(item for item in (await service.read(query)).items if item.record_id == receipt.resource_id)
                    for name, value in changed.items():
                        assert getattr(found, name) == value
                pump = await session.get(PumpingRecord, response.items[1].resource_id)
                mirror = await session.scalar(select(MotherObservation).where(
                    MotherObservation.owner_user_id == owner, MotherObservation.kind == 'pump',
                    MotherObservation.fields['canonical_record_id'].astext == str(pump.id)))
                assert pump.milk_volume_ml == 50 and mirror.value == '50.0 ml'
                assert mirror.fields['volume_ml'] == 50 and mirror.fields['side'] == 'Right side'
    asyncio.run(run())


@postgres
def test_batches_reject_cross_owner_and_stale_updates_without_partial_writes():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner, stranger = owners
            baby = uuid4()
            async with sessions.begin() as session:
                session.add(BabyProfile(id=baby, owner_user_id=owner, name='Baby'))
                mother = MaternalProfile(owner_user_id=owner, latest_delivery_date=datetime(2026, 8, 1).date())
                session.add(mother)
                await session.flush()
                session.add(MaternalCurrentDeliveryInfant(maternal_profile_id=mother.id, infant_id=baby, birth_order=1))
            initial = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': [_baby_create(baby)]})
            async with sessions.begin() as session:
                receipt = await AgentBatchService(session).records(initial, key=str(uuid4()), request_id='initial')
            update = {'op': 'update', 'topic': 'growth', 'infant_id': str(baby), 'record_source': 'baby_records',
                'record_id': str(receipt.items[0].resource_id), 'revision': '1', 'fields': {'value': 4.6}}
            async with sessions.begin() as session:
                await AgentBatchService(session).records(RecordBatch.model_validate({
                    'actor_user_id': owner, 'timezone': 'UTC', 'operations': [update],
                }), key=str(uuid4()), request_id='update')
            for actor, operation, expected in (
                (stranger, _baby_create(baby), 'not_found'),
                (stranger, update, 'not_found'),
                (owner, update, 'version_conflict'),
            ):
                with pytest.raises(ApiError) as error:
                    async with sessions.begin() as session:
                        await AgentBatchService(session).records(RecordBatch.model_validate({
                            'actor_user_id': actor, 'timezone': 'UTC',
                            'operations': [_baby_create(baby), operation],
                        }), key=str(uuid4()), request_id='failed-batch')
                assert error.value.code == expected
            async with sessions.begin() as session:
                entries = (await session.scalars(select(BabyRecord).where(BabyRecord.owner_user_id == owner))).all()
                assert len(entries) == 1 and entries[0].data['value'] == 4.6
    asyncio.run(run())

@postgres
def test_schedule_batch_updates_multiple_dates_replays_and_checks_personal_owner_scope():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner, stranger = owners
            async with sessions.begin() as session:
                own = PlanTask(owner_user_id=owner, task_date=datetime(2026, 9, 28).date(),
                    task_time='10:00', title='Own', description='', payload={'schedule_kind': 'personal'})
                foreign = PlanTask(owner_user_id=stranger, task_date=datetime(2026, 9, 28).date(),
                    task_time='12:00', title='Other', description='', payload={'schedule_kind': 'personal'})
                nonpersonal = PlanTask(owner_user_id=owner, task_date=datetime(2026, 9, 29).date(),
                    task_time='13:00', title='Not personal', description='', payload={})
                session.add_all([own, foreign, nonpersonal])
                await session.flush()
                own_id, foreign_id, nonpersonal_id, revision = own.id, foreign.id, nonpersonal.id, own.updated_at.isoformat()
            operations = [
                {'op': 'create', 'fields': {'title': 'Checkup', 'date': '2026-10-01', 'start_time': '09:30'}},
                {'op': 'update', 'task_id': str(own_id), 'expected_updated_at': revision,
                    'fields': {'date': '2026-09-30', 'start_time': '10:30'}},
                {'op': 'update', 'task_id': str(own_id), 'expected_updated_at': revision,
                    'fields': {'title': 'Updated own'}},
                {'op': 'create', 'fields': {'title': 'Another day', 'date': '2026-10-02', 'start_time': '18:00'}},
            ]
            command = ScheduleBatch.model_validate({'actor_user_id': owner, 'operations': operations})
            key = str(uuid4())
            async with sessions.begin() as session:
                saved = await AgentBatchService(session).schedule(command, key=key, request_id='schedule-batch')
            assert len(saved.items) == 4
            assert saved.items[1].resource_id == saved.items[2].resource_id == own_id
            async with sessions.begin() as session:
                assert await AgentBatchService(session).schedule(command, key=key, request_id='retry') == saved
                tasks = (await session.scalars(select(PlanTask).where(PlanTask.owner_user_id == owner))).all()
                assert len(tasks) == 4
                changed = await session.get(PlanTask, own_id)
                assert changed.title == 'Updated own' and changed.task_time == '10:30'
                assert changed.task_date == datetime(2026, 9, 30).date()
            for target, expected in ((foreign_id, 'not_found'), (nonpersonal_id, 'not_found'), (own_id, 'version_conflict')):
                bad = ScheduleBatch.model_validate({'actor_user_id': owner, 'operations': [
                    {'op': 'create', 'fields': {'title': 'Should roll back', 'date': '2026-10-04', 'start_time': '15:00'}},
                    {'op': 'update', 'task_id': str(target), 'expected_updated_at': revision,
                        'fields': {'title': 'Should not change'}},
                ]})
                with pytest.raises(ApiError) as error:
                    async with sessions.begin() as session:
                        await AgentBatchService(session).schedule(bad, key=str(uuid4()), request_id='bad')
                assert error.value.code == expected
            async with sessions.begin() as session:
                tasks = (await session.scalars(select(PlanTask).where(PlanTask.owner_user_id == owner))).all()
                assert len(tasks) == 4
                assert (await session.get(PlanTask, nonpersonal_id)).title == 'Not personal'
                assert (await session.get(PlanTask, foreign_id)).title == 'Other'
    asyncio.run(run())


@postgres
def test_record_batch_supports_multiple_updates_of_one_legacy_entry_and_rolls_back_on_invalid_field():
    from app.modules.records.models import FeedingRecord, GrowthRecord
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            baby = uuid4()
            async with sessions.begin() as session:
                session.add(BabyProfile(id=baby, owner_user_id=owner, name='Baby'))
                mother = MaternalProfile(owner_user_id=owner, latest_delivery_date=datetime(2026, 8, 1).date())
                session.add(mother)
                await session.flush()
                session.add(MaternalCurrentDeliveryInfant(maternal_profile_id=mother.id, infant_id=baby, birth_order=1))
                feeding = FeedingRecord(owner_user_id=owner, infant_id=baby,
                    feed_time=datetime(2026, 9, 23, 10, tzinfo=timezone.utc),
                    feed_type='formula', volume_ml=50)
                growth = GrowthRecord(owner_user_id=owner, infant_id=baby,
                    measured_at=datetime(2026, 9, 23, 10, tzinfo=timezone.utc), weight_kg=4.5)
                session.add_all([feeding, growth])
                await session.flush()
                feeding_id, feeding_revision = feeding.id, feeding.updated_at.isoformat()
                growth_id, growth_revision = growth.id, growth.updated_at.isoformat()
            updates = [
                {'op': 'update', 'topic': 'feeding', 'infant_id': str(baby), 'record_source': 'feeding_records',
                    'record_id': str(feeding_id), 'revision': feeding_revision, 'fields': {'volume_ml': 65}},
                {'op': 'update', 'topic': 'feeding', 'infant_id': str(baby), 'record_source': 'feeding_records',
                    'record_id': str(feeding_id), 'revision': feeding_revision, 'fields': {'method': 'expressed_milk'}},
                {'op': 'update', 'topic': 'growth', 'infant_id': str(baby), 'record_source': 'growth_records',
                    'record_id': str(growth_id), 'revision': growth_revision, 'fields': {'weight_kg': 4.6}},
            ]
            command = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': updates})
            async with sessions.begin() as session:
                result = await AgentBatchService(session).records(command, key=str(uuid4()), request_id='legacy')
            assert result.items[0].resource_id == result.items[1].resource_id == feeding_id
            async with sessions.begin() as session:
                entry = await session.get(FeedingRecord, feeding_id)
                measurement = await session.get(GrowthRecord, growth_id)
                assert entry.volume_ml == 65 and entry.feed_type == 'expressed_milk'
                assert measurement.weight_kg == 4.6
            bad = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': [
                _baby_create(baby), {**updates[2], 'revision': result.items[2].revision, 'fields': {'weight_kg': None}},
            ]})
            with pytest.raises(ApiError) as error:
                async with sessions.begin() as session:
                    await AgentBatchService(session).records(bad, key=str(uuid4()), request_id='invalid')
            assert error.value.code == 'validation_failed'
            async with sessions.begin() as session:
                assert (await session.get(GrowthRecord, growth_id)).weight_kg == 4.6
                assert not (await session.scalars(select(BabyRecord).where(BabyRecord.owner_user_id == owner))).all()
    asyncio.run(run())


@postgres
def test_record_batch_rejects_old_delivery_baby_and_mismatched_source_or_revision():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner, stranger = owners
            current, older = uuid4(), uuid4()
            async with sessions.begin() as session:
                session.add_all([
                    BabyProfile(id=current, owner_user_id=owner, name='Current'),
                    BabyProfile(id=older, owner_user_id=owner, name='Older'),
                ])
                mother = MaternalProfile(owner_user_id=owner, latest_delivery_date=datetime(2026, 8, 1).date())
                session.add(mother)
                await session.flush()
                session.add(MaternalCurrentDeliveryInfant(maternal_profile_id=mother.id, infant_id=current, birth_order=1))
            command = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC',
                'operations': [_baby_create(current)]})
            key = str(uuid4())
            async with sessions.begin() as session:
                original = await AgentBatchService(session).records(command, key=key, request_id='first')
            assert original.items[0].revision == '1'
            for actor, operations, replay_key, expected in (
                (owner, [_baby_create(older)], str(uuid4()), 'not_found'),
                (stranger, [_baby_create(current)], str(uuid4()), 'not_found'),
                (owner, [{**_baby_create(current), 'fields': {**_baby_create(current)['fields'], 'value': 4.8}}], key, 'idempotency_conflict'),
                (stranger, [_baby_create(current)], key, 'idempotency_conflict'),
            ):
                with pytest.raises(ApiError) as error:
                    async with sessions.begin() as session:
                        await AgentBatchService(session).records(RecordBatch.model_validate({
                            'actor_user_id': actor, 'timezone': 'UTC', 'operations': operations,
                        }), key=replay_key, request_id='rejected')
                assert error.value.code == expected
            mismatched = {'op': 'update', 'topic': 'growth', 'infant_id': str(current),
                'record_source': 'baby_records', 'record_id': str(original.items[0].resource_id),
                'revision': '1', 'fields': {'value': 4.6}}
            with pytest.raises(ValidationError):
                RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': [
                    {**mismatched, 'record_source': 'pumping_records'},
                ]})
            with pytest.raises(ApiError) as error:
                async with sessions.begin() as session:
                    await AgentBatchService(session).records(RecordBatch.model_validate({
                        'actor_user_id': owner, 'timezone': 'UTC', 'operations': [
                            {**mismatched, 'topic': 'feeding', 'fields': {'duration_minutes': 5}},
                        ],
                    }), key=str(uuid4()), request_id='wrong-kind')
            assert error.value.code == 'not_found'
            async with sessions.begin() as session:
                entries = (await session.scalars(select(BabyRecord).where(BabyRecord.owner_user_id == owner))).all()
                assert len(entries) == 1 and entries[0].data['value'] == 4.5
    asyncio.run(run())

@postgres
def test_idempotency_rejects_replay_that_changes_omitted_field_to_explicit_null():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            baby = uuid4()
            async with sessions.begin() as session:
                session.add(BabyProfile(id=baby, owner_user_id=owner, name='Baby'))
                mother = MaternalProfile(owner_user_id=owner, latest_delivery_date=datetime(2026, 8, 1).date())
                session.add(mother)
                await session.flush()
                session.add(MaternalCurrentDeliveryInfant(maternal_profile_id=mother.id, infant_id=baby, birth_order=1))
                original = BabyRecord(owner_user_id=owner, baby_id=baby, kind='daily_status',
                    recorded_on=datetime(2026, 9, 23).date(), version=1,
                    data={'stool_count': 1, 'color': 'green', 'timezone': 'UTC'})
                session.add(original)
                await session.flush()
                record_id = original.id
            operation = {'op': 'update', 'topic': 'diaper', 'record_type': 'daily_summary',
                'infant_id': str(baby), 'record_source': 'baby_records',
                'record_id': str(record_id), 'revision': '1', 'fields': {'wet_count': 5}}
            command = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC',
                'operations': [operation]})
            changed = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC',
                'operations': [{**operation, 'fields': {'wet_count': 5, 'color': None}}]})
            key = str(uuid4())
            async with sessions.begin() as session:
                await AgentBatchService(session).records(command, key=key, request_id='first')
            with pytest.raises(ApiError) as exc:
                async with sessions.begin() as session:
                    await AgentBatchService(session).records(changed, key=key, request_id='changed')
            assert exc.value.code == 'idempotency_conflict'
            async with sessions.begin() as session:
                stored = await session.get(BabyRecord, record_id)
                assert stored.version == 2 and stored.data['color'] == 'green'
    asyncio.run(run())


@postgres
def test_pumping_update_rejects_unmeasured_mirror_and_rolls_back_previous_item():
    from app.modules.records.models import PumpingRecord
    from app.modules.profiles.me_models import MotherObservation

    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            create = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': [{
                'op': 'create', 'topic': 'pumping', 'fields': {
                    'occurred_at': '2026-09-23T10:00:00Z', 'side': 'Left side', 'volume_ml': 45, 'duration_minutes': 10,
                },
            }]})
            async with sessions.begin() as session:
                result = await AgentBatchService(session).records(create, key=str(uuid4()), request_id='pump-create')
            pump_id, revision = result.items[0].resource_id, result.items[0].revision
            invalid = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': [
                {'op': 'create', 'topic': 'pumping', 'fields': {
                    'occurred_at': '2026-09-23T12:00:00Z', 'side': 'Right side', 'volume_ml': 20,
                }},
                {'op': 'update', 'topic': 'pumping', 'record_source': 'pumping_records',
                    'record_id': str(pump_id), 'revision': revision, 'fields': {'volume_ml': None}},
            ]})
            with pytest.raises(ApiError) as error:
                async with sessions.begin() as session:
                    await AgentBatchService(session).records(invalid, key=str(uuid4()), request_id='pump-update')
            assert error.value.code == 'validation_failed'
            async with sessions.begin() as session:
                pumps = (await session.scalars(select(PumpingRecord).where(PumpingRecord.owner_user_id == owner))).all()
                assert len(pumps) == 1 and pumps[0].milk_volume_ml == 45
                mirror = await session.scalar(select(MotherObservation).where(
                    MotherObservation.owner_user_id == owner, MotherObservation.kind == 'pump',
                    MotherObservation.fields['canonical_record_id'].astext == str(pump_id)))
                assert mirror.fields['volume_ml'] == 45 and mirror.fields['side'] == 'Left side'
    asyncio.run(run())


@postgres
def test_legacy_pumping_side_update_creates_valid_me_mirror_and_replays_once():
    from app.modules.profiles.me_models import MotherObservation
    from app.modules.profiles.topical_records import TopicalRecordsQuery, TopicalRecordsService
    from app.modules.profiles.repository import ProfileRepository
    from app.modules.records.models import PumpingRecord

    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            owner = owners[0]
            async with sessions.begin() as session:
                pump = PumpingRecord(owner_user_id=owner, pump_start_time=datetime(2026, 9, 23, 10, tzinfo=timezone.utc),
                    milk_volume_ml=35, source='manual', pump_type='manual')
                session.add(pump)
                await session.flush()
                pump_id, revision = pump.id, pump.updated_at.isoformat()
            operations = [
                {'op': 'update', 'topic': 'pumping', 'record_source': 'pumping_records',
                    'record_id': str(pump_id), 'revision': revision, 'fields': {'side': 'Both sides'}},
                {'op': 'update', 'topic': 'pumping', 'record_source': 'pumping_records',
                    'record_id': str(pump_id), 'revision': revision, 'fields': {'volume_ml': 40}},
            ]
            batch = RecordBatch.model_validate({'actor_user_id': owner, 'timezone': 'UTC', 'operations': operations})
            key = str(uuid4())
            async with sessions.begin() as session:
                saved = await AgentBatchService(session).records(batch, key=key, request_id='legacy-pump')
            assert len(saved.items) == 2 and all(item.resource_id == pump_id for item in saved.items)
            async with sessions.begin() as session:
                assert await AgentBatchService(session).records(batch, key=key, request_id='retry') == saved
                mirrors = (await session.scalars(select(MotherObservation).where(
                    MotherObservation.owner_user_id == owner, MotherObservation.kind == 'pump',
                    MotherObservation.fields['canonical_record_id'].astext == str(pump_id)))).all()
                assert len(mirrors) == 1 and mirrors[0].fields['volume_ml'] == 40
                assert mirrors[0].fields['side'] == 'Both sides'
                observed = await TopicalRecordsService(session, ProfileRepository(session)).read(TopicalRecordsQuery(
                    actor_user_id=owner, topic='pumping', start_date=datetime(2026, 9, 23).date(),
                    end_date=datetime(2026, 9, 23).date(), timezone='UTC'))
                assert len(observed.items) == 1 and observed.items[0].side == 'Both sides'
                assert observed.items[0].volume_ml == 40
    asyncio.run(run())
