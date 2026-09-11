import asyncio
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.api.dependencies import require_current_user
from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth.models import DeviceSession
from app.modules.notifications.lifecycle import NotificationLifecycleService
from app.modules.notifications.models import Notification
from app.modules.notifications.router import get_push_registration_service
from tests.test_care_booking import postgres
from tests.test_notification_lifecycle_postgres import notification_case
from tests.test_push_registration import KEY

pytestmark = postgres


def test_notification_api_gates_tasks_with_real_session_permission_and_owner():
    async def run():
        async with notification_case() as (sessions, _booking, actors, clock, book):
            appointment = await book()
            app = create_app(Settings(app_env='test'))
            actor = [actors[0]]
            app.dependency_overrides[require_current_user] = lambda: actor[0]
            async def lifecycle():
                async with sessions.begin() as session:
                    yield NotificationLifecycleService(session, token_key=KEY, now=lambda: clock[0])
            app.dependency_overrides[get_push_registration_service] = lifecycle
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                root = '/v1/notifications'
                installation_id = str(uuid4())
                body = {'installation_id': installation_id, 'installation_secret': 'test-installation-secret-at-least-32', 'revision': 1,
                    'platform': 'android', 'permission': 'not_determined'}
                response = await client.post(f'{root}/installations', json={**body, 'owner_user_id': str(actors[1].user_id)})
                assert response.status_code == 422
                assert (await client.post(f'{root}/installations', json=body)).status_code == 200
                path = f'{root}/appointments/{appointment.id}/reminder'
                enabling = {'enabled': True, 'installation_id': installation_id}
                assert (await client.put(path, json=enabling)).status_code == 409
                body.update(revision=2, permission='authorized', token='local-device-token-not-a-real-provider-token')
                assert (await client.post(f'{root}/installations', json=body)).json()['token_registered'] is True
                assert (await client.put(path, json=enabling)).json()['status'] == 'scheduled'
                actor[0] = actors[1]
                assert (await client.get(path)).status_code == 404
                assert (await client.put(path, json=enabling)).status_code == 404
                actor[0] = actors[0]
                assert (await client.patch(f'{root}/preferences/appointments', json={'enabled': False})).json()['appointments'] is False
                assert (await client.get(path)).json()['enabled'] is False
                assert (await client.patch(f'{root}/preferences/marketing', json=enabling)).status_code == 422
                async with sessions.begin() as session:
                    device = await session.get(DeviceSession, UUID(actors[0].session_id))
                    device.status = 'revoked'
                body['revision'] = 3
                assert (await client.post(f'{root}/installations', json=body)).status_code == 401
            async with sessions() as session:
                value = await session.scalar(select(Notification).where(Notification.notification_type == 'appointment_reminder'))
                assert value.send_status == 'disabled'
    asyncio.run(run())
