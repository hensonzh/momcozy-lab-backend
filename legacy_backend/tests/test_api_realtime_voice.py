from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from momcozy_agent.api import routes
from momcozy_agent.api_app import create_app


SESSION_ID = "test-session"


def _content(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big", signed=True) + encoded


def _payload(value: bytes | dict) -> bytes:
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False).encode("utf-8")
    return len(value).to_bytes(4, "big", signed=True) + value


def _server_frame(event: int, *, message_type: int = routes.VOLC_WS_FULL_SERVER_RESPONSE, payload: bytes | dict | None = None) -> bytes:
    frame = bytearray(
        routes._volc_ws_header(
            message_type=message_type,
            message_type_flags=routes.VOLC_WS_FLAG_WITH_EVENT,
            serial_method=routes.VOLC_WS_JSON if isinstance(payload, dict) else routes.VOLC_WS_NO_SERIALIZATION,
        )
    )
    frame.extend(event.to_bytes(4, "big", signed=True))
    if event == routes.VOLC_EVENT_CONNECTION_STARTED:
        frame.extend(_content("conn-1"))
    elif event in {
        routes.VOLC_EVENT_SESSION_STARTED,
        routes.VOLC_EVENT_SESSION_FINISHED,
        routes.VOLC_EVENT_SESSION_FAILED,
    }:
        frame.extend(_content(SESSION_ID))
        frame.extend(_content("{}"))
    elif event == routes.VOLC_EVENT_TTS_RESPONSE:
        frame.extend(_content(SESSION_ID))
        frame.extend(_payload(payload if isinstance(payload, bytes) else b""))
    elif event in {routes.VOLC_EVENT_TTS_SENTENCE_START, routes.VOLC_EVENT_TTS_SENTENCE_END}:
        frame.extend(_content(SESSION_ID))
        frame.extend(_payload(payload or {}))
    return bytes(frame)


def _parse_client_frame(raw: bytes) -> dict:
    event = int.from_bytes(raw[4:8], "big", signed=True)
    offset = 8
    session_id = ""
    if event in {routes.VOLC_EVENT_START_SESSION, routes.VOLC_EVENT_TASK_REQUEST, routes.VOLC_EVENT_FINISH_SESSION}:
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


class _FakeVolcStreamWebSocket:
    sent_events: list[dict] = []
    last_url: str | None = None
    last_kwargs: dict | None = None

    def __init__(self) -> None:
        self._messages = iter(
            [
                _server_frame(routes.VOLC_EVENT_CONNECTION_STARTED),
                _server_frame(routes.VOLC_EVENT_SESSION_STARTED),
                _server_frame(routes.VOLC_EVENT_TTS_RESPONSE, message_type=routes.VOLC_WS_AUDIO_ONLY_RESPONSE, payload=b"pcm-1"),
                _server_frame(routes.VOLC_EVENT_TTS_RESPONSE, message_type=routes.VOLC_WS_AUDIO_ONLY_RESPONSE, payload=b"pcm-2"),
                _server_frame(routes.VOLC_EVENT_SESSION_FINISHED),
            ]
        )

    async def __aenter__(self) -> "_FakeVolcStreamWebSocket":
        return self

    async def __aexit__(self, exc_type, exc, exc_tb) -> None:
        return None

    async def send(self, payload: bytes) -> None:
        type(self).sent_events.append(_parse_client_frame(payload))

    async def recv(self) -> bytes:
        return await self.__anext__()

    def __aiter__(self) -> "_FakeVolcStreamWebSocket":
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration from None


def _fake_stream_connect(url: str, **kwargs) -> _FakeVolcStreamWebSocket:
    _FakeVolcStreamWebSocket.last_url = url
    _FakeVolcStreamWebSocket.last_kwargs = kwargs
    return _FakeVolcStreamWebSocket()


class _FakeVolcSessionWebSocket:
    sent_events: list[dict] = []
    last_url: str | None = None
    last_kwargs: dict | None = None

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def __aenter__(self) -> "_FakeVolcSessionWebSocket":
        return self

    async def __aexit__(self, exc_type, exc, exc_tb) -> None:
        return None

    async def send(self, payload: bytes) -> None:
        event = _parse_client_frame(payload)
        type(self).sent_events.append(event)
        event_type = event["event"]
        if event_type == routes.VOLC_EVENT_START_CONNECTION:
            await self._queue.put(_server_frame(routes.VOLC_EVENT_CONNECTION_STARTED))
        elif event_type == routes.VOLC_EVENT_START_SESSION:
            await self._queue.put(_server_frame(routes.VOLC_EVENT_SESSION_STARTED))
        elif event_type == routes.VOLC_EVENT_TASK_REQUEST:
            await self._queue.put(
                _server_frame(routes.VOLC_EVENT_TTS_RESPONSE, message_type=routes.VOLC_WS_AUDIO_ONLY_RESPONSE, payload=b"session-pcm")
            )
        elif event_type == routes.VOLC_EVENT_FINISH_SESSION:
            await self._queue.put(_server_frame(routes.VOLC_EVENT_SESSION_FINISHED))

    async def recv(self) -> bytes:
        return await self._queue.get()

    def __aiter__(self) -> "_FakeVolcSessionWebSocket":
        return self

    async def __anext__(self) -> bytes:
        return await self._queue.get()


def _fake_session_connect(url: str, **kwargs) -> _FakeVolcSessionWebSocket:
    _FakeVolcSessionWebSocket.last_url = url
    _FakeVolcSessionWebSocket.last_kwargs = kwargs
    return _FakeVolcSessionWebSocket()


class RealtimeVoiceStreamApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ENTRY_API_KEY"] = "test-token"
        self.headers = {"Authorization": "Bearer test-token"}
        _FakeVolcStreamWebSocket.sent_events = []
        _FakeVolcStreamWebSocket.last_url = None
        _FakeVolcStreamWebSocket.last_kwargs = None
        _FakeVolcSessionWebSocket.sent_events = []
        _FakeVolcSessionWebSocket.last_url = None
        _FakeVolcSessionWebSocket.last_kwargs = None

    def test_realtime_voice_stream_requires_volc_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENTRY_API_KEY": "test-token",
                "VOLC_TTS_API_KEY": "",
                "VOLC_REALTIME_VOICE_API_KEY": "",
                "VOLCENGINE_TTS_API_KEY": "",
                "VOLC_TTS_APP_ID": "",
                "VOLC_TTS_ACCESS_TOKEN": "",
                "VOLC_TTS_ACCESS_KEY": "",
            },
            clear=False,
        ):
            client = TestClient(create_app())
            response = client.get("/v1/realtime-voice-stream", params={"text": "你好"}, headers=self.headers)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "volc_tts_config_missing")

    def test_realtime_voice_stream_uses_volc_bidirectional_tts(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "ENTRY_API_KEY": "test-token",
                    "VOLC_TTS_API_KEY": "volc-test",
                    "VOLC_TTS_RESOURCE_ID": "seed-tts-2.0",
                    "VOLC_TTS_VOICE_TYPE": "saturn_zh_female_qingyingduoduo_cs_tob",
                    "VOLC_TTS_SPEED_RATIO": "1.1",
                },
                clear=False,
            ),
            patch("websockets.connect", _fake_stream_connect),
        ):
            client = TestClient(create_app())
            response = client.get(
                "/v1/realtime-voice-stream",
                params={"user_id": "mom-user-1", "text": "你好，今天状态怎么样？"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("audio/pcm"))
        self.assertEqual(response.headers["x-mai-audio-format"], "pcm16")
        self.assertEqual(response.headers["x-mai-audio-sample-rate"], "24000")
        self.assertEqual(response.content, b"pcm-1pcm-2")

        self.assertEqual(_FakeVolcStreamWebSocket.last_url, "wss://openspeech.bytedance.com/api/v3/tts/bidirection")
        headers = (_FakeVolcStreamWebSocket.last_kwargs or {})["additional_headers"]
        self.assertEqual(headers["X-Api-Key"], "volc-test")
        self.assertEqual(headers["X-Api-Resource-Id"], "seed-tts-2.0")
        self.assertIn("X-Api-Connect-Id", headers)

        sent_events = _FakeVolcStreamWebSocket.sent_events
        self.assertEqual(
            [event["event"] for event in sent_events],
            [
                routes.VOLC_EVENT_START_CONNECTION,
                routes.VOLC_EVENT_START_SESSION,
                routes.VOLC_EVENT_TASK_REQUEST,
                routes.VOLC_EVENT_FINISH_SESSION,
                routes.VOLC_EVENT_FINISH_CONNECTION,
            ],
        )
        task_payload = sent_events[2]["payload"]
        self.assertEqual(task_payload["namespace"], "BidirectionalTTS")
        self.assertEqual(task_payload["req_params"]["text"], "你好，今天状态怎么样？")
        self.assertEqual(task_payload["req_params"]["speaker"], "saturn_zh_female_qingyingduoduo_cs_tob")
        self.assertEqual(
            task_payload["req_params"]["audio_params"],
            {"format": "pcm", "sample_rate": 24000, "speech_rate": 10},
        )

    def test_realtime_voice_session_keeps_one_volc_websocket_for_segments(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "ENTRY_API_KEY": "test-token",
                    "VOLC_TTS_API_KEY": "volc-test",
                    "VOLC_TTS_RESOURCE_ID": "seed-tts-2.0",
                    "VOLC_TTS_VOICE_TYPE": "saturn_zh_female_qingyingduoduo_cs_tob",
                    "VOLC_TTS_SPEED_RATIO": "1.1",
                },
                clear=False,
            ),
            patch("websockets.connect", _fake_session_connect),
        ):
            client = TestClient(create_app())
            with client.websocket_connect(
                "/v1/realtime-voice-session?token=test-token&user_id=mom-user-1"
            ) as websocket:
                ready = websocket.receive_json()
                websocket.send_json({"type": "append", "text": "你好呀。"})
                audio = websocket.receive_bytes()
                websocket.send_json({"type": "finish"})
                done = websocket.receive_json()

        self.assertEqual(ready["type"], "ready")
        self.assertEqual(ready["audio_format"], "pcm16")
        self.assertEqual(ready["sample_rate"], 24000)
        self.assertEqual(audio, b"session-pcm")
        self.assertEqual(done["type"], "done")

        self.assertEqual(_FakeVolcSessionWebSocket.last_url, "wss://openspeech.bytedance.com/api/v3/tts/bidirection")
        sent_events = _FakeVolcSessionWebSocket.sent_events
        self.assertEqual(
            [event["event"] for event in sent_events],
            [
                routes.VOLC_EVENT_START_CONNECTION,
                routes.VOLC_EVENT_START_SESSION,
                routes.VOLC_EVENT_TASK_REQUEST,
                routes.VOLC_EVENT_FINISH_SESSION,
                routes.VOLC_EVENT_FINISH_CONNECTION,
            ],
        )
        task_payload = sent_events[2]["payload"]
        self.assertEqual(task_payload["req_params"]["text"], "你好呀。")
        self.assertEqual(task_payload["req_params"]["speaker"], "saturn_zh_female_qingyingduoduo_cs_tob")
        self.assertEqual(
            task_payload["req_params"]["audio_params"],
            {"format": "pcm", "sample_rate": 24000, "speech_rate": 10},
        )


if __name__ == "__main__":
    unittest.main()
