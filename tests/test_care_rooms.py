import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.errors import ApiError
from app.infrastructure.video.provider import SandboxVideoProvider
from app.modules.appointments.schemas import BookingPrecheckWrite, HoldWrite, VersionWrite
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService, IdempotencyService
from app.modules.auth import CurrentUser
from app.modules.care.models import CareEpisode
from app.modules.consultations.models import CareConsentRevision, CareIntakeRevision
from app.modules.consultations.repository import ConsultationRepository
from app.modules.consultations.room_models import CareSessionConsumption, CareVideoCommand
from app.modules.consultations.room_repository import RoomRepository
from app.modules.consultations.room_schemas import EndWrite, LocationWrite, PresenceWrite
from app.modules.consultations.room_service import RoomService
from app.modules.consultations.schemas import ConsentWrite
from app.modules.consultations.service import ConsultationService
from app.workers.care_video import process_next
from test_care_booking import database, postgres


def actor(user_id, role='mom'):
    return CurrentUser(user_id=user_id, subject=str(user_id), session_id='test', token_id='test', roles=frozenset({role}), permissions=frozenset())


@asynccontextmanager
async def room_case(*, intake=True, consents=True):
    async with database() as (sessions, booking, now, provider, owners, episodes):
        async with sessions.begin() as session:
            service = booking(session)
            check = await service.precheck(owners[0], episodes[0], BookingPrecheckWrite(region='CA', service_suitable=True, emergency_status='clear'), 'check')
            slots = await service.availability(owners[0], episodes[0], check.id, provider, (now + timedelta(days=1)).date())
            held = await service.hold(owners[0], episodes[0], HoldWrite(eligibility_id=check.id, provider_id=provider, starts_at=slots.slots[0].starts_at), 'hold', 'hold')
            appointment = await service.confirm(owners[0], held.id, VersionWrite(expected_version=1), 'confirm')
            if intake:
                session.add(CareIntakeRevision(appointment_id=appointment.id, episode_id=episodes[0], version=1,
                    symptoms=['latch_difficulty'], feeding_goal='Test goal', support_needed='', profile={}, submitted_at=now))
            if consents:
                for scope in ['ibclc_case', 'video']:
                    session.add(CareConsentRevision(episode_id=episodes[0], scope=scope, version=1, active=True, policy_version='2026-09-08', recorded_at=now))
        at = [appointment.starts_at - timedelta(minutes=5)]
        video = SandboxVideoProvider()
        def service(session, *, media=video):
            audit = AuditRepository(session)
            return RoomService(RoomRepository(session), ConsultationRepository(session), booking(session, at=at[0]),
                AuditService(repository=audit), IdempotencyService(repository=audit), media, now=lambda: at[0])
        yield sessions, service, at, appointment, actor(owners[0]), actor(provider, 'ibclc'), actor(owners[1], 'ibclc'), booking


async def enter(sessions, service, at, appointment, mom, expert):
    async with sessions.begin() as session:
        await service(session).check_location(mom, appointment.id, LocationWrite(region='CA'), 'location')
        context = await service(session).prepare(mom, appointment.id, 'prepare')
        assert context.consultation.room_status == 'creating'
    assert await process_next(sessions, SandboxVideoProvider(), now=lambda: at[0])
    async with sessions.begin() as session:
        first = await service(session).join(mom, appointment.id, 'mom-join', 'join')
        second = await service(session).join(expert, appointment.id, 'expert-join', 'join')
        for user, joined in [(mom, first), (expert, second)]:
            await service(session).presence(user, appointment.id, PresenceWrite(connection_id=joined.connection_id, presence='joined'), 'present')
    return first, second


