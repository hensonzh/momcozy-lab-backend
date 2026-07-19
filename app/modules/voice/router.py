from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import Depends, File, Form, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from ...api.dependencies import authenticate_request_user, require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ..auth import CurrentUser
from .schemas import SpeechTranscriptionResponse
from .service import VoiceService


router = SurfaceAPIRouter(
    tags=["voice"],
    api_surface_metadata=api_surface("public_app_api", owner="voice", clients=["flutter"]),
)
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


def get_voice_service(request: Request) -> VoiceService:
    return VoiceService(settings=request.app.state.settings)


@router.post("/speech/transcribe-chunk", response_model=SpeechTranscriptionResponse)
async def transcribe_speech_chunk(
    request: Request,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    current_user: CurrentUser = Depends(require_current_user),
    service: VoiceService = Depends(get_voice_service),
) -> SpeechTranscriptionResponse:
    body = await _read_upload_body(file=file, max_bytes=request.app.state.settings.file_upload_max_bytes)
    result = await service.transcribe_chunk(
        actor_user_id=current_user.user_id,
        body=body,
        filename=file.filename or "",
        content_type=file.content_type or "application/octet-stream",
        language=language.strip() if language and language.strip() else None,
    )
    return SpeechTranscriptionResponse(text=result.text)


@router.get(
    "/realtime-voice-stream",
    openapi_extra=api_surface("runtime_stream_api", owner="voice", clients=["flutter"]),
)
async def realtime_voice_stream(
    request: Request,
    text: str = Query(min_length=1, max_length=4000),
    current_user: CurrentUser = Depends(require_current_user),
    service: VoiceService = Depends(get_voice_service),
) -> StreamingResponse:
    stream = service.synthesize_pcm_stream(actor_user_id=current_user.user_id, text=text)
    stream = await _prefetch_pcm_stream(stream)
    sample_rate = request.app.state.settings.voice_tts_sample_rate
    return StreamingResponse(
        stream,
        media_type=f"audio/pcm; rate={sample_rate}; channels=1",
        headers={
            "X-Mai-Audio-Format": "pcm16",
            "X-Mai-Audio-Sample-Rate": str(sample_rate),
            "X-Mai-Audio-Channels": "1",
        },
    )


@router.websocket("/realtime-voice-session")
async def realtime_voice_session(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    service = VoiceService(settings=settings)
    token = _bearer_token(websocket)
    if not token:
        await websocket.close(code=1008, reason="authentication_required")
        return
    try:
        current_user = await authenticate_request_user(
            token=token,
            settings=settings,
            session_factory=getattr(websocket.app.state, "db_session_factory", None),
        )
    except ApiError:
        await websocket.close(code=1008, reason="authentication_required")
        return

    await websocket.accept()
    try:
        await service.run_realtime_session(actor_user_id=current_user.user_id, client=websocket)
    except ApiError as exc:
        if exc.code == "voice_provider_disabled":
            await websocket.send_json(service.disabled_frame())
        else:
            await websocket.send_json({"type": "error", "code": exc.code, "message": exc.message})
    except WebSocketDisconnect:
        return
    finally:
        await websocket.close()


async def _read_upload_body(*, file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ApiError(
                code="payload_too_large",
                message="Uploaded audio chunk is too large.",
                status=413,
                details={"max_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _prefetch_pcm_stream(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    try:
        first_chunk = await anext(stream)
    except StopAsyncIteration:
        return _empty_pcm_stream()
    except ApiError:
        await _close_async_iterator(stream)
        raise
    except Exception as exc:
        await _close_async_iterator(stream)
        raise ApiError(
            code="voice_stream_failed",
            message="Failed to start voice stream.",
            status=502,
        ) from exc

    return _chain_prefetched_pcm_stream(first_chunk=first_chunk, stream=stream)


async def _chain_prefetched_pcm_stream(*, first_chunk: bytes, stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    try:
        if first_chunk:
            yield first_chunk
        async for chunk in stream:
            if chunk:
                yield chunk
    finally:
        await _close_async_iterator(stream)


async def _empty_pcm_stream() -> AsyncIterator[bytes]:
    if False:
        yield b""


async def _close_async_iterator(stream: AsyncIterator[bytes]) -> None:
    aclose = getattr(stream, "aclose", None)
    if callable(aclose):
        with contextlib.suppress(Exception):
            await aclose()


def _bearer_token(websocket: WebSocket) -> str:
    authorization = websocket.headers.get("authorization", "")
    scheme, _separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()
