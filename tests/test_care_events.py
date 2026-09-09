import asyncio

import pytest
from sqlalchemy import func, select

from app.core.errors import ApiError
from app.core.settings import Settings
from app.modules.appointments.schemas import VersionWrite
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.care.models import CareEpisode
from app.modules.care.models import CareProvider
from app.modules.consultations.models import CareConsentRevision
from app.modules.profiles.models import UserProfile
from app.modules.care.event_models import CareServiceEvent, CareServiceEventRead
from app.modules.ibclc.repository import WorkbenchRepository
from app.modules.ibclc.service import WorkbenchService
from test_care_booking import postgres
from test_care_rooms import room_case


@postgres
def test_booking_events_and_read_receipts_are_durable_scoped_and_idempotent():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            def service(session):
                return WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0])
            async with sessions.begin() as session:
                result = await service(session).reminders(expert, offset=0, limit=20, request_id='list')
                assert result.total == result.unread_count == 1
                event = result.items[0]
                assert event.kind == 'appointment_confirmed' and event.appointment_id == appointment.id
                assert event.patient_ref == mom.user_id and event.read_at is None
                await service(session).read_reminder(expert, event.id, 'read')
                await service(session).read_reminder(expert, event.id, 'repeat-read')
                assert await session.scalar(select(func.count()).select_from(CareServiceEventRead)) == 1
                with pytest.raises(ApiError) as denied:
                    await service(session).reminders(mom, offset=0, limit=20, request_id='mom')
                assert denied.value.status == 403
            async with sessions.begin() as session:
                assert (await service(session).reminders(expert, offset=0, limit=20, request_id='reload')).unread_count == 0
                cancelled = await booking(session).cancel(mom.user_id, appointment.id, VersionWrite(expected_version=appointment.version), 'cancel')
                await booking(session).cancel(mom.user_id, appointment.id, VersionWrite(expected_version=appointment.version), 'retry')
                assert cancelled.status == 'cancelled'
                events = await service(session).reminders(expert, offset=0, limit=20, request_id='cancel-list')
                assert events.total == 2 and events.unread_count == 1
                assert events.items[0].kind == 'appointment_cancelled'
                assert await session.scalar(select(func.count()).select_from(CareServiceEvent)) == 2
                episode = await session.get(CareEpisode, appointment.episode_id)
                episode.assigned_ibclc_id = None
                await session.flush()
                assert (await service(session).reminders(expert, offset=0, limit=20, request_id='unassigned')).items == []
                with pytest.raises(ApiError) as hidden:
                    await service(session).read_reminder(expert, event.id, 'old-link')
                assert hidden.value.status == 404
    asyncio.run(run())


@postgres
def test_events_follow_business_rollback_and_names_follow_current_consent():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            def service(session):
                return WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0])
            with pytest.raises(RuntimeError, match='rollback business operation'):
                async with sessions.begin() as session:
                    await booking(session).cancel(mom.user_id, appointment.id, VersionWrite(expected_version=appointment.version), 'cancel')
                    raise RuntimeError('rollback business operation')
            async with sessions.begin() as session:
                session.add(UserProfile(user_id=mom.user_id, preferred_name='Consented name'))
                session.add(CareProvider(user_id=wrong.user_id, display_name='Different expert', timezone='UTC', regions=['CA'], languages=['en'], active=True))
                await session.flush()
                result = await service(session).reminders(expert, offset=0, limit=20, request_id='before')
                assert result.total == 1 and result.items[0].kind == 'appointment_confirmed'
                assert result.items[0].patient_name == 'Consented name'
                assert (await service(session).reminders(wrong, offset=0, limit=20, request_id='foreign')).items == []
                with pytest.raises(ApiError) as denied:
                    await service(session).read_reminder(wrong, result.items[0].id, 'foreign-read')
                assert denied.value.status == 404
                session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ibclc_case', version=2, active=False, policy_version='2026-09-08'))
                await session.flush()
                hidden = await service(session).reminders(expert, offset=0, limit=20, request_id='after')
                assert hidden.items[0].patient_name is None
                assert 'Consented name' not in hidden.model_dump_json()
    asyncio.run(run())
