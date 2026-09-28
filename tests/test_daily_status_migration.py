"""Exercise the duplicate-cleanup migration against an isolated PostgreSQL schema."""

import asyncio
import importlib
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.modules.audit.models import AuditLog, IdempotencyKey
from app.modules.baby.models import BabyRecord
from app.modules.baby.profile_models import BabyProfile
from app.modules.profiles.agent_mutation import AgentBatchReceipt
from product_database import database, postgres


@postgres
def test_duplicate_daily_status_migration_deletes_old_rows_without_losing_current_fields():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            engine = sessions.kw['bind']
            # create_all creates the target index; reproduce the preceding revision.
            async with engine.begin() as connection:
                await connection.execute(text('DROP INDEX uq_baby_records_active_daily_status'))
            day = date(2026, 9, 27)
            start = datetime(2026, 9, 27, 8, tzinfo=timezone.utc)
            baby, other = uuid4(), uuid4()
            oldest, middle, newest, deleted, other_day, other_baby, event = (uuid4() for _ in range(7))
            batch_id = uuid4()
            def daily(record_id, baby_id, on, minutes, data, *, deleted_at=None):
                return BabyRecord(id=record_id, owner_user_id=owners[0], baby_id=baby_id,
                    kind='daily_status', recorded_on=on, data=data,
                    version=1, created_at=start + timedelta(minutes=minutes),
                    updated_at=start + timedelta(minutes=minutes), deleted_at=deleted_at)
            async with sessions.begin() as session:
                session.add_all([
                    BabyProfile(id=baby, owner_user_id=owners[0], name='Baby A'),
                    BabyProfile(id=other, owner_user_id=owners[0], name='Baby B'),
                ])
                await session.flush()
                session.add_all([
                    daily(oldest, baby, day, 1, {'timezone': 'UTC', 'wet_count': 2, 'stool_count': 1,
                        'color': 'yellow', 'consistency': 'pasty'}),
                    daily(middle, baby, day, 2, {'timezone': 'UTC', 'stool_count': 3, 'mental_state': 'content',
                        'color': 'green'}),
                    daily(newest, baby, day, 3, {'timezone': 'UTC', 'wet_count': 4, 'stool_count': None}),
                    daily(deleted, baby, day, 4, {'timezone': 'UTC', 'wet_count': 99}, deleted_at=start),
                    daily(other_day, baby, day - timedelta(days=1), 1, {'timezone': 'UTC', 'wet_count': 7}),
                    daily(other_baby, other, day, 1, {'timezone': 'UTC', 'wet_count': 5}),
                    BabyRecord(id=event, owner_user_id=owners[0], baby_id=baby, kind='diaper',
                        occurred_at=start, data={'diaper_kind': 'wet'}),
                    IdempotencyKey(actor_user_id=owners[0], scope=f'baby_record.create:{baby}',
                        key='old-summary', request_hash='hash', response_ref=str(oldest),
                        status='completed', expires_at=start + timedelta(days=30)),
                    AuditLog(actor_user_id=owners[0], action='baby_record.create',
                        resource_type='baby_record', resource_id=str(oldest), request_id='previous-save'),
                    AgentBatchReceipt(id=batch_id, owner_user_id=owners[0], request_hash='hash',
                        result={'batch_id': str(batch_id), 'items': [
                            {'op': 'create', 'resource_id': str(middle), 'revision': '1'},
                            {'op': 'create', 'resource_id': str(other_baby), 'revision': '1'}]}),
                ])
            revision = importlib.import_module('migrations.versions.20260928_0025_daily_status_current')
            def upgrade(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    revision.upgrade()
            async with engine.begin() as connection:
                await connection.run_sync(upgrade)
            async with sessions.begin() as session:
                rows = (await session.scalars(select(BabyRecord).where(BabyRecord.owner_user_id == owners[0]))).all()
                assert {row.id for row in rows} == {newest, deleted, other_day, other_baby, event}
                current = next(row for row in rows if row.id == newest)
                assert current.data['wet_count'] == 4
                assert current.data['stool_count'] == 3
                assert current.data['color'] == 'green'
                assert current.data['consistency'] == 'pasty'
                assert current.data['mental_state'] == 'content'
                assert current.version == 2
                key = await session.scalar(select(IdempotencyKey).where(IdempotencyKey.key == 'old-summary'))
                assert key.response_ref == str(newest)
                receipt = await session.get(AgentBatchReceipt, batch_id)
                assert receipt.result['items'][0]['resource_id'] == str(newest)
                assert receipt.result['items'][0]['revision'] == '2'
                assert receipt.result['items'][1] == {'op': 'create', 'resource_id': str(other_baby), 'revision': '1'}
                audit = await session.scalar(select(AuditLog).where(AuditLog.request_id == 'previous-save'))
                assert audit.resource_id == str(oldest)
            with pytest.raises(IntegrityError):
                async with sessions.begin() as session:
                    session.add(daily(uuid4(), baby, day, 5, {'timezone': 'UTC', 'wet_count': 6}))
    asyncio.run(run())
