from datetime import datetime, timezone

import jwt
import pytest

from app.core.settings import Settings
from app.infrastructure.video.provider import LiveKitVideoProvider, SandboxVideoProvider, build_video_provider


def test_livekit_credentials_are_short_lived_and_grant_only_the_requested_room():
    provider = LiveKitVideoProvider(url='wss://example.invalid', api_key='test-key', api_secret='test-secret-with-at-least-32-bytes')
    credentials = provider.credentials(room_name='care-room', participant_identity='actor-id')
    claims = jwt.decode(credentials.token, 'test-secret-with-at-least-32-bytes', algorithms=['HS256'])
    assert claims['sub'] == 'actor-id' and claims['iss'] == 'test-key'
    assert claims['video']['room'] == 'care-room' and claims['video']['roomJoin'] is True
    assert not claims['video'].get('roomAdmin') and not claims['video'].get('roomCreate') and not claims['video'].get('roomRecord')
    assert claims['video']['canPublishData'] is False
    assert 0 < claims['exp'] - datetime.now(timezone.utc).timestamp() <= 60
    assert 'test-secret' not in repr(provider) and credentials.token not in repr(credentials)


def test_sandbox_media_is_explicit_and_cannot_be_enabled_in_production():
    assert isinstance(build_video_provider(Settings(app_env='test', consultation_video_provider='sandbox')), SandboxVideoProvider)
    with pytest.raises(ValueError, match='sandbox'):
        build_video_provider(Settings(app_env='production', consultation_video_provider='sandbox'))
    with pytest.raises(ValueError, match='credentials'):
        build_video_provider(Settings(consultation_video_provider='livekit'))
    assert build_video_provider(Settings()).name == 'disabled'
