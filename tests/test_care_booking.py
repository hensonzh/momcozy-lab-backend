
from app.modules.baby.profile_models import BabyProfile
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import ApiError
from app.infrastructure.db.base import Base
from app.modules.audit.models import AuditLog, IdempotencyKey
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService, IdempotencyService
from app.modules.appointments.availability import slots_for_day
from app.modules.appointments.models import BookingEligibility, CareAppointment, ProviderAvailability, ProviderCalendarBlock
from app.modules.appointments.repository import AppointmentRepository
from app.modules.appointments.schemas import BookingEligibilityRead, BookingPrecheckWrite, HoldWrite, VersionWrite
from app.modules.appointments.service import AppointmentService
from app.modules.care.models import CareEligibility, CareEpisode, CareOrder, CareProvider
from app.modules.care.event_models import CareServiceEvent, CareServiceEventRead
from app.modules.reports.models import CareConversationLink, CareReport, CareReportReview
from app.modules.lactation.models import LactationRecord
from app.modules.baby.models import BabyRecord
from app.modules.consultations.models import CareConsentRevision, CareIntakeRevision
from app.modules.consultations.room_models import CareConsultation, CareLocationCheck, CareRoomParticipant, CareSessionConsumption, CareVideoCommand
from app.modules.documentation.models import ClinicalNote, CarePlanDraft, CarePlanPublication, CareTaskProgress
from app.modules.profiles.models import MaternalProfile, UserProfile
from app.modules.users.models import User

DATABASE_URL = os.getenv('MOMCOZY_TEST_DATABASE_URL', '')
postgres = pytest.mark.skipif(not DATABASE_URL, reason='Requires isolated MOMCOZY_TEST_DATABASE_URL PostgreSQL database.')


def test_availability_handles_dst_gaps_and_both_folds_without_duplicates():
    spring = slots_for_day(date(2026, 3, 8), 'America/Los_Angeles', [(6, 60, 240, 60)])
    assert [start.isoformat() for start, _ in spring] == ['2026-03-08T09:00:00+00:00', '2026-03-08T10:00:00+00:00']
    autumn = slots_for_day(date(2026, 11, 1), 'America/Los_Angeles', [(6, 60, 180, 60)])
    assert len(autumn) == 3 and len(set(autumn)) == 3
    assert all(end - start == timedelta(hours=1) for start, end in autumn)


def test_booking_writes_reject_owner_duration_and_naive_time_overrides():
    with pytest.raises(ValidationError):
        HoldWrite.model_validate({'eligibility_id': str(uuid4()), 'provider_id': str(uuid4()), 'starts_at': '2026-09-10T09:00:00'})
    with pytest.raises(ValidationError):
        HoldWrite.model_validate({'eligibility_id': str(uuid4()), 'provider_id': str(uuid4()), 'starts_at': '2026-09-10T09:00:00Z', 'duration_minutes': 240})
    with pytest.raises(ValidationError):
        BookingPrecheckWrite.model_validate({'region': 'CA', 'service_suitable': True, 'emergency_status': 'clear', 'owner_user_id': str(uuid4())})


