from test_baby_records import babies, service
from test_care_booking import database, postgres
from app.modules.reports.baby_source_text import baby_record_text
import asyncio
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.baby.models import BabyRecord
from app.modules.baby.schemas import BabyRecordRead, BabyRecordWrite

BASE = {'kind': 'daily_status', 'recorded_on': '2026-09-20', 'timezone': 'Asia/Shanghai'}

@pytest.mark.parametrize('fields', [
    {'mental_state': 'content'}, {'wet_count': 2},
    {'mental_state': 'active', 'wet_count': 5, 'stool_count': 2, 'color': 'yellow', 'consistency': 'loose'},
])
def test_daily_form_round_trips_as_one_atomic_observation(fields):
    value = BabyRecordWrite.model_validate({'observation': {**BASE, **fields}}).observation
    record = BabyRecord(id=uuid4(), baby_id=uuid4(), owner_user_id=uuid4(), version=1,
        kind=value.kind, recorded_on=value.recorded_on, occurred_at=None,
        data=value.model_dump(mode='json', exclude={'kind', 'recorded_on'}),
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    output = BabyRecordRead.model_validate(record).model_dump(mode='json')['observation']
    assert 'occurred_at' not in output
    assert all(output[key] == expected for key, expected in fields.items())

@pytest.mark.parametrize('fields', [{}, {'wet_count': 0}, {'wet_count': 1.5}, {'wet_count': True}, {'stool_count': -1}, {'wet_count': 101}, {'mental_state': 'other'}, {'color': 'yellow'}, {'consistency': 'loose'}, {'wet_count': 2, 'occurred_at': '2026-09-20T00:00:00Z'}])
def test_daily_form_rejects_empty_partial_and_invalid_observations(fields):
    with pytest.raises(ValidationError):
        BabyRecordWrite.model_validate({'observation': {**BASE, **fields}})




@postgres
def test_daily_status_commits_all_tabs_and_replays_once_in_the_correct_day():
    async def run():
        async with database() as (sessions, booking, now, provider, owners, episodes):
            first, second, foreign = await babies(sessions, owners)
            fields = {**BASE, 'mental_state': 'content', 'wet_count': 5, 'stool_count': 2,
                      'color': 'yellow', 'consistency': 'loose'}
            body = BabyRecordWrite.model_validate({'observation': fields})
            async with sessions.begin() as session:
                saved = await service(session, now).create(owners[0], first, body.observation, 'daily-once', 'create')
                record_id = saved.id
            async with sessions.begin() as session:
                api = service(session, now)
                replay = await api.create(owners[0], first, body.observation, 'daily-once', 'retry')
                assert replay.id == record_id
                assert replay.observation == body.observation
                assert replay.occurred_at is None
                text = baby_record_text(replay, 'Asia/Shanghai')
                assert '平静满足' in text and '今日湿尿布数：5' in text and '不累加' in text
                day = body.observation.recorded_on
                result = await api.list(owners[0], first, day, day + timedelta(days=1), timezone_name='Asia/Shanghai', kind='daily_status', offset=0, limit=100)
                assert result.total == 1
                assert result.items[0].observation.stool_count == 2
                other_day = await api.list(owners[0], first, day + timedelta(days=1), day + timedelta(days=2), timezone_name='Asia/Shanghai', kind=None, offset=0, limit=100)
                assert other_day.total == 0
                other_baby = await api.list(owners[0], second, day, day + timedelta(days=1), timezone_name='Asia/Shanghai', kind=None, offset=0, limit=100)
                assert other_baby.total == 0
                assert len(list(await session.scalars(select(BabyRecord)))) == 1
    asyncio.run(run())
