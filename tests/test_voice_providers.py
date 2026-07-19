import asyncio
import json
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.core.settings import Settings
from app.modules.voice import providers
from app.modules.voice.providers import (
    DisabledVoiceProvider,
    DoubaoRealtimeVoiceProvider,
    LocalStubVoiceProvider,
    OpenAiSpeechTranscriber,
    SpeechTranscription,
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


def test_doubao_realtime_voice_provider_keeps_speech_transcription_independent_from_tts() -> None:
    actor_user_id = uuid4()
    transcriber = _FakeSpeechTranscriber()
    provider = DoubaoRealtimeVoiceProvider(
        Settings(app_env="test", voice_provider="doubao", voice_api_key="doubao-test"),
        transcriber=transcriber,
    )

    transcription = asyncio.run(
        provider.transcribe_chunk(
            actor_user_id=actor_user_id,
            body=b"audio",
            filename="speech.wav",
            content_type="audio/wav",
            language="zh-CN",
        )
    )

    assert transcription.text == "你好"
    assert transcriber.calls == [
        {
            "actor_user_id": actor_user_id,
            "body": b"audio",
            "filename": "speech.wav",
            "content_type": "audio/wav",
            "language": "zh-CN",
        }
    ]


def test_openai_speech_transcriber_reports_missing_stt_credentials() -> None:
    transcriber = OpenAiSpeechTranscriber(Settings(app_env="test", openai_api_key=""))

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            transcriber.transcribe_chunk(
                actor_user_id=uuid4(),
                body=b"audio",
                filename="speech.wav",
                content_type="audio/wav",
                language=None,
            )
        )

    assert exc_info.value.code == "openai_stt_config_missing"
    assert exc_info.value.status == 400


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


def test_doubao_realtime_voice_provider_accepts_legacy_app_access_key_credentials() -> None:
    actor_user_id = uuid4()
    _FakeDoubaoStreamWebSocket.sent_events = []
    _FakeDoubaoStreamWebSocket.last_url = None
    _FakeDoubaoStreamWebSocket.last_kwargs = None
    provider = DoubaoRealtimeVoiceProvider(
        Settings(
            app_env="test",
            voice_provider="doubao",
            voice_app_id="legacy-app-id",
            voice_access_key="legacy-access-token",
            voice_tts_resource_id="seed-tts-2.0",
            voice_tts_voice_type="saturn_zh_female_qingyingduoduo_cs_tob",
        ),
        connect=_fake_stream_connect,
    )

    chunks = asyncio.run(_collect_bytes(provider.synthesize_pcm_stream(actor_user_id=actor_user_id, text="你好")))

    assert chunks == [b"pcm-1", b"pcm-2"]
    headers = (_FakeDoubaoStreamWebSocket.last_kwargs or {})["additional_headers"]
    assert "X-Api-Key" not in headers
    assert headers["X-Api-App-Key"] == "legacy-app-id"
    assert headers["X-Api-Access-Key"] == "legacy-access-token"


def test_doubao_realtime_voice_session_keeps_one_bidirectional_tts_connection() -> None:
    actor_user_id = uuid4()
    _FakeDoubaoSessionWebSocket.sent_events = []
    _FakeDoubaoSessionWebSocket.last_url = None
    _FakeDoubaoSessionWebSocket.last_kwargs = None
    client = _FakeRealtimeVoiceClient(
        [
            {"type": "append", "text": "你好呀。"},
            {"type": "finish"},
        ]
    )
    provider = DoubaoRealtimeVoiceProvider(
        Settings(
            app_env="test",
            voice_provider="doubao",
            voice_api_key="doubao-test",
            voice_tts_resource_id="seed-tts-2.0",
            voice_tts_voice_type="saturn_zh_female_qingyingduoduo_cs_tob",
            voice_tts_speed_ratio=1.1,
        ),
        connect=_fake_session_connect,
    )

    asyncio.run(provider.run_realtime_session(actor_user_id=actor_user_id, client=client))

    assert [frame["type"] for frame in client.json_frames] == ["ready", "done"]
    assert client.bytes_frames == [b"session-pcm"]
    assert _FakeDoubaoSessionWebSocket.last_url == "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    headers = (_FakeDoubaoSessionWebSocket.last_kwargs or {})["additional_headers"]
    assert headers["X-Api-Key"] == "doubao-test"
    assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert [event["event"] for event in _FakeDoubaoSessionWebSocket.sent_events] == [
        providers.DOUBAO_EVENT_START_CONNECTION,
        providers.DOUBAO_EVENT_START_SESSION,
        providers.DOUBAO_EVENT_TASK_REQUEST,
        providers.DOUBAO_EVENT_FINISH_SESSION,
        providers.DOUBAO_EVENT_FINISH_CONNECTION,
    ]
    task_payload = _FakeDoubaoSessionWebSocket.sent_events[2]["payload"]
    assert task_payload["namespace"] == "BidirectionalTTS"
    assert task_payload["req_params"]["text"] == "你好呀。"
    assert task_payload["req_params"]["speaker"] == "saturn_zh_female_qingyingduoduo_cs_tob"
    assert task_payload["req_params"]["audio_params"] == {"format": "pcm", "sample_rate": 24000, "speech_rate": 10}