@asynccontextmanager
async def database():
    schema = f'care_booking_{uuid4().hex}'
    admin = create_async_engine(DATABASE_URL)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(DATABASE_URL, connect_args={'server_settings': {'search_path': schema}})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
                User.__table__, UserProfile.__table__, MaternalProfile.__table__, BabyProfile.__table__, AuditLog.__table__, IdempotencyKey.__table__, CareProvider.__table__,
                CareEligibility.__table__, CareOrder.__table__, CareEpisode.__table__, BookingEligibility.__table__,
                ProviderAvailability.__table__, ProviderCalendarBlock.__table__, CareAppointment.__table__,
                CareIntakeRevision.__table__, CareConsentRevision.__table__, CareConsultation.__table__,
                CareLocationCheck.__table__, CareRoomParticipant.__table__, CareSessionConsumption.__table__, CareVideoCommand.__table__,
                ClinicalNote.__table__, CarePlanDraft.__table__, CarePlanPublication.__table__, CareTaskProgress.__table__,
                CareServiceEvent.__table__, CareServiceEventRead.__table__, CareConversationLink.__table__, CareReport.__table__, CareReportReview.__table__, LactationRecord.__table__, BabyRecord.__table__]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
        provider, owners, episodes = uuid4(), [uuid4(), uuid4()], [uuid4(), uuid4()]
        async with sessions.begin() as session:
            session.add_all([User(id=value) for value in [provider, *owners]])
            await session.flush()
            session.add(CareProvider(user_id=provider, display_name='Test IBCLC', timezone='America/Los_Angeles', regions=['CA'], languages=['en'], active=True, sandbox=True))
            await session.flush()
            for weekday in range(7):
                session.add(ProviderAvailability(provider_id=provider, weekday=weekday, start_minute=540, end_minute=720, slot_minutes=60))
            for owner, episode in zip(owners, episodes, strict=True):
                eligibility = CareEligibility(owner_user_id=owner, package_id='feeding-confidence', region='CA', eligible=True, expires_at=now + timedelta(days=1))
                session.add(eligibility)
                await session.flush()
                order = CareOrder(owner_user_id=owner, eligibility_id=eligibility.id, package_id='feeding-confidence', price_minor=21900, duration_days=7, total_sessions=2, region='CA', status='paid')
                session.add(order)
                await session.flush()
                session.add(CareEpisode(id=episode, owner_user_id=owner, order_id=order.id, package_id='feeding-confidence', total_sessions=2, remaining_sessions=2))
        def service(session, at=None):
            audit = AuditRepository(session)
            return AppointmentService(AppointmentRepository(session), AuditService(repository=audit), IdempotencyService(repository=audit), sandbox_enabled=True, now=lambda: at or now)
        yield sessions, service, now, provider, owners, episodes
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@postgres
def test_booking_conflicts_retries_cancel_and_owner_isolation_on_postgres():
    async def run():
        async with database() as (sessions, service, now, provider, owners, episodes):
            checks = []
            async with sessions.begin() as session:
                current = service(session)
                refused = await current.precheck(owners[0], episodes[0], BookingPrecheckWrite(region='CA', service_suitable=True, emergency_status='needs_help'), 'risk')
                assert not refused.eligible and refused.reason == 'emergency_help'
                for owner, episode in zip(owners, episodes, strict=True):
                    checks.append(await current.precheck(owner, episode, BookingPrecheckWrite(region='CA', service_suitable=True, emergency_status='clear'), 'precheck'))
                available = await current.availability(owners[0], episodes[0], checks[0].id, provider, (now + timedelta(days=1)).date())
                slot = available.slots[0]
            async def hold(index, key):
                async with sessions.begin() as session:
                    return await service(session).hold(owners[index], episodes[index], HoldWrite(eligibility_id=checks[index].id, provider_id=provider, starts_at=slot.starts_at), key, 'hold')
            outcomes = await asyncio.gather(hold(0, 'hold-a'), hold(1, 'hold-b'), return_exceptions=True)
            winners = [index for index, value in enumerate(outcomes) if not isinstance(value, Exception)]
            assert len(winners) == 1
            winner = winners[0]
            loser = 1 - winner
            assert isinstance(outcomes[loser], ApiError) and outcomes[loser].code == 'slot_unavailable'
            held = outcomes[winner]
            retry = await hold(winner, 'hold-a' if winner == 0 else 'hold-b')
            assert retry.id == held.id
            async with sessions.begin() as session:
                current = service(session)
                with pytest.raises(ApiError) as hidden:
                    await current.read(owners[loser], held.id)
                assert hidden.value.status == 404
                confirmed = await current.confirm(owners[winner], held.id, VersionWrite(expected_version=held.version), 'confirm')
                again = await current.confirm(owners[winner], held.id, VersionWrite(expected_version=held.version), 'retry-confirm')
                assert confirmed.id == again.id and confirmed.status == 'confirmed' and confirmed.version == 2
                episode = await session.get(CareEpisode, episodes[winner])
                assert episode.remaining_sessions == 2 and episode.assigned_ibclc_id == provider
            async with sessions.begin() as session:
                current = service(session)
                cancelled = await current.cancel(owners[winner], held.id, VersionWrite(expected_version=2), 'cancel')
                replay = await current.cancel(owners[winner], held.id, VersionWrite(expected_version=2), 'retry-cancel')
                assert cancelled.status == replay.status == 'cancelled'
                episode = await session.get(CareEpisode, episodes[winner])
                assert episode.remaining_sessions == 2
            replacement = await hold(loser, 'released-slot')
            assert replacement.status == 'held' and replacement.id != held.id
            async with sessions.begin() as session:
                actions = list(await session.scalars(select(AuditLog.action).where(AuditLog.resource_id == str(held.id))))
                assert actions.count('care.appointment.confirmed') == 1 and actions.count('care.appointment.cancelled') == 1
    asyncio.run(run())


