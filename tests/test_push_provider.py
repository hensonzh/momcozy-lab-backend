import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.push.provider import FcmPushProvider, PushMessage


def test_fcm_payload_contains_only_opaque_routing_ids_and_generic_lock_screen_copy():
    async def run():
        captured = []
        def request(value):
            captured.append(json.loads(value.content))
            return httpx.Response(200, json={'name': 'projects/local-test/messages/accepted'})
        async def access_token():
            return 'synthetic-auth-token'
        async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
            provider = FcmPushProvider(project_id='local-test', access_token=access_token, client=client)
            message = PushMessage(notification_id=uuid4(), binding_id=uuid4(), expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), badge_count=67)
            result = await provider.send(token='synthetic-device-token', message=message)
        assert result.outcome == 'sent'
        payload = captured[0]['message']
        assert payload['data'] == {'notification_id': str(message.notification_id), 'binding_id': str(message.binding_id)}
        assert payload['notification'] == {'title': 'Momcozy', 'body': 'You have a new update. Open the app to view it.'}
        assert payload['android']['notification']['tag'] == str(message.notification_id)
        assert payload['apns']['headers']['apns-collapse-id'] == str(message.notification_id)
        assert payload['apns']['payload']['aps']['badge'] == 67
    asyncio.run(run())


@pytest.mark.parametrize(('status', 'code', 'outcome'), [
    (404, 'UNREGISTERED', 'invalid'), (400, 'INVALID_ARGUMENT', 'failed'),
    (429, 'QUOTA_EXCEEDED', 'retry'), (503, 'UNAVAILABLE', 'retry'), (403, 'SENDER_ID_MISMATCH', 'failed'),
])
def test_fcm_failure_classification_does_not_leak_payload_or_confuse_bad_requests_with_invalid_tokens(status, code, outcome):
    async def run():
        def request(_value):
            return httpx.Response(status, headers={'Retry-After': '120'}, json={'error': {'message': 'private provider response',
                'details': [{'@type': 'type.googleapis.com/google.firebase.fcm.v1.FcmError', 'errorCode': code}]}})
        async def access_token():
            return 'synthetic-auth-token'
        async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
            provider = FcmPushProvider(project_id='local-test', access_token=access_token, client=client)
            result = await provider.send(token='synthetic-device-token', message=PushMessage(notification_id=uuid4(), binding_id=uuid4(),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        assert result.outcome == outcome
        assert 'private provider response' not in repr(result) and 'synthetic-device-token' not in repr(result)
        if status == 429:
            assert result.retry_after_seconds == 120
    asyncio.run(run())