def test_doubao_realtime_voice_session_resets_timeout_while_audio_is_active(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="production_backend.voice")
    spoken_text = "这是一段持续时间超过固定截止窗口的语音。"
    client = _FakeRealtimeVoiceClient(
        [
            {"type": "append", "text": spoken_text},
            {"type": "finish"},
        ]
    )
    provider = DoubaoRealtimeVoiceProvider(
        Settings(
            app_env="test",
            voice_provider="doubao",
            voice_api_key="doubao-test",
            voice_request_timeout_seconds=1,
        ),
        connect=_fake_slow_active_session_connect,
    )

    asyncio.run(provider.run_realtime_session(actor_user_id=uuid4(), client=client))

    assert client.bytes_frames == [b"pcm-1", b"pcm-2", b"pcm-3"]
    assert [frame["type"] for frame in client.json_frames] == ["ready", "done"]
    assert "outcome=completed" in caplog.text
    assert "segments=1" in caplog.text
    assert spoken_text not in caplog.text


def test_doubao_realtime_voice_session_still_times_out_after_inactivity() -> None:
    async def run_scenario() -> None:
        upstream_task = asyncio.create_task(asyncio.sleep(3600))
        try:
            with pytest.raises(RuntimeError, match="became inactive"):
                await providers._wait_for_doubao_session_completion(
                    session_done=asyncio.Event(),
                    session_activity=asyncio.Event(),
                    upstream_task=upstream_task,
                    inactivity_timeout_seconds=0.01,
                )
        finally:
            upstream_task.cancel()
            await asyncio.gather(upstream_task, return_exceptions=True)

    asyncio.run(run_scenario())


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


class _FakeDoubaoSessionWebSocket:
    sent_events: list[dict] = []
    last_url: str | None = None
    last_kwargs: dict | None = None

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def __aenter__(self) -> "_FakeDoubaoSessionWebSocket":
        return self

    async def __aexit__(self, exc_type, exc, exc_tb) -> None:
        return None

    async def send(self, payload: bytes) -> None:
        event = _parse_client_frame(payload)
        type(self).sent_events.append(event)
        event_type = event["event"]
        if event_type == providers.DOUBAO_EVENT_START_CONNECTION:
            await self._queue.put(_server_frame(providers.DOUBAO_EVENT_CONNECTION_STARTED))
        elif event_type == providers.DOUBAO_EVENT_START_SESSION:
            await self._queue.put(_server_frame(providers.DOUBAO_EVENT_SESSION_STARTED))
        elif event_type == providers.DOUBAO_EVENT_TASK_REQUEST:
            await self._queue.put(
                _server_frame(
                    providers.DOUBAO_EVENT_TTS_RESPONSE,
                    message_type=providers.DOUBAO_WS_AUDIO_ONLY_RESPONSE,
                    payload=b"session-pcm",
                )
            )
        elif event_type == providers.DOUBAO_EVENT_FINISH_SESSION:
            await self._queue.put(_server_frame(providers.DOUBAO_EVENT_SESSION_FINISHED))

    async def recv(self) -> bytes:
        return await self._queue.get()

    def __aiter__(self) -> "_FakeDoubaoSessionWebSocket":
        return self

    async def __anext__(self) -> bytes:
        return await self._queue.get()


def _fake_session_connect(url: str, **kwargs) -> _FakeDoubaoSessionWebSocket:
    _FakeDoubaoSessionWebSocket.last_url = url
    _FakeDoubaoSessionWebSocket.last_kwargs = kwargs
    return _FakeDoubaoSessionWebSocket()


class _SlowActiveDoubaoSessionWebSocket:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._producer: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "_SlowActiveDoubaoSessionWebSocket":
        return self

    async def __aexit__(self, exc_type, exc, exc_tb) -> None:
        if self._producer is not None and not self._producer.done():
            self._producer.cancel()
            await asyncio.gather(self._producer, return_exceptions=True)

    async def send(self, payload: bytes) -> None:
        event = _parse_client_frame(payload)["event"]
        if event == providers.DOUBAO_EVENT_START_CONNECTION:
            await self._queue.put(_server_frame(providers.DOUBAO_EVENT_CONNECTION_STARTED))
        elif event == providers.DOUBAO_EVENT_START_SESSION:
            await self._queue.put(_server_frame(providers.DOUBAO_EVENT_SESSION_STARTED))
        elif event == providers.DOUBAO_EVENT_FINISH_SESSION:
            self._producer = asyncio.create_task(self._emit_active_audio())

    async def _emit_active_audio(self) -> None:
        for index in range(1, 4):
            await asyncio.sleep(0.4)
            await self._queue.put(
                _server_frame(
                    providers.DOUBAO_EVENT_TTS_RESPONSE,
                    message_type=providers.DOUBAO_WS_AUDIO_ONLY_RESPONSE,
                    payload=f"pcm-{index}".encode(),
                )
            )
        await self._queue.put(_server_frame(providers.DOUBAO_EVENT_SESSION_FINISHED))

    async def recv(self) -> bytes:
        return await self._queue.get()

    def __aiter__(self) -> "_SlowActiveDoubaoSessionWebSocket":
        return self

    async def __anext__(self) -> bytes:
        return await self._queue.get()


def _fake_slow_active_session_connect(url: str, **kwargs) -> _SlowActiveDoubaoSessionWebSocket:
    return _SlowActiveDoubaoSessionWebSocket()


class _FakeRealtimeVoiceClient:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._messages = iter(json.dumps(message, ensure_ascii=False) for message in messages)
        self.json_frames: list[dict[str, object]] = []
        self.bytes_frames: list[bytes] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.json_frames.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.bytes_frames.append(payload)

    async def receive_text(self) -> str:
        try:
            return next(self._messages)
        except StopIteration:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable") from None


class _FakeSpeechTranscriber:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def transcribe_chunk(self, **kwargs) -> SpeechTranscription:
        self.calls.append(kwargs)
        return SpeechTranscription(text="你好")
