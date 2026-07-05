import asyncio
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.modules.voice.providers import DisabledVoiceProvider, LocalStubVoiceProvider, create_voice_provider


def test_voice_provider_factory_returns_stable_disabled_and_local_stub_providers() -> None:
    assert isinstance(create_voice_provider(Settings(app_env="test", voice_provider="disabled")), DisabledVoiceProvider)
    assert isinstance(create_voice_provider(Settings(app_env="test", voice_provider="local_stub")), LocalStubVoiceProvider)


def test_disabled_voice_provider_fails_before_provider_work() -> None:
    provider = DisabledVoiceProvider()

    with pytest.raises(ApiError) as exc_info:
        provider.ensure_available()

    assert exc_info.value.code == "voice_provider_disabled"


def test_local_stub_voice_provider_shapes_match_service_contract() -> None:
    actor_user_id = uuid4()
    provider = LocalStubVoiceProvider()

    transcription = asyncio.run(
        provider.transcribe_chunk(
            actor_user_id=actor_user_id,
            body=b"audio",
            filename="speech.wav",
            content_type="audio/wav",
            language="zh-CN",
        )
    )
    session_events = asyncio.run(_collect_events(provider.realtime_session_events(actor_user_id=actor_user_id)))

    assert transcription.text == ""
    assert [event["type"] for event in session_events] == ["session.open", "audio.done"]


async def _collect_events(stream):
    return [event async for event in stream]