@postgres
def test_leaving_and_rejoining_never_finishes_consultation_and_completed_end_consumes_once():
    async def run():
        async with room_case() as (sessions, service, at, appointment, mom, expert, wrong, _):
            first, _ = await enter(sessions, service, at, appointment, mom, expert)
            async with sessions.begin() as session:
                current = service(session)
                with pytest.raises(ApiError) as denied:
                    await current.context(wrong, appointment.id)
                assert denied.value.status == 404
                with pytest.raises(ApiError) as role:
                    await current.start(mom, appointment.id, VersionWrite(expected_version=1), 'wrong-role')
                assert role.value.status == 403
                started = await current.start(expert, appointment.id, VersionWrite(expected_version=1), 'start')
                assert started.consultation.status == 'in_progress'
                left = await current.presence(mom, appointment.id, PresenceWrite(connection_id=first.connection_id, presence='left'), 'leave')
                assert left.consultation.status == 'in_progress' and left.appointment.status == 'in_progress'
                with pytest.raises(ApiError) as replay_after_leave:
                    await current.join(mom, appointment.id, 'mom-join', 'stale-join')
                assert replay_after_leave.value.code == 'connection_closed'
                rejoined = await current.join(mom, appointment.id, 'mom-rejoin', 'rejoin')
                with pytest.raises(ApiError) as stale_leave:
                    await current.presence(mom, appointment.id, PresenceWrite(connection_id=first.connection_id, presence='left'), 'old-tab')
                assert stale_leave.value.code == 'connection_replaced'
                assert rejoined.connection_id != first.connection_id
                episode = await session.get(CareEpisode, appointment.episode_id)
                assert episode.remaining_sessions == 2
                expected = started.consultation.version
            async def end():
                async with sessions.begin() as session:
                    return await service(session).end(expert, appointment.id, EndWrite(expected_version=expected, reason='completed'), 'end')
            outcomes = await asyncio.gather(end(), end())
            assert all(value.consultation.status == 'note_pending' and value.consultation.end_reason == 'completed' for value in outcomes)
            async with sessions.begin() as session:
                assert (await session.get(CareEpisode, appointment.episode_id)).remaining_sessions == 1
                assert await session.scalar(select(func.count()).select_from(CareSessionConsumption)) == 1
                assert await session.scalar(select(func.count()).select_from(CareVideoCommand).where(CareVideoCommand.operation == 'close')) == 1
                with pytest.raises(ApiError):
                    await service(session).join(mom, appointment.id, 'after-end', 'after-end')
            assert await process_next(sessions, SandboxVideoProvider(), now=lambda: at[0])
            async with sessions.begin() as session:
                context = await service(session).context(mom, appointment.id)
                assert context.consultation.room_status == 'closed' and context.consultation.status == 'note_pending'
    asyncio.run(run())


@postgres
def test_intake_consent_location_and_join_window_are_checked_before_issuing_credentials():
    async def run():
        async with room_case(consents=False) as (sessions, service, at, appointment, mom, expert, _, _):
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as missing:
                    await service(session).prepare(mom, appointment.id, 'prepare')
                assert missing.value.code == 'consent_required'
            async with sessions.begin() as session:
                for scope in ['ibclc_case', 'video']:
                    session.add(CareConsentRevision(episode_id=appointment.episode_id, scope=scope, version=1, active=True, policy_version='2026-09-08', recorded_at=at[0]))
            async with sessions.begin() as session:
                denied = await service(session).check_location(mom, appointment.id, LocationWrite(region='NY'), 'outside-region')
                assert denied.decision == 'blocked'
                with pytest.raises(ApiError) as location:
                    await service(session).prepare(mom, appointment.id, 'prepare')
                assert location.value.code == 'location_required'
                at[0] -= timedelta(minutes=20)
                with pytest.raises(ApiError) as early:
                    await service(session).prepare(expert, appointment.id, 'early')
                assert early.value.code == 'room_not_open'
        async with room_case(intake=False) as (sessions, service, at, appointment, mom, expert, _, _):
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as missing:
                    await service(session).prepare(mom, appointment.id, 'prepare')
                assert missing.value.code == 'intake_required'
                with pytest.raises(ApiError) as early_no_show:
                    await service(session).end(expert, appointment.id, EndWrite(expected_version=0, reason='user_no_show'), 'early-no-show')
                assert early_no_show.value.code == 'no_show_too_early'
                at[0] = appointment.starts_at + timedelta(minutes=10)
                ended = await service(session).end(expert, appointment.id, EndWrite(expected_version=0, reason='user_no_show'), 'no-show')
                assert ended.consultation.status == 'no_show'
                assert (await session.get(CareEpisode, appointment.episode_id)).remaining_sessions == 2
    asyncio.run(run())


