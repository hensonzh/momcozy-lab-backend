import asyncio
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import ApiError
from app.core.settings import Settings
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService
from app.modules.care.models import CareProvider
from app.modules.consultations.models import CareConsentRevision
from app.modules.ibclc.repository import WorkbenchRepository
from app.modules.ibclc.service import WorkbenchService
from app.modules.profiles.models import MaternalProfile, UserProfile
from test_care_booking import postgres
from test_care_rooms import room_case


@postgres
def test_workbench_queues_use_assignment_provider_day_and_explicit_case_consent():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            def service(session):
                return WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0])
            async with sessions.begin() as session:
                session.add(UserProfile(user_id=mom.user_id, preferred_name='Only assigned expert may see this name'))
                session.add(MaternalProfile(owner_user_id=mom.user_id, latest_delivery_date=date(2026, 8, 1)))
                session.add(CareProvider(user_id=wrong.user_id, display_name='Different expert', timezone='UTC', regions=['CA'], languages=['en'], active=True))
                await session.flush()
                day = appointment.starts_at.astimezone(ZoneInfo(appointment.timezone)).date()
                result = await service(session).appointments(expert, day=day, offset=0, limit=10, request_id='list')
                assert result.total == 1 and result.items[0].appointment.id == appointment.id
                assert result.items[0].patient_name == 'Only assigned expert may see this name'
                assert result.items[0].delivery_date == date(2026, 8, 1)
                assert result.items[0].package.id == 'feeding-confidence'
                assert result.items[0].case_consent is True
                previous = await service(session).appointments(expert, day=day - timedelta(days=1), offset=0, limit=10, request_id='previous')
                assert previous.total == 0
                foreign = await service(session).appointments(wrong, day=day, offset=0, limit=10, request_id='foreign')
                assert foreign.items == []
                with pytest.raises(ApiError) as patient:
                    await service(session).appointments(mom, day=day, offset=0, limit=10, request_id='patient')
                assert patient.value.status == 403
                session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ibclc_case', version=2, active=False, policy_version='2026-09-08'))
                await session.flush()
                withdrawn = await service(session).appointments(expert, day=day, offset=0, limit=10, request_id='withdrawn')
                assert withdrawn.items[0].case_consent is False
                assert withdrawn.items[0].patient_name is None and withdrawn.items[0].delivery_date is None
                assert withdrawn.items[0].episode.baby_id is None
                assert 'Only assigned expert' not in withdrawn.model_dump_json()
    asyncio.run(run())


@postgres
def test_client_search_and_detail_do_not_expose_unassigned_or_withdrawn_health_data():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            async with sessions.begin() as session:
                session.add(UserProfile(user_id=mom.user_id, preferred_name='Searchable client'))
                session.add(CareProvider(user_id=wrong.user_id, display_name='Different expert', timezone='UTC', regions=['CA'], languages=['en'], active=True))
                await session.flush()
                service = WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0])
                found = await service.clients(expert, query='Searchable', status='all', offset=0, limit=20, request_id='search')
                assert found.total == 1 and found.items[0].patient_ref == mom.user_id
                detail = await service.client_detail(expert, mom.user_id, 'detail')
                assert detail.appointment_total == 1 and detail.client.services[0].episode.id == appointment.episode_id
                with pytest.raises(ApiError) as foreign:
                    await service.client_detail(wrong, mom.user_id, 'foreign')
                assert foreign.value.status == 404
                session.add(CareConsentRevision(episode_id=appointment.episode_id, scope='ibclc_case', version=2, active=False, policy_version='2026-09-08'))
                await session.flush()
                hidden = await service.clients(expert, query='Searchable', status='all', offset=0, limit=20, request_id='hidden')
                assert hidden.items == []
                minimal = await service.client_detail(expert, mom.user_id, 'minimal')
                assert minimal.client.name is None and minimal.client.delivery_date is None
                assert minimal.client.services[0].case_consent is False
    asyncio.run(run())


@postgres
def test_workbench_calendar_uses_provider_week_and_excludes_cancelled_appointments():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            from app.modules.appointments.models import CareAppointment
            async with sessions.begin() as session:
                service = WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0])
                day = appointment.starts_at.astimezone(ZoneInfo(appointment.timezone)).date()
                week_start = day - timedelta(days=day.weekday())
                week = await service.calendar(expert, selected=week_start + timedelta(days=6), offset=0, limit=100, request_id='week')
                assert week.week_start == week_start and week.total == 1
                assert week.items[0].appointment.id == appointment.id
                following = await service.calendar(expert, selected=week_start + timedelta(days=7), offset=0, limit=100, request_id='next')
                assert following.total == 0
                record = await session.get(CareAppointment, appointment.id)
                record.status = 'cancelled'
                await session.flush()
                cancelled = await service.calendar(expert, selected=day, offset=0, limit=100, request_id='cancelled')
                assert cancelled.items == [] and cancelled.total == 0
    asyncio.run(run())


@postgres
def test_calendar_week_boundaries_include_overnight_overlap_and_daylight_saving():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, wrong, booking):
            from app.modules.appointments.models import CareAppointment
            async with sessions.begin() as session:
                service = WorkbenchService(WorkbenchRepository(session), AuditService(repository=AuditRepository(session)), Settings(), now=lambda: at[0])
                record = await session.get(CareAppointment, appointment.id)
                zone = ZoneInfo('America/Los_Angeles')
                for monday, hours in [(date(2026, 3, 2), 167), (date(2026, 10, 26), 169)]:
                    start = datetime.combine(monday, time.min, zone).astimezone(timezone.utc)
                    end = datetime.combine(monday + timedelta(days=7), time.min, zone).astimezone(timezone.utc)
                    assert (end - start).total_seconds() / 3600 == hours
                    for begins, finishes, count in [
                        (start - timedelta(minutes=30), start + timedelta(minutes=30), 1),
                        (start - timedelta(hours=1), start, 0),
                        (end - timedelta(hours=1), end, 1),
                        (end, end + timedelta(hours=1), 0),
                    ]:
                        record.starts_at, record.ends_at = begins, finishes
                        await session.flush()
                        result = await service.calendar(expert, selected=monday + timedelta(days=6), offset=0, limit=100, request_id='boundaries')
                        assert result.total == count and len(result.items) == count
    asyncio.run(run())
