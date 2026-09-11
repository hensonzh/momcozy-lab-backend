import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.errors import ApiError
from app.infrastructure.push.provider import PushResult
from app.modules.appointments.models import CareAppointment
from app.modules.appointments.schemas import BookingPrecheckWrite, HoldWrite, VersionWrite
from app.modules.auth.current_user import CurrentUser
from app.modules.auth.models import DeviceSession
from app.modules.care.event_models import CareServiceEvent
from app.modules.notifications.lifecycle import NotificationLifecycleService, REMINDER_LEAD
from app.modules.notifications.models import Notification, NotificationDelivery, NotificationEventReceipt, NotificationPreference, PushInstallation
from app.modules.notifications.push_registration import PushRegistrationService
from app.workers.notifications import process_next, project_next_event, reconcile_reminders
from tests.test_care_booking import database, postgres
from tests.test_push_registration import KEY, registration

pytestmark = postgres


@asynccontextmanager
async def notification_case():
    async with database() as (sessions, booking, at, provider, owners, episodes):
        async with sessions.kw['bind'].begin() as connection:
            for table in [DeviceSession.__table__, PushInstallation.__table__, Notification.__table__, NotificationDelivery.__table__,
                          NotificationPreference.__table__, NotificationEventReceipt.__table__]:
                await connection.run_sync(table.create)
        clock = [at]
        actors = []
        async with sessions.begin() as session:
            for owner in owners:
                device = DeviceSession(user_id=owner, device_id=str(uuid4()))
                session.add(device)
                await session.flush()
                actors.append(CurrentUser(user_id=owner, subject=str(owner), session_id=str(device.id), token_id=str(uuid4()),
                    roles=frozenset({'user'}), permissions=frozenset()))
        async def book(index=0):
            async with sessions.begin() as session:
                service = booking(session)
                check = await service.precheck(owners[index], episodes[index], BookingPrecheckWrite(region='CA', service_suitable=True, emergency_status='clear'), 'check')
                slots = await service.availability(owners[index], episodes[index], check.id, provider, at.date())
                slot = next(slot for slot in slots.slots if slot.available)
                held = await service.hold(owners[index], episodes[index], HoldWrite(eligibility_id=check.id, provider_id=provider, starts_at=slot.starts_at), str(uuid4()), 'hold')
                return await service.confirm(owners[index], held.id, VersionWrite(expected_version=held.version), 'confirm')
        yield sessions, booking, actors, clock, book


class RecordingPush:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = outcomes or {}
    async def send(self, *, token, message):
        self.calls.append((token, message))
        values = self.outcomes.get(token, [])
        return values.pop(0) if values else PushResult('sent', message_id='local-accepted')


def test_booking_projection_is_idempotent_and_reminder_requires_permission_before_scheduling():
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, book):
            appointment = await book()
            assert await project_next_event(sessions, push_available=True, now=lambda: clock[0])
            assert not await project_next_event(sessions, push_available=True, now=lambda: clock[0])
            installation_id = uuid4()
            async with sessions.begin() as session:
                lifecycle = NotificationLifecycleService(session, now=lambda: clock[0])
                pending = await lifecycle.appointment_reminder(actors[0].user_id, appointment.id)
                assert pending.send_status == 'disabled'
                with pytest.raises(ApiError) as denied:
                    await lifecycle.set_appointment_reminder(actors[0], appointment.id, enabled=True, installation_id=installation_id)
                assert denied.value.code == 'notification_permission_required'
                await PushRegistrationService(session, token_key=KEY, now=lambda: clock[0]).register(actors[0], registration(installation_id))
                enabled = await lifecycle.set_appointment_reminder(actors[0], appointment.id, enabled=True, installation_id=installation_id)
                assert enabled.send_status == 'scheduled' and enabled.trigger_at == appointment.starts_at - REMINDER_LEAD
                created = await session.scalar(select(Notification).where(Notification.notification_type == 'appointment_created'))
                created.status = 'read'
                event = await session.scalar(select(CareServiceEvent))
                await lifecycle.project_care_event(event)
                assert created.status == 'read'
                assert await session.scalar(select(func.count()).select_from(Notification)) == 2
            push = RecordingPush()
            assert not await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            clock[0] = appointment.starts_at - REMINDER_LEAD + timedelta(seconds=1)
            assert await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            assert len(push.calls) == 1
            async with sessions.begin() as session:
                reminder = await NotificationLifecycleService(session).appointment_reminder(actors[0].user_id, appointment.id)
                assert reminder.send_status == 'sent' and reminder.sent_at is not None and reminder.delivered_at is None
    asyncio.run(run())


