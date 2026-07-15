import asyncio
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.modules.voice.service import VoiceService


def test_voice_local_stub_main_flow_returns_stable_transcription_stream_and_session_shapes() -> None:
    actor_user_id = uuid4()
    service = VoiceService(settings=Settings(app_env="test", voice_provider="local_stub"))

    transcription = asyncio.run(
        service.transcribe_chunk(
            actor_user_id=actor_user_id,
            body=b"audio",
            filename="speech.wav",
            content_type="audio/wav",
            language="zh-CN",
        )
    )
    pcm_chunks = asyncio.run(_collect_bytes(service.synthesize_pcm_stream(actor_user_id=actor_user_id, text="hello")))
    session_events = asyncio.run(_collect_events(service.realtime_session_events(actor_user_id=actor_user_id)))

    assert transcription.text == ""
    assert pcm_chunks == []
    assert [event["type"] for event in session_events] == ["session.open", "audio.done"]
    assert [event["sequence"] for event in session_events] == [0, 1]
    assert session_events[0]["session_id"] == session_events[1]["session_id"]


def test_voice_disabled_provider_has_stable_error_contract_for_all_entry_points() -> None:
    actor_user_id = uuid4()
    service = VoiceService(settings=Settings(app_env="test", voice_provider="disabled"))

    with pytest.raises(ApiError) as transcribe_exc:
        asyncio.run(
            service.transcribe_chunk(
                actor_user_id=actor_user_id,
                body=b"audio",
                filename="speech.wav",
                content_type="audio/wav",
                language=None,
            )
        )
    with pytest.raises(ApiError) as stream_exc:
        service.synthesize_pcm_stream(actor_user_id=actor_user_id, text="hello")
    with pytest.raises(ApiError) as session_exc:
        asyncio.run(_collect_events(service.realtime_session_events(actor_user_id=actor_user_id)))

    assert transcribe_exc.value.code == "voice_provider_disabled"
    assert transcribe_exc.value.status == 503
    assert stream_exc.value.code == "voice_provider_disabled"
    assert session_exc.value.code == "voice_provider_disabled"
    assert service.disabled_frame() == {
        "type": "error",
        "code": "voice_provider_disabled",
        "message": "Voice provider is not configured.",
    }


def test_voice_chunk_and_text_validation_match_user_visible_limits() -> None:
    actor_user_id = uuid4()
    service = VoiceService(settings=Settings(app_env="test", voice_provider="local_stub", file_upload_max_bytes=3))

    with pytest.raises(ApiError) as empty_audio_exc:
        asyncio.run(
            service.transcribe_chunk(
                actor_user_id=actor_user_id,
                body=b"",
                filename="speech.wav",
                content_type="audio/wav",
                language=None,
            )
        )
    with pytest.raises(ApiError) as large_audio_exc:
        asyncio.run(
            service.transcribe_chunk(
                actor_user_id=actor_user_id,
                body=b"toolarge",
                filename="speech.wav",
                content_type="audio/wav",
                language=None,
            )
        )
    with pytest.raises(ApiError) as empty_text_exc:
        service.synthesize_pcm_stream(actor_user_id=actor_user_id, text=" ")

    assert empty_audio_exc.value.code == "validation_failed"
    assert large_audio_exc.value.code == "payload_too_large"
    assert large_audio_exc.value.details == {"max_bytes": 3}
    assert empty_text_exc.value.code == "validation_failed"


async def _collect_bytes(stream):
    return [chunk async for chunk in stream]


async def _collect_events(stream):
    return [event async for event in stream]