@postgres
def test_consent_withdrawal_closes_media_and_old_connections_cannot_rejoin_after_restore():
    async def run():
        async with room_case() as (sessions, service, at, appointment, mom, expert, _, booking):
            first, _ = await enter(sessions, service, at, appointment, mom, expert)
            async with sessions.begin() as session:
                old_room = await RoomRepository(session).consultation(appointment.id)
                old_name = old_room.room_name
                intake = ConsultationService(ConsultationRepository(session), booking(session, at=at[0]), AuditService(repository=AuditRepository(session)), now=lambda: at[0])
                await intake.set_consent(mom.user_id, appointment.episode_id, ConsentWrite(expected_version=1, scope='video', active=False, policy_version='2026-09-08'), 'withdraw')
                context = await service(session).context(mom, appointment.id)
                assert not context.video_consent and context.consultation.room_status == 'closing'
                with pytest.raises(ApiError):
                    await service(session).join(expert, appointment.id, 'withdrawn', 'join')
            assert await process_next(sessions, SandboxVideoProvider(), now=lambda: at[0])
            async with sessions.begin() as session:
                intake = ConsultationService(ConsultationRepository(session), booking(session, at=at[0]), AuditService(repository=AuditRepository(session)), now=lambda: at[0])
                await intake.set_consent(mom.user_id, appointment.episode_id, ConsentWrite(expected_version=2, scope='video', active=True, policy_version='2026-09-08'), 'restore')
                await service(session).prepare(mom, appointment.id, 'reopen')
                new_room = await RoomRepository(session).consultation(appointment.id)
                assert new_room.room_name != old_name and new_room.room_generation == 2
            assert await process_next(sessions, SandboxVideoProvider(), now=lambda: at[0])
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as stale:
                    await service(session).join(mom, appointment.id, 'mom-join', 'stale')
                assert stale.value.code == 'connection_replaced'
                current = await service(session).join(mom, appointment.id, 'fresh', 'fresh')
                assert current.connection_id != first.connection_id
    asyncio.run(run())


@postgres
def test_video_worker_retries_after_failure_and_cancellation_never_reopens_room():
    class UnavailableVideo(SandboxVideoProvider):
        async def create(self, room_name):
            raise TimeoutError('Provider unavailable; credentials must not enter persisted errors.')
    async def run():
        async with room_case() as (sessions, service, at, appointment, mom, expert, _, booking):
            async with sessions.begin() as session:
                await service(session).check_location(mom, appointment.id, LocationWrite(region='CA'), 'location')
                await service(session).prepare(mom, appointment.id, 'prepare')
            assert await process_next(sessions, UnavailableVideo(), now=lambda: at[0])
            async with sessions.begin() as session:
                command = await session.scalar(select(CareVideoCommand))
                assert command.status == 'pending' and command.attempts == 1 and command.last_error == 'TimeoutError'
                context = await service(session).context(mom, appointment.id)
                assert context.consultation.room_status == 'failed'
                await booking(session, at=at[0]).cancel(mom.user_id, appointment.id, VersionWrite(expected_version=context.appointment.version), 'cancel')
            at[0] += timedelta(minutes=1)
            while await process_next(sessions, SandboxVideoProvider(), now=lambda: at[0]):
                pass
            async with sessions.begin() as session:
                context = await service(session).context(mom, appointment.id)
                assert context.appointment.status == 'cancelled' and context.consultation.status == 'cancelled' and context.consultation.room_status == 'closed'
                assert (await session.get(CareEpisode, appointment.episode_id)).remaining_sessions == 2
    asyncio.run(run())


@postgres
@pytest.mark.parametrize('reason,status', [('technical_failure', 'failed'), ('safety_escalation', 'note_pending')])
def test_interrupted_consultation_requires_expert_and_preserves_entitlement(reason, status):
    async def run():
        async with room_case() as (sessions, service, at, appointment, mom, expert, _, _):
            first, _ = await enter(sessions, service, at, appointment, mom, expert)
            async with sessions.begin() as session:
                current = service(session)
                started = await current.start(expert, appointment.id, VersionWrite(expected_version=1), 'start')
                at[0] = appointment.ends_at + timedelta(hours=1)
                # An active consultation remains active across the appointment window and a long call.
                heartbeat = await current.presence(mom, appointment.id, PresenceWrite(connection_id=first.connection_id, presence='joined'), 'heartbeat')
                assert heartbeat.consultation.status == 'in_progress'
                with pytest.raises(ApiError) as no_show:
                    await current.end(expert, appointment.id, EndWrite(expected_version=started.consultation.version, reason='user_no_show'), 'no-show')
                assert no_show.value.code == 'no_show_not_allowed'
                with pytest.raises(ApiError) as user_end:
                    await current.end(mom, appointment.id, EndWrite(expected_version=started.consultation.version, reason=reason), 'user-end')
                assert user_end.value.status == 403
                ended = await current.end(expert, appointment.id, EndWrite(expected_version=started.consultation.version, reason=reason), 'interrupted')
                assert ended.consultation.status == status and ended.consultation.end_reason == reason
                assert (await session.get(CareEpisode, appointment.episode_id)).remaining_sessions == 2
                assert await session.scalar(select(func.count()).select_from(CareSessionConsumption)) == 0
    asyncio.run(run())