@postgres
def test_expired_hold_region_blocks_and_off_grid_slots_are_not_bookable():
    async def run():
        async with database() as (sessions, service, now, provider, owners, episodes):
            async with sessions.begin() as session:
                current = service(session)
                denied = await current.precheck(owners[0], episodes[0], BookingPrecheckWrite(region='TX', service_suitable=True, emergency_status='clear'), 'unsupported')
                assert not denied.eligible and denied.reason == 'region_unavailable'
                check = await current.precheck(owners[0], episodes[0], BookingPrecheckWrite(region='CA', service_suitable=True, emergency_status='clear'), 'eligible')
                available = await current.availability(owners[0], episodes[0], check.id, provider, (now + timedelta(days=1)).date())
                slot = available.slots[0]
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as invalid:
                    await service(session).hold(owners[0], episodes[0], HoldWrite(eligibility_id=check.id, provider_id=provider, starts_at=slot.starts_at + timedelta(minutes=1)), 'off-grid', 'invalid')
                assert invalid.value.code == 'slot_unavailable'
            async with sessions.begin() as session:
                held = await service(session).hold(owners[0], episodes[0], HoldWrite(eligibility_id=check.id, provider_id=provider, starts_at=slot.starts_at), 'first', 'hold')
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as expired:
                    await service(session, now + timedelta(minutes=11)).confirm(owners[0], held.id, VersionWrite(expected_version=1), 'expired')
                assert expired.value.code == 'hold_expired'
                state = await service(session, now + timedelta(minutes=11)).read(owners[0], held.id)
                assert state.status == 'expired'
            async with sessions.begin() as session:
                session.add(ProviderCalendarBlock(provider_id=provider, starts_at=slot.starts_at, ends_at=slot.ends_at, title='Private'))
            async with sessions.begin() as session:
                availability = await service(session, now + timedelta(minutes=11)).availability(owners[0], episodes[0], check.id, provider, slot.starts_at.date())
                assert not next(item for item in availability.slots if item.starts_at == slot.starts_at).available
                assert next(item for item in availability.slots if item.starts_at == slot.ends_at).available
    asyncio.run(run())


def test_booking_api_requires_session_and_does_not_accept_owner_override():
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    from app.api.dependencies import require_current_user
    from app.core.settings import Settings
    from app.factory import create_app
    from app.modules.appointments.router import get_appointment_service
    from app.modules.auth import CurrentUser
    app = create_app(Settings(app_env='test'))
    client = TestClient(app)
    episode = uuid4()
    assert client.get(f'/v1/care/episodes/{episode}/booking').status_code == 401
    owner = uuid4()
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(user_id=owner, subject=str(owner), session_id='test', token_id='test', roles=frozenset({'user'}), permissions=frozenset())
    service = AsyncMock()
    app.dependency_overrides[get_appointment_service] = lambda: service
    check = BookingEligibilityRead(id=uuid4(), episode_id=episode, region='CA', service_suitable=True, emergency_status='clear', eligible=True, reason='', expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
    service.precheck.return_value = check
    body = {'region': 'CA', 'service_suitable': True, 'emergency_status': 'clear'}
    response = client.post(f'/v1/care/episodes/{episode}/booking-eligibility', json=body)
    assert response.status_code == 201 and service.precheck.call_args.args[:2] == (owner, episode)
    assert client.post(f'/v1/care/episodes/{episode}/booking-eligibility', json={**body, 'owner_user_id': str(uuid4())}).status_code == 422
    assert client.post(f'/v1/care/episodes/{episode}/holds', json={'eligibility_id': str(check.id), 'provider_id': str(uuid4()), 'starts_at': '2030-01-01T09:00:00Z'}).status_code == 422
    service.hold.assert_not_called()
