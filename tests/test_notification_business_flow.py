import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.modules.appointments.schemas import VersionWrite
from app.modules.audit.repository import AuditRepository
from app.modules.audit.service import AuditService, IdempotencyService
from app.modules.auth.models import DeviceSession
from app.modules.consultations.room_schemas import EndWrite
from app.modules.documentation.repository import DocumentationRepository
from app.modules.documentation.schemas import NoteVersionWrite, NoteWrite, PlanWrite
from app.modules.documentation.service import DocumentationService
from app.modules.notifications.lifecycle import NotificationLifecycleService
from app.modules.notifications.models import Notification, NotificationDelivery, NotificationEventReceipt, NotificationPreference, PushInstallation
from app.workers.notifications import process_next, project_next_event
from tests.test_care_booking import postgres
from tests.test_care_documentation import NOTE, PLAN
from tests.test_care_rooms import enter, room_case
from tests.test_notification_lifecycle_postgres import RecordingPush
from tests.test_push_registration import KEY, registration


@postgres
def test_all_six_notification_types_follow_real_booking_consultation_and_publication_events():
    async def run():
        async with room_case() as (sessions, rooms, at, appointment, mom, expert, _wrong, booking):
            async with sessions.kw['bind'].begin() as connection:
                for table in [DeviceSession.__table__, PushInstallation.__table__, Notification.__table__, NotificationDelivery.__table__, NotificationPreference.__table__, NotificationEventReceipt.__table__]:
                    await connection.run_sync(table.create)
            async def project():
                while await project_next_event(sessions, push_available=True, now=lambda: at[0]):
                    pass
            at[0] = appointment.starts_at - timedelta(hours=1)
            async with sessions.begin() as session:
                device = DeviceSession(user_id=mom.user_id, device_id=str(uuid4()))
                session.add(device)
                await session.flush()
                actor = replace(mom, session_id=str(device.id))
                lifecycle = NotificationLifecycleService(session, token_key=KEY, now=lambda: at[0])
                installation_id = uuid4()
                await lifecycle.register(actor, registration(installation_id))
                await lifecycle.set_appointment_reminder(actor, appointment.id, enabled=True, installation_id=installation_id)
            await project()
            at[0] = appointment.starts_at - timedelta(minutes=15)
            push = RecordingPush()
            while await process_next(sessions, push, token_key=KEY, now=lambda: at[0]):
                pass
            assert any(message.notification_id for _token, message in push.calls)
            at[0] = appointment.starts_at - timedelta(minutes=8)
            await enter(sessions, rooms, at, appointment, mom, expert)
            async with sessions.begin() as session:
                started = await rooms(session).start(expert, appointment.id, VersionWrite(expected_version=1), 'start')
            await project()
            async with sessions.begin() as session:
                await rooms(session).end(expert, appointment.id, EndWrite(expected_version=started.consultation.version, reason='completed'), 'complete')
                audit = AuditRepository(session)
                documentation = DocumentationService(DocumentationRepository(session), booking(session, at=at[0]), AuditService(repository=audit), IdempotencyService(repository=audit), now=lambda: at[0])
                await documentation.save_note(expert, appointment.id, NoteWrite(expected_revision=0, expected_version=0, content=NOTE), 'note', 'note')
                await documentation.sign_note(expert, appointment.id, NoteVersionWrite(expected_revision=1, expected_version=1), 'sign', 'sign')
                await documentation.save_plan(expert, appointment.id, PlanWrite(expected_version=0, content=PLAN), 'plan', 'plan')
                await documentation.publish(expert, appointment.id, VersionWrite(expected_version=1), 'publish', 'publish')
            await project()
            async with sessions.begin() as session:
                notifications = list(await session.scalars(select(Notification)))
                assert {value.notification_type for value in notifications} == {'appointment_created', 'appointment_reminder', 'consultation_started', 'consultation_ended', 'expert_feedback', 'service_progress_updated'}
                assert all(value.owner_user_id == mom.user_id for value in notifications)
                for value in notifications:
                    target = await NotificationLifecycleService(session, now=lambda: at[0]).open_notification(actor, value.id)
                    assert target.resource_available
                    expected = '/summary' if value.notification_type in {'consultation_ended', 'expert_feedback'} else '/room' if value.notification_type == 'consultation_started' else None
                    if expected:
                        assert target.route.endswith(expected)
                    assert value.status == 'read'
    asyncio.run(run())
