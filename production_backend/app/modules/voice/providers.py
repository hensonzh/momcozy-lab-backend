from __future__ import annotations

import asyncio
import contextlib
import json
from io import BytesIO
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

from ...core.errors import ApiError
from ...core.settings import Settings


DOUBAO_TTS_MAX_INPUT_CHARS = 4096
DOUBAO_TTS_NAMESPACE = "BidirectionalTTS"
OPENAI_STT_DEFAULT_MODEL = "whisper-1"

DOUBAO_WS_FULL_CLIENT_REQUEST = 0b0001
DOUBAO_WS_FULL_SERVER_RESPONSE = 0b1001
DOUBAO_WS_AUDIO_ONLY_RESPONSE = 0b1011
DOUBAO_WS_ERROR_INFORMATION = 0b1111
DOUBAO_WS_FLAG_WITH_EVENT = 0b0100
DOUBAO_WS_NO_SERIALIZATION = 0b0000
DOUBAO_WS_JSON = 0b0001

DOUBAO_EVENT_START_CONNECTION = 1
DOUBAO_EVENT_FINISH_CONNECTION = 2
DOUBAO_EVENT_CONNECTION_STARTED = 50
DOUBAO_EVENT_CONNECTION_FAILED = 51
DOUBAO_EVENT_START_SESSION = 100
DOUBAO_EVENT_FINISH_SESSION = 102
DOUBAO_EVENT_SESSION_STARTED = 150
DOUBAO_EVENT_SESSION_FINISHED = 152
DOUBAO_EVENT_SESSION_FAILED = 153
DOUBAO_EVENT_TASK_REQUEST = 200
DOUBAO_EVENT_TTS_SENTENCE_START = 350
DOUBAO_EVENT_TTS_SENTENCE_END = 351
DOUBAO_EVENT_TTS_RESPONSE = 352


@dataclass(frozen=True)
class SpeechTranscription:
    text: str