def test_appointment_changes_cancel_old_reminders_and_cancellation_prevents_send():
    async def run():
        async with notification_case() as (sessions, booking, actors, clock, book):
            appointment = await book()
            async with sessions.begin() as session:
                installation_id = uuid4()
                await PushRegistrationService(session, token_key=KEY, now=lambda: clock[0]).register(actors[0], registration(installation_id))
                old = await NotificationLifecycleService(session, now=lambda: clock[0]).set_appointment_reminder(actors[0], appointment.id,
                    enabled=True, installation_id=installation_id)
                old_id = old.id
                changed = await session.get(CareAppointment, appointment.id)
                changed.starts_at += timedelta(hours=1)
                changed.ends_at += timedelta(hours=1)
                changed.version += 1
            await reconcile_reminders(sessions, push_available=True, now=lambda: clock[0])
            async with sessions.begin() as session:
                assert (await session.get(Notification, old_id)).send_status == 'canceled'
                current = await NotificationLifecycleService(session).appointment_reminder(actors[0].user_id, appointment.id)
                assert current.send_status == 'scheduled' and current.trigger_at == changed.starts_at - REMINDER_LEAD
                await booking(session).cancel(actors[0].user_id, appointment.id, VersionWrite(expected_version=changed.version), 'cancel')
            await reconcile_reminders(sessions, push_available=True, now=lambda: clock[0])
            clock[0] = changed.starts_at
            push = RecordingPush()
            assert not await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            assert not push.calls
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['retry', 'invalid'])
def test_delivery_failures_are_isolated_per_device_and_success_is_not_repeated(failure):
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, book):
            async with sessions.begin() as session:
                register = PushRegistrationService(session, token_key=KEY, now=lambda: clock[0])
                await register.register(actors[0], registration(uuid4(), token='first-device-token'))
                await register.register(actors[0], registration(uuid4(), token='second-device-token'))
            await book()
            await project_next_event(sessions, push_available=True, now=lambda: clock[0])
            push = RecordingPush({'second-device-token': [PushResult(failure, error_code='local-failure')]})
            await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            if failure == 'retry':
                clock[0] += timedelta(minutes=5)
                await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            tokens = [token for token, _message in push.calls]
            assert tokens.count('first-device-token') == 1
            assert tokens.count('second-device-token') == (2 if failure == 'retry' else 1)
            async with sessions.begin() as session:
                notification = await session.scalar(select(Notification).where(Notification.notification_type == 'appointment_created'))
                assert notification.send_status == 'sent'
                if failure == 'invalid':
                    assert await session.scalar(select(func.count()).select_from(PushInstallation).where(PushInstallation.invalidated_at.is_not(None))) == 1
    asyncio.run(run())


def test_permission_revocation_disables_tasks_and_recovery_never_sends_elapsed_reminders():
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, book):
            appointment = await book()
            installation_id = uuid4()
            async with sessions.begin() as session:
                registration_service = PushRegistrationService(session, token_key=KEY, now=lambda: clock[0])
                await registration_service.register(actors[0], registration(installation_id))
                await NotificationLifecycleService(session, now=lambda: clock[0]).set_appointment_reminder(actors[0], appointment.id,
                    enabled=True, installation_id=installation_id)
                await registration_service.register(actors[0], registration(installation_id, revision=2, permission='denied'))
            await reconcile_reminders(sessions, push_available=True, now=lambda: clock[0])
            async with sessions.begin() as session:
                reminder = await NotificationLifecycleService(session).appointment_reminder(actors[0].user_id, appointment.id)
                assert reminder.send_status == 'disabled'
                clock[0] = appointment.starts_at - REMINDER_LEAD + timedelta(seconds=1)
                await PushRegistrationService(session, token_key=KEY, now=lambda: clock[0]).register(actors[0], registration(installation_id, revision=3))
                restored = await NotificationLifecycleService(session, now=lambda: clock[0]).restore_future_reminders(actors[0], installation_id)
                assert restored == 0
            push = RecordingPush()
            assert not await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            assert not push.calls
    asyncio.run(run())


def test_retry_of_a_logged_out_device_is_canceled_without_stalling_successful_delivery():
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, book):
            async with sessions.begin() as session:
                register = PushRegistrationService(session, token_key=KEY, now=lambda: clock[0])
                await register.register(actors[0], registration(uuid4(), token='first-device-token'))
                second_session = DeviceSession(user_id=actors[0].user_id, device_id=str(uuid4()))
                session.add(second_session)
                await session.flush()
                second = CurrentUser(user_id=actors[0].user_id, subject=str(actors[0].user_id), session_id=str(second_session.id),
                    token_id=str(uuid4()), roles=frozenset({'user'}), permissions=frozenset())
                await register.register(second, registration(uuid4(), token='second-device-token'))
            await book()
            await project_next_event(sessions, push_available=True, now=lambda: clock[0])
            push = RecordingPush({'second-device-token': [PushResult('retry', error_code='local-failure')]})
            await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            async with sessions.begin() as session:
                (await session.get(DeviceSession, second_session.id)).status = 'revoked'
            clock[0] += timedelta(minutes=5)
            await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            async with sessions.begin() as session:
                value = await session.scalar(select(Notification).where(Notification.notification_type == 'appointment_created'))
                assert value.send_status == 'sent'
                assert await session.scalar(select(func.count()).select_from(NotificationDelivery).where(NotificationDelivery.status == 'pending')) == 0
            assert len(push.calls) == 2
    asyncio.run(run())


