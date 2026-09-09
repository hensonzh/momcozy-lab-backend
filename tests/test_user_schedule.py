import asyncio
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import ApiError
from app.infrastructure.db.base import Base
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService, IdempotencyService
from app.modules.plans.models import Plan, PlanTask
from app.modules.schedule.repository import ScheduleRepository
from app.modules.schedule.schemas import PersonalScheduleWrite, PersonalScheduleUpdate
from app.modules.schedule.service import ScheduleService
from test_care_booking import database, postgres
from test_care_documentation import documented_case, NOTE, PLAN
from app.modules.appointments.schemas import VersionWrite
from app.modules.documentation.schemas import NoteWrite, NoteVersionWrite, PlanWrite, TaskProgressWrite


async def task_tables(session):
    connection = await session.connection()
    await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Plan.__table__, PlanTask.__table__]))


def schedule(session, documentation=None):
    audit = AuditRepository(session)
    audit_service = AuditService(repository=audit)
    return ScheduleService(ScheduleRepository(session), audit=audit_service, idempotency=IdempotencyService(repository=audit), documentation=documentation)


@pytest.mark.parametrize('change', [{'start_time': '25:00'}, {'start_time': '9:00'}, {'title': ' '}, {'owner_user_id': 'other'}, {'note': 'x' * 121}])
def test_schedule_write_is_a_strict_calendar_contract(change):
    with pytest.raises(ValidationError):
        PersonalScheduleWrite.model_validate({'title': 'Baby checkup', 'date': '2026-09-09', 'start_time': '09:00', **change})


@postgres
def test_personal_schedule_uses_shared_tasks_and_preserves_conflicts_isolation_and_retry():
    async def run():
        async with database() as (sessions, _, now, _, owners, _):
            body = PersonalScheduleWrite(title='Baby checkup', date=now.date(), start_time='09:30', note='Bring records')
            async with sessions.begin() as session:
                await task_tables(session)
                saved = await schedule(session).create(owners[0], body, 'one-checkup', 'create')
            async with sessions.begin() as session:
                api = schedule(session)
                assert (await api.create(owners[0], body, 'one-checkup', 'retry')).id == saved.id
                rows = list(await session.scalars(select(PlanTask)))
                assert len(rows) == 1 and rows[0].task_date == body.date
                changed = PersonalScheduleUpdate(**body.model_dump(), expected_updated_at=saved.updated_at)
                changed.title = 'Changed checkup'
                updated = await api.update(owners[0], saved.id, changed, 'edit')
                assert (await api.update(owners[0], saved.id, changed, 'retry-edit')).updated_at == updated.updated_at
                with pytest.raises(ApiError) as conflict:
                    await api.update(owners[0], saved.id, changed.model_copy(update={'title': 'Stale edit'}), 'stale')
                assert conflict.value.code == 'version_conflict'
                with pytest.raises(ApiError) as foreign:
                    await api.update(owners[1], saved.id, changed, 'foreign')
                assert foreign.value.status == 404
                page = await api.read(owners[0], now.date(), now.date() + timedelta(days=1), 'UTC', 0, 20)
                assert page.personal[0].title == 'Changed checkup'
                assert not (await api.read(owners[1], now.date(), now.date() + timedelta(days=1), 'UTC', 0, 20)).personal
                await api.delete(owners[0], saved.id, updated.updated_at, 'delete')
                await api.delete(owners[0], saved.id, updated.updated_at, 'retry-delete')
                assert not (await api.read(owners[0], now.date(), now.date() + timedelta(days=1), 'UTC', 0, 20)).personal
    asyncio.run(run())


@postgres
def test_schedule_includes_only_latest_published_tasks_and_never_private_notes():
    async def run():
        async with documented_case() as (sessions, documentation, at, appointment, mom, expert, wrong):
            async with sessions.begin() as session:
                await task_tables(session)
                docs = documentation(session)
                await docs.save_note(expert, appointment.id, NoteWrite(expected_revision=0, expected_version=0, content=NOTE), 'note', 'note')
                await docs.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=1), 'sign', 'sign')
                await docs.save_plan(expert, appointment.id, PlanWrite(expected_version=0, content=PLAN), 'plan', 'plan')
                first = await docs.publish(expert, appointment.id, VersionWrite(expected_version=1), 'publish', 'publish')
                await docs.update_task(mom.user_id, first.id, 'log-observation', TaskProgressWrite(expected_version=1, status='completed'), 'feedback')
                draft = await docs.save_plan(expert, appointment.id, PlanWrite(expected_version=2, content=PLAN.model_copy(update={'summary': 'Revised advice'})), 'plan-2', 'plan-2')
                latest = await docs.publish(expert, appointment.id, VersionWrite(expected_version=draft.version), 'publish-2', 'publish-2')
                api = schedule(session, docs)
                page = await api.read(mom.user_id, at[0].date() - timedelta(days=1), at[0].date() + timedelta(days=2), 'UTC', 0, 20)
                assert [value.publication.id for value in page.plans] == [latest.id]
                assert page.plans[0].publication.tasks[0].status == 'completed'
                assert page.plans[0].episode_id == appointment.episode_id
                assert page.appointments[0].id == appointment.id
                assert 'Private report' not in page.model_dump_json()
                assert not (await api.read(wrong.user_id, at[0].date(), at[0].date() + timedelta(days=1), 'UTC', 0, 20)).plans
    asyncio.run(run())
