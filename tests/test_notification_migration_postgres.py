import asyncio
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.care.event_models import CareServiceEvent
from app.modules.care.models import CareEligibility, CareEpisode, CareOrder
from app.modules.users.models import User
from tests.test_care_booking import DATABASE_URL, postgres

pytestmark = postgres
ROOT = Path(__file__).resolve().parents[1]


def test_notification_migration_preserves_inbox_seeds_event_receipts_and_matches_models():
    async def run():
        source = make_url(DATABASE_URL)
        # Always migrate a newly created, dedicated database, never the supplied database.
        database = f'notification_migration_{uuid4().hex}'
        admin = await asyncpg.connect(source.set(drivername='postgresql').render_as_string(hide_password=False))
        await admin.execute(f'CREATE DATABASE "{database}"')
        url = source.set(database=database).render_as_string(hide_password=False)
        env = {**os.environ, 'DATABASE_URL': url, 'APP_ENV': 'test'}
        async def migrate(*args):
            result = await asyncio.to_thread(subprocess.run, [sys.executable, '-m', 'alembic', *args], cwd=ROOT,
                env=env, capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine)
        try:
            await migrate('upgrade', '20260910_0017')
            now = datetime.now(timezone.utc)
            owner, inbox_id, event_id = uuid4(), uuid4(), uuid4()
            async with sessions.begin() as session:
                session.add(User(id=owner))
                await session.flush()
                eligible = CareEligibility(owner_user_id=owner, package_id='local-test', region='CA', eligible=True, expires_at=now + timedelta(days=1))
                session.add(eligible)
                await session.flush()
                order = CareOrder(owner_user_id=owner, eligibility_id=eligible.id, package_id='local-test', price_minor=0,
                    duration_days=30, total_sessions=1, currency='USD', status='paid', region='CA')
                session.add(order)
                await session.flush()
                episode = CareEpisode(owner_user_id=owner, order_id=order.id, package_id='local-test', total_sessions=1, remaining_sessions=1)
                session.add(episode)
                await session.flush()
                session.add(CareServiceEvent(id=event_id, episode_id=episode.id, kind='service_progress_changed', aggregate_id=episode.id,
                    aggregate_version=1, occurred_at=now))
                await session.execute(text("INSERT INTO notifications (id, owner_user_id, notification_type, title, body, status, source, payload_json) VALUES (:id, :owner, 'system', 'Existing update', 'Existing body', 'read', 'system', '{}')"), {'id': inbox_id, 'owner': owner})
            await migrate('upgrade', 'head')
            async with sessions() as session:
                row = (await session.execute(text('SELECT title, status, send_status FROM notifications WHERE id=:id'), {'id': inbox_id})).one()
                assert tuple(row) == ('Existing update', 'read', 'in_app')
                assert await session.scalar(text('SELECT count(*) FROM notification_event_receipts WHERE event_id=:id'), {'id': event_id}) == 1
            await migrate('check')
            await migrate('downgrade', '20260910_0017')
            await migrate('upgrade', 'head')
        finally:
            await engine.dispose()
            await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
            await admin.close()
    asyncio.run(run())
