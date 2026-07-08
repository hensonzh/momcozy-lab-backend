import asyncio
import json
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.modules.voice import providers
from production_backend.app.modules.voice.providers import (
    DisabledVoiceProvider,
    DoubaoRealtimeVoiceProvider,
    LocalStubVoiceProvider,
    create_voice_provider,
)


SESSION_ID = "test-session"


def test_voice_provider_factory_returns_stable_disabled_and_local_stub_providers() -> None:
    assert isinstance(create_voice_provider(Settings(app_env="test", voice_provider="disabled")), DisabledVoiceProvider)
    assert isinstance(create_voice_provider(Settings(app_env="test", voice_provider="local_stub")), LocalStubVoiceProvider)
    assert isinstance(
        create_voice_provider(Settings(app_env="test", voice_provider="doubao", voice_api_key="doubao-test")),
        DoubaoRealtimeVoiceProvider,
    )


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


def test_doubao_realtime_voice_provider_uses_legacy_bidirectional_tts_contract() -> None:
    actor_user_id = uuid4()
    _FakeDoubaoStreamWebSocket.sent_events = []
    _FakeDoubaoStreamWebSocket.last_url = None
    _FakeDoubaoStreamWebSocket.last_kwargs = None
    provider = DoubaoRealtimeVoiceProvider(
        Settings(
            app_env="test",
            voice_provider="doubao",
            voice_api_key="doubao-test",
            voice_tts_resource_id="seed-tts-2.0",
            voice_tts_voice_type="saturn_zh_female_qingyingduoduo_cs_tob",
            voice_tts_speed_ratio=1.1,
        ),
        connect=_fake_stream_connect,
    )

    chunks = asyncio.run(_collect_bytes(provider.synthesize_pcm_stream(actor_user_id=actor_user_id, text="你好，今天状态怎么样？")))

    assert chunks == [b"pcm-1", b"pcm-2"]
    assert _FakeDoubaoStreamWebSocket.last_url == "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    headers = (_FakeDoubaoStreamWebSocket.last_kwargs or {})["additional_headers"]
    assert headers["X-Api-Key"] == "doubao-test"
    assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert "X-Api-Connect-Id" in headers
    assert [event["event"] for event in _FakeDoubaoStreamWebSocket.sent_events] == [
        providers.DOUBAO_EVENT_START_CONNECTION,
        providers.DOUBAO_EVENT_START_SESSION,
        providers.DOUBAO_EVENT_TASK_REQUEST,
        providers.DOUBAO_EVENT_FINISH_SESSION,
        providers.DOUBAO_EVENT_FINISH_CONNECTION,
    ]
    task_payload = _FakeDoubaoStreamWebSocket.sent_events[2]["payload"]
    assert task_payload["namespace"] == "BidirectionalTTS"
    assert task_payload["req_params"]["text"] == "你好，今天状态怎么样？"
    assert task_payload["req_params"]["speaker"] == "saturn_zh_female_qingyingduoduo_cs_tob"
    assert task_payload["req_params"]["audio_params"] == {"format": "pcm", "sample_rate": 24000, "speech_rate": 10}


async def _collect_events(stream):
    return [event async for event in stream]


async def _collect_bytes(stream):
    return [chunk async for chunk in stream]


def _content(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big", signed=True) + encoded


def _payload(value: bytes | dict) -> bytes:
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False).encode("utf-8")
    return len(value).to_bytes(4, "big", signed=True) + value


def _server_frame(event: int, *, message_type: int = providers.DOUBAO_WS_FULL_SERVER_RESPONSE, payload: bytes | dict | None = None) -> bytes:
    frame = bytearray(
        providers._doubao_ws_header(
            message_type=message_type,
            message_type_flags=providers.DOUBAO_WS_FLAG_WITH_EVENT,
            serial_method=providers.DOUBAO_WS_JSON if isinstance(payload, dict) else providers.DOUBAO_WS_NO_SERIALIZATION,
        )
    )
    frame.extend(event.to_bytes(4, "big", signed=True))
    if event == providers.DOUBAO_EVENT_CONNECTION_STARTED:
        frame.extend(_content("conn-1"))
    elif event in {
        providers.DOUBAO_EVENT_SESSION_STARTED,
        providers.DOUBAO_EVENT_SESSION_FINISHED,
        providers.DOUBAO_EVENT_SESSION_FAILED,
    }:
        frame.extend(_content(SESSION_ID))
        frame.extend(_content("{}"))
    elif event == providers.DOUBAO_EVENT_TTS_RESPONSE:
        frame.extend(_content(SESSION_ID))
        frame.extend(_payload(payload if isinstance(payload, bytes) else b""))
    return bytes(frame)


def _parse_client_frame(raw: bytes) -> dict:
    event = int.from_bytes(raw[4:8], "big", signed=True)
    offset = 8
    session_id = ""
    if event in {
        providers.DOUBAO_EVENT_START_SESSION,
        providers.DOUBAO_EVENT_TASK_REQUEST,
        providers.DOUBAO_EVENT_FINISH_SESSION,
    }:
        size = int.from_bytes(raw[offset : offset + 4], "big", signed=True)
        offset += 4
        session_id = raw[offset : offset + size].decode("utf-8")
        offset += size
    payload = {}
    if offset + 4 <= len(raw):
        size = int.from_bytes(raw[offset : offset + 4], "big", signed=True)
        offset += 4
        if size:
            payload = json.loads(raw[offset : offset + size].decode("utf-8"))
    return {"event": event, "session_id": session_id, "payload": payload}


class _FakeDoubaoStreamWebSocket:
    sent_events: list[dict] = []
    last_url: str | None = None
    last_kwargs: dict | None = None

    def __init__(self) -> None:
        self._messages = iter(
            [
                _server_frame(providers.DOUBAO_EVENT_CONNECTION_STARTED),
                _server_frame(providers.DOUBAO_EVENT_SESSION_STARTED),
                _server_frame(
                    providers.DOUBAO_EVENT_TTS_RESPONSE,
                    message_type=providers.DOUBAO_WS_AUDIO_ONLY_RESPONSE,
                    payload=b"pcm-1",
                ),
                _server_frame(
                    providers.DOUBAO_EVENT_TTS_RESPONSE,
                    message_type=providers.DOUBAO_WS_AUDIO_ONLY_RESPONSE,
                    payload=b"pcm-2",
                ),
                _server_frame(providers.DOUBAO_EVENT_SESSION_FINISHED),
            ]
        )

    async def __aenter__(self) -> "_FakeDoubaoStreamWebSocket":
        return self

    async def __aexit__(self, exc_type, exc, exc_tb) -> None:
        return None

    async def send(self, payload: bytes) -> None:
        type(self).sent_events.append(_parse_client_frame(payload))

    async def recv(self) -> bytes:
        return await self.__anext__()

    def __aiter__(self) -> "_FakeDoubaoStreamWebSocket":
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration from None


def _fake_stream_connect(url: str, **kwargs) -> _FakeDoubaoStreamWebSocket:
    _FakeDoubaoStreamWebSocket.last_url = url
    _FakeDoubaoStreamWebSocket.last_kwargs = kwargs
    return _FakeDoubaoStreamWebSocket()