class SpeechTranscriber(Protocol):
    async def transcribe_chunk(
        self,
        *,
        actor_user_id: UUID,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> SpeechTranscription: ...


class VoiceProvider(Protocol):
    provider_name: str

    def ensure_available(self) -> None: ...

    async def transcribe_chunk(
        self,
        *,
        actor_user_id: UUID,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> SpeechTranscription: ...

    def synthesize_pcm_stream(self, *, actor_user_id: UUID, text: str) -> AsyncIterator[bytes]: ...

    def realtime_session_events(self, *, actor_user_id: UUID) -> AsyncIterator[dict[str, object]]: ...

    async def run_realtime_session(self, *, actor_user_id: UUID, client: RealtimeVoiceClient) -> None: ...


class RealtimeVoiceClient(Protocol):
    async def send_json(self, payload: dict[str, object]) -> None: ...

    async def send_bytes(self, payload: bytes) -> None: ...

    async def receive_text(self) -> str: ...


class DisabledVoiceProvider:
    provider_name = "disabled"

    def ensure_available(self) -> None:
        raise ApiError(
            code="voice_provider_disabled",
            message="Voice provider is not configured.",
            status=503,
        )

    async def transcribe_chunk(
        self,
        *,
        actor_user_id: UUID,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> SpeechTranscription:
        self.ensure_available()
        raise AssertionError("unreachable")

    def synthesize_pcm_stream(self, *, actor_user_id: UUID, text: str) -> AsyncIterator[bytes]:
        self.ensure_available()
        raise AssertionError("unreachable")

    async def realtime_session_events(self, *, actor_user_id: UUID) -> AsyncIterator[dict[str, object]]:
        self.ensure_available()
        if False:
            yield {}

    async def run_realtime_session(self, *, actor_user_id: UUID, client: RealtimeVoiceClient) -> None:
        self.ensure_available()


class LocalStubVoiceProvider:
    provider_name = "local_stub"

    def ensure_available(self) -> None:
        return None

    async def transcribe_chunk(
        self,
        *,
        actor_user_id: UUID,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> SpeechTranscription:
        return SpeechTranscription(text="")

    def synthesize_pcm_stream(self, *, actor_user_id: UUID, text: str) -> AsyncIterator[bytes]:
        return _empty_pcm_stream()

    async def realtime_session_events(self, *, actor_user_id: UUID) -> AsyncIterator[dict[str, object]]:
        session_id = f"voice_session_{uuid4().hex}"
        yield {"type": "session.open", "session_id": session_id, "sequence": 0}
        yield {"type": "audio.done", "session_id": session_id, "sequence": 1}

    async def run_realtime_session(self, *, actor_user_id: UUID, client: RealtimeVoiceClient) -> None:
        session_id = f"voice_session_{uuid4().hex}"
        await client.send_json(
            {
                "type": "ready",
                "session_id": session_id,
                "sequence": 0,
                "audio_format": "pcm16",
                "sample_rate": 24000,
                "channels": 1,
            }
        )
        await client.send_json({"type": "done", "session_id": session_id, "sequence": 1})


class OpenAiSpeechTranscriber:
    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.openai_api_key.strip()
        self.model = settings.voice_transcribe_model.strip() or OPENAI_STT_DEFAULT_MODEL

    async def transcribe_chunk(
        self,
        *,
        actor_user_id: UUID,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> SpeechTranscription:
        if not self.api_key:
            raise ApiError(
                code="openai_stt_config_missing",
                message="OpenAI speech transcription is not configured.",
                status=400,
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ApiError(
                code="openai_sdk_missing",
                message="OpenAI SDK is not installed.",
                status=501,
            ) from exc

        kwargs: dict[str, Any] = {
            "model": self.model,
            "file": (filename or "speech.wav", BytesIO(body), content_type or "audio/wav"),
            "response_format": "json",
        }
        if language:
            kwargs["language"] = language
        try:
            async with AsyncOpenAI(api_key=self.api_key) as client:
                transcription = await client.audio.transcriptions.create(**kwargs)
        except Exception as exc:
            raise ApiError(
                code="openai_stt_failed",
                message="OpenAI speech transcription failed.",
                status=502,
            ) from exc

        text = transcription.get("text") if isinstance(transcription, dict) else getattr(transcription, "text", None)
        return SpeechTranscription(text=str(text or "").strip())


@dataclass(frozen=True)
class DoubaoRealtimeTtsSettings:
    api_key: str
    app_id: str
    access_key: str
    ws_url: str
    resource_id: str
    voice_type: str
    audio_format: str
    sample_rate: int
    speed_ratio: float
    speech_rate: int
    first_chunk_timeout_seconds: int
    response_timeout_seconds: int


class DoubaoRealtimeVoiceProvider:
    provider_name = "doubao"

    def __init__(self, settings: Settings, *, connect: Any | None = None, transcriber: SpeechTranscriber | None = None) -> None:
        self.settings = _doubao_settings(settings)
        self._connect = connect
        self._transcriber = transcriber or OpenAiSpeechTranscriber(settings)

    def ensure_available(self) -> None:
        if not self.settings.api_key and not (self.settings.app_id and self.settings.access_key):
            raise ApiError(
                code="doubao_tts_config_missing",
                message="Doubao realtime TTS is not configured.",
                status=503,
            )

    async def transcribe_chunk(
        self,
        *,
        actor_user_id: UUID,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> SpeechTranscription:
        return await self._transcriber.transcribe_chunk(
            actor_user_id=actor_user_id,
            body=body,
            filename=filename,
            content_type=content_type,
            language=language,
        )

    def synthesize_pcm_stream(self, *, actor_user_id: UUID, text: str) -> AsyncIterator[bytes]:
        self.ensure_available()
        return _prefetched_doubao_realtime_voice_audio(
            text=text[:DOUBAO_TTS_MAX_INPUT_CHARS],
            settings=self.settings,
            user_id=str(actor_user_id),
            connect=self._resolve_connect(),
        )

    async def realtime_session_events(self, *, actor_user_id: UUID) -> AsyncIterator[dict[str, object]]:
        self.ensure_available()
        session_id = f"voice_session_{uuid4().hex}"
        yield {
            "type": "session.open",
            "session_id": session_id,
            "sequence": 0,
            "audio_format": "pcm16",
            "sample_rate": self.settings.sample_rate,
            "channels": 1,
        }
        yield {"type": "audio.done", "session_id": session_id, "sequence": 1}

    async def run_realtime_session(self, *, actor_user_id: UUID, client: RealtimeVoiceClient) -> None:
        self.ensure_available()
        await _run_doubao_realtime_voice_session(
            client=client,
            settings=self.settings,
            user_id=str(actor_user_id),
            connect=self._resolve_connect(),
        )

    def _resolve_connect(self) -> Any:
        if self._connect is not None:
            return self._connect
        try:
            import websockets
        except ImportError as exc:
            raise ApiError(
                code="websockets_missing",
                message="websockets package is not installed.",
                status=501,
            ) from exc
        return websockets.connect


def create_voice_provider(settings: Settings) -> VoiceProvider:
    if settings.voice_provider == "disabled":
        return DisabledVoiceProvider()
    if settings.voice_provider == "local_stub":
        return LocalStubVoiceProvider()
    if settings.voice_provider in {"doubao", "volcengine"}:
        return DoubaoRealtimeVoiceProvider(settings)
    raise ApiError(code="voice_provider_invalid", message="Voice provider is not supported.", status=500)


async def _empty_pcm_stream() -> AsyncIterator[bytes]:
    if False:
        yield b""


async def _prefetched_doubao_realtime_voice_audio(
    *,
    text: str,
    settings: DoubaoRealtimeTtsSettings,
    user_id: str,
    connect: Any,
) -> AsyncGenerator[bytes, None]:
    audio_iter = _iter_doubao_realtime_voice_audio(
        text=text,
        settings=settings,
        user_id=user_id,
        connect=connect,
    )
    try:
        first_chunk = await asyncio.wait_for(
            anext(audio_iter, b""),
            timeout=settings.first_chunk_timeout_seconds,
        )
    except Exception as exc:
        await audio_iter.aclose()
        raise ApiError(
            code="doubao_realtime_voice_failed",
            message=f"Failed to stream Doubao realtime voice: {exc}",
            status=502,
        ) from exc
    if not first_chunk:
        await audio_iter.aclose()
        raise ApiError(
            code="doubao_realtime_voice_empty",
            message="Doubao realtime TTS did not return audio.",
            status=502,
        )
    try:
        yield first_chunk
        async for chunk in audio_iter:
            if chunk:
                yield chunk
    finally:
        await audio_iter.aclose()


def _doubao_settings(settings: Settings) -> DoubaoRealtimeTtsSettings:
    speed_ratio = settings.voice_tts_speed_ratio
    return DoubaoRealtimeTtsSettings(
        api_key=settings.voice_api_key.strip(),
        app_id=settings.voice_app_id.strip(),
        access_key=settings.voice_access_key.strip(),
        ws_url=settings.voice_base_url.strip(),
        resource_id=settings.voice_tts_resource_id.strip(),
        voice_type=settings.voice_tts_voice_type.strip(),
        audio_format=settings.voice_tts_audio_format.strip().lower(),
        sample_rate=settings.voice_tts_sample_rate,
        speed_ratio=speed_ratio,
        speech_rate=int(round((speed_ratio - 1.0) * 100)),
        first_chunk_timeout_seconds=settings.voice_tts_first_chunk_timeout_seconds,
        response_timeout_seconds=settings.voice_request_timeout_seconds,
    )


def _doubao_headers(settings: DoubaoRealtimeTtsSettings) -> dict[str, str]:
    headers = {
        "X-Api-Resource-Id": settings.resource_id,
        "X-Api-Connect-Id": str(uuid4()),
    }
    if settings.api_key:
        headers["X-Api-Key"] = settings.api_key
    else:
        headers["X-Api-App-Key"] = settings.app_id
        headers["X-Api-Access-Key"] = settings.access_key
    return headers


async def _iter_doubao_realtime_voice_audio(
    *,
    text: str,
    settings: DoubaoRealtimeTtsSettings,
    user_id: str,
    connect: Any,
) -> AsyncGenerator[bytes, None]:
    session_id = uuid4().hex
    async with connect(
        settings.ws_url,
        additional_headers=_doubao_headers(settings),
        open_timeout=15,
        max_size=1000000000,
    ) as ws:
        await _doubao_start_connection(ws)
        response = await _doubao_receive_response(ws)
        _doubao_expect_event(response, DOUBAO_EVENT_CONNECTION_STARTED, "start connection")

        await _doubao_start_session(ws, settings=settings, session_id=session_id, user_id=user_id)
        response = await _doubao_receive_response(ws)
        _doubao_expect_event(response, DOUBAO_EVENT_SESSION_STARTED, "start session")

        await _doubao_send_task_request(ws, settings=settings, session_id=session_id, text=text, user_id=user_id)
        await _doubao_finish_session(ws, session_id=session_id)

        async for raw in ws:
            response = _doubao_parse_response(raw)
            event = int(response.get("event") or 0)
            message_type = int(response.get("message_type") or 0)
            if message_type == DOUBAO_WS_ERROR_INFORMATION or event in {
                DOUBAO_EVENT_CONNECTION_FAILED,
                DOUBAO_EVENT_SESSION_FAILED,
            }:
                raise RuntimeError(_format_doubao_realtime_error(response))
            if event == DOUBAO_EVENT_TTS_RESPONSE:
                payload = response.get("payload")
                if isinstance(payload, bytes) and payload:
                    yield payload
                continue
            if event in {DOUBAO_EVENT_TTS_SENTENCE_START, DOUBAO_EVENT_TTS_SENTENCE_END}:
                continue
            if event == DOUBAO_EVENT_SESSION_FINISHED:
                with contextlib.suppress(Exception):
                    await _doubao_finish_connection(ws)
                break


async def _run_doubao_realtime_voice_session(
    *,
    client: RealtimeVoiceClient,
    settings: DoubaoRealtimeTtsSettings,
    user_id: str,
    connect: Any,
) -> None:
    session_id = uuid4().hex
    session_done = asyncio.Event()
    send_lock = asyncio.Lock()
    upstream_error: RuntimeError | None = None

    async with connect(
        settings.ws_url,
        additional_headers=_doubao_headers(settings),
        open_timeout=15,
        max_size=1000000000,
    ) as ws:
        await _doubao_start_connection(ws)
        response = await _doubao_receive_response(ws)
        _doubao_expect_event(response, DOUBAO_EVENT_CONNECTION_STARTED, "start connection")

        await _doubao_start_session(ws, settings=settings, session_id=session_id, user_id=user_id)
        response = await _doubao_receive_response(ws)
        _doubao_expect_event(response, DOUBAO_EVENT_SESSION_STARTED, "start session")

        await client.send_json(
            {
                "type": "ready",
                "session_id": session_id,
                "audio_format": "pcm16",
                "sample_rate": settings.sample_rate,
                "channels": 1,
            }
        )

        async def upstream_reader() -> None:
            nonlocal upstream_error
            async for raw in ws:
                response = _doubao_parse_response(raw)
                event = int(response.get("event") or 0)
                message_type = int(response.get("message_type") or 0)
                if message_type == DOUBAO_WS_ERROR_INFORMATION or event in {
                    DOUBAO_EVENT_CONNECTION_FAILED,
                    DOUBAO_EVENT_SESSION_FAILED,
                }:
                    message = _format_doubao_realtime_error(response)
                    upstream_error = RuntimeError(message)
                    session_done.set()
                    await client.send_json({"type": "error", "code": "doubao_realtime_error", "message": message})
                    return
                if event == DOUBAO_EVENT_TTS_RESPONSE:
                    payload = response.get("payload")
                    if isinstance(payload, bytes) and payload:
                        await client.send_bytes(payload)
                    continue
                if event in {DOUBAO_EVENT_TTS_SENTENCE_START, DOUBAO_EVENT_TTS_SENTENCE_END}:
                    continue
                if event == DOUBAO_EVENT_SESSION_FINISHED:
                    session_done.set()
                    return

        async def client_reader() -> None:
            while True:
                raw = await client.receive_text()
                try:
                    payload = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    await client.send_json({"type": "error", "code": "invalid_request", "message": "message must be valid JSON"})
                    continue
                if not isinstance(payload, dict):
                    await client.send_json({"type": "error", "code": "invalid_request", "message": "message must be a JSON object"})
                    continue

                event_type = str(payload.get("type") or "").strip().lower()
                if event_type == "append":
                    text = str(payload.get("text") or "").strip()
                    if text:
                        async with send_lock:
                            await _doubao_send_task_request(
                                ws,
                                settings=settings,
                                session_id=session_id,
                                text=text[:DOUBAO_TTS_MAX_INPUT_CHARS],
                                user_id=user_id,
                            )
                    continue
                if event_type in {"finish", "cancel"}:
                    async with send_lock:
                        await _doubao_finish_session(ws, session_id=session_id)
                    return
                await client.send_json(
                    {"type": "error", "code": "invalid_request", "message": f"unsupported event type: {event_type}"}
                )

        upstream_task = asyncio.create_task(upstream_reader())
        client_task = asyncio.create_task(client_reader())
        tasks = {upstream_task, client_task}
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
            if upstream_task in done and not session_done.is_set():
                client_task.cancel()
                raise RuntimeError("Doubao realtime TTS session closed")
            if client_task in done:
                await asyncio.wait_for(session_done.wait(), timeout=settings.response_timeout_seconds)
                if upstream_error is not None:
                    raise upstream_error
                await client.send_json({"type": "done", "session_id": session_id})
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(Exception):
                await _doubao_finish_connection(ws)


async def _doubao_start_connection(ws: Any) -> None:
    await _doubao_send_event(ws, DOUBAO_EVENT_START_CONNECTION, payload={}, serial_method=DOUBAO_WS_NO_SERIALIZATION)


async def _doubao_start_session(
    ws: Any,
    *,
    settings: DoubaoRealtimeTtsSettings,
    session_id: str,
    user_id: str,
) -> None:
    await _doubao_send_event(
        ws,
        DOUBAO_EVENT_START_SESSION,
        session_id=session_id,
        payload=_doubao_tts_payload(settings=settings, event=DOUBAO_EVENT_START_SESSION, user_id=user_id),
    )


async def _doubao_send_task_request(
    ws: Any,
    *,
    settings: DoubaoRealtimeTtsSettings,
    session_id: str,
    text: str,
    user_id: str,
) -> None:
    await _doubao_send_event(
        ws,
        DOUBAO_EVENT_TASK_REQUEST,
        session_id=session_id,
        payload=_doubao_tts_payload(
            settings=settings,
            event=DOUBAO_EVENT_TASK_REQUEST,
            text=text,
            user_id=user_id,
        ),
    )


async def _doubao_finish_session(ws: Any, *, session_id: str) -> None:
    await _doubao_send_event(ws, DOUBAO_EVENT_FINISH_SESSION, session_id=session_id, payload={})


async def _doubao_finish_connection(ws: Any) -> None:
    await _doubao_send_event(ws, DOUBAO_EVENT_FINISH_CONNECTION, payload={})


async def _doubao_send_event(
    ws: Any,
    event: int,
    *,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
    serial_method: int | None = None,
) -> None:
    payload_bytes = None
    resolved_serial_method = DOUBAO_WS_NO_SERIALIZATION
    if payload is not None:
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        resolved_serial_method = DOUBAO_WS_JSON if serial_method is None else serial_method
    message = bytearray(
        _doubao_ws_header(
            message_type=DOUBAO_WS_FULL_CLIENT_REQUEST,
            message_type_flags=DOUBAO_WS_FLAG_WITH_EVENT,
            serial_method=resolved_serial_method,
        )
    )
    message.extend(_doubao_ws_optional(event=event, session_id=session_id))
    if payload_bytes is not None:
        message.extend(len(payload_bytes).to_bytes(4, "big", signed=True))
        message.extend(payload_bytes)
    await ws.send(bytes(message))


def _doubao_tts_payload(
    *,
    settings: DoubaoRealtimeTtsSettings,
    event: int,
    text: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    uid = user_id.strip() or "mai-user"
    return {
        "user": {"uid": uid},
        "event": event,
        "namespace": DOUBAO_TTS_NAMESPACE,
        "req_params": {
            "text": text,
            "speaker": settings.voice_type,
            "audio_params": {
                "format": settings.audio_format,
                "sample_rate": settings.sample_rate,
                "speech_rate": settings.speech_rate,
            },
        },
    }


def _doubao_ws_header(
    *,
    message_type: int,
    message_type_flags: int = 0,
    serial_method: int = DOUBAO_WS_NO_SERIALIZATION,
    compression_type: int = 0,
) -> bytes:
    return bytes(
        [
            (0b0001 << 4) | 0b0001,
            (message_type << 4) | message_type_flags,
            (serial_method << 4) | compression_type,
            0,
        ]
    )


def _doubao_ws_optional(*, event: int = 0, session_id: str | None = None) -> bytes:
    option = bytearray()
    if event:
        option.extend(event.to_bytes(4, "big", signed=True))
    if session_id is not None:
        encoded = session_id.encode("utf-8")
        option.extend(len(encoded).to_bytes(4, "big", signed=True))
        option.extend(encoded)
    return bytes(option)


async def _doubao_receive_response(ws: Any) -> dict[str, Any]:
    return _doubao_parse_response(await ws.recv())


def _doubao_parse_response(raw: bytes | str) -> dict[str, Any]:
    if isinstance(raw, str):
        raise RuntimeError(f"Doubao realtime TTS returned text frame: {raw}")
    if len(raw) < 4:
        raise RuntimeError("Doubao realtime TTS returned an invalid frame")

    message_type = (raw[1] >> 4) & 0x0F
    flags = raw[1] & 0x0F
    serial_method = (raw[2] >> 4) & 0x0F
    offset = (raw[0] & 0x0F) * 4
    response: dict[str, Any] = {
        "message_type": message_type,
        "flags": flags,
        "serial_method": serial_method,
        "event": 0,
        "session_id": "",
        "payload": b"",
        "payload_json": "",
        "response_meta_json": "",
        "error_code": 0,
    }

    if message_type in {DOUBAO_WS_FULL_SERVER_RESPONSE, DOUBAO_WS_AUDIO_ONLY_RESPONSE}:
        if flags & DOUBAO_WS_FLAG_WITH_EVENT:
            event = int.from_bytes(raw[offset : offset + 4], "big", signed=True)
            response["event"] = event
            offset += 4
            if event == DOUBAO_EVENT_CONNECTION_STARTED:
                connection_id, offset = _doubao_read_content(raw, offset)
                response["connection_id"] = connection_id
            elif event == DOUBAO_EVENT_CONNECTION_FAILED:
                response["response_meta_json"], offset = _doubao_read_content(raw, offset)
            elif event in {DOUBAO_EVENT_SESSION_STARTED, DOUBAO_EVENT_SESSION_FAILED, DOUBAO_EVENT_SESSION_FINISHED}:
                response["session_id"], offset = _doubao_read_content(raw, offset)
                if offset + 4 <= len(raw):
                    response["response_meta_json"], offset = _doubao_read_content(raw, offset)
            elif event == DOUBAO_EVENT_TTS_RESPONSE:
                response["session_id"], offset = _doubao_read_content(raw, offset)
                response["payload"], offset = _doubao_read_payload(raw, offset)
            elif event in {DOUBAO_EVENT_TTS_SENTENCE_START, DOUBAO_EVENT_TTS_SENTENCE_END}:
                response["session_id"], offset = _doubao_read_content(raw, offset)
                payload, offset = _doubao_read_payload(raw, offset)
                response["payload"] = payload
                response["payload_json"] = _decode_bytes(payload)

        if not response["payload"] and not response["payload_json"] and offset + 4 <= len(raw):
            payload, offset = _doubao_read_payload(raw, offset)
            response["payload"] = payload
            if serial_method == DOUBAO_WS_JSON:
                response["payload_json"] = _decode_bytes(payload)

    elif message_type == DOUBAO_WS_ERROR_INFORMATION:
        response["error_code"] = int.from_bytes(raw[offset : offset + 4], "big", signed=True)
        offset += 4
        payload, offset = _doubao_read_payload(raw, offset)
        response["payload"] = payload
        response["payload_json"] = _decode_bytes(payload)
    else:
        raise RuntimeError(f"Doubao realtime TTS returned unsupported message type: {message_type}")

    return response


def _doubao_read_content(raw: bytes, offset: int) -> tuple[str, int]:
    size = int.from_bytes(raw[offset : offset + 4], "big", signed=True)
    offset += 4
    content = raw[offset : offset + size].decode("utf-8")
    offset += size
    return content, offset


def _doubao_read_payload(raw: bytes, offset: int) -> tuple[bytes, int]:
    size = int.from_bytes(raw[offset : offset + 4], "big", signed=True)
    offset += 4
    payload = raw[offset : offset + size]
    offset += size
    return payload, offset


def _doubao_expect_event(response: dict[str, Any], expected_event: int, operation: str) -> None:
    event = int(response.get("event") or 0)
    if event != expected_event:
        raise RuntimeError(f"Doubao realtime TTS failed to {operation}: {_format_doubao_realtime_error(response)}")


def _format_doubao_realtime_error(response: dict[str, Any]) -> str:
    payload_json = str(response.get("payload_json") or response.get("response_meta_json") or "").strip()
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            return payload_json
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("error") or payload.get("msg") or "").strip()
            code = str(payload.get("code") or payload.get("error_code") or response.get("error_code") or "").strip()
            if message and code:
                return f"{code}: {message}"
            if message:
                return message
    payload = response.get("payload")
    if isinstance(payload, bytes) and payload:
        return _decode_bytes(payload)
    event = response.get("event")
    message_type = response.get("message_type")
    return f"event={event}, message_type={message_type}"


def _decode_bytes(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return repr(payload)