def test_replayed_booking_event_at_due_time_does_not_disable_an_enabled_reminder():
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, book):
            appointment = await book()
            async with sessions.begin() as session:
                installation_id = uuid4()
                lifecycle = NotificationLifecycleService(session, token_key=KEY, now=lambda: clock[0])
                await lifecycle.register(actors[0], registration(installation_id))
                reminder = await lifecycle.set_appointment_reminder(actors[0], appointment.id, enabled=True, installation_id=installation_id)
                clock[0] = reminder.trigger_at + timedelta(seconds=1)
                event = await session.scalar(select(CareServiceEvent))
                await lifecycle.project_care_event(event)
                assert reminder.send_status == 'scheduled'
    asyncio.run(run())


def test_future_requested_reminder_recovers_but_unrequested_and_immediate_updates_do_not():
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, book):
            appointment = await book()
            await project_next_event(sessions, push_available=True, now=lambda: clock[0])
            async with sessions.begin() as session:
                installation_id = uuid4()
                lifecycle = NotificationLifecycleService(session, token_key=KEY, now=lambda: clock[0])
                await lifecycle.register(actors[0], registration(installation_id))
                reminder = await lifecycle.appointment_reminder(actors[0].user_id, appointment.id)
                assert reminder.send_status == 'disabled'  # No implicit opt-in on login.
                await lifecycle.set_appointment_reminder(actors[0], appointment.id, enabled=True, installation_id=installation_id)
                await lifecycle.register(actors[0], registration(installation_id, revision=2, permission='denied'))
                assert reminder.send_status == 'disabled'
                await lifecycle.register(actors[0], registration(installation_id, revision=3))
                assert reminder.send_status == 'scheduled'
                created = await session.scalar(select(Notification).where(Notification.notification_type == 'appointment_created'))
                assert created.send_status == 'disabled'
    asyncio.run(run())


def test_parallel_projection_and_delivery_are_idempotent_and_resource_ownership_is_rechecked():
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, book):
            async with sessions.begin() as session:
                await PushRegistrationService(session, token_key=KEY, now=lambda: clock[0]).register(actors[0], registration(uuid4()))
            await book()
            await asyncio.gather(*(project_next_event(sessions, push_available=True, now=lambda: clock[0]) for _ in range(3)))
            push = RecordingPush()
            await asyncio.gather(*(process_next(sessions, push, token_key=KEY, now=lambda: clock[0]) for _ in range(3)))
            assert len(push.calls) == 1
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count()).select_from(NotificationEventReceipt)) == 1
                value = await session.scalar(select(Notification).where(Notification.notification_type == 'appointment_created'))
                lifecycle = NotificationLifecycleService(session, now=lambda: clock[0])
                with pytest.raises(ApiError) as error:
                    await lifecycle.open_notification(actors[1], value.id)
                assert error.value.status == 404
                value.related_resource_id = uuid4()
                opened = await lifecycle.open_notification(actors[0], value.id)
                assert not opened.resource_available and opened.route is None
                assert value.status == 'read'
                value.send_status = 'pending'
            await process_next(sessions, push, token_key=KEY, now=lambda: clock[0])
            assert len(push.calls) == 1
    asyncio.run(run())


def test_agent_notification_target_is_checked_against_runtime_owner_before_open():
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, _book):
            thread = uuid4()
            async with sessions.begin() as session:
                value = Notification(owner_user_id=actors[0].user_id, notification_type='agent_update', title='Update', body='',
                    related_resource_type='agent_conversation', related_resource_id=thread, payload={})
                session.add(value)
                await session.flush()
                calls = []
                async def verify(owner, target):
                    calls.append((owner, target))
                service = NotificationLifecycleService(session, now=lambda: clock[0])
                opened = await service.open_notification(actors[0], value.id, verify_conversation=verify)
                assert opened.route == f'/?conversationId={thread}'
                assert calls == [(actors[0].user_id, thread)]
                async def removed(owner, target):
                    raise ApiError(code='not_found', message='Removed.', status=404)
                assert not (await service.open_notification(actors[0], value.id, verify_conversation=removed)).resource_available
    asyncio.run(run())
