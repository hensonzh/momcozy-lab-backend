from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

from ...core.errors import ApiError
from ...core.settings import Settings


@dataclass(frozen=True)
class SpeechTranscription:
    text: str


class VoiceService:
    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings

    async def transcribe_chunk(
        self,
        *,
        actor_user_id: UUID,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> SpeechTranscription:
        self._ensure_provider_enabled()
        self._validate_chunk(body=body, filename=filename, content_type=content_type, language=language)
        return SpeechTranscription(text="")

    def synthesize_pcm_stream(self, *, actor_user_id: UUID, text: str) -> AsyncIterator[bytes]:
        self._ensure_provider_enabled()
        self._validate_text(text)
        return _empty_pcm_stream()

    async def realtime_session_events(self, *, actor_user_id: UUID) -> AsyncIterator[dict[str, object]]:
        self._ensure_provider_enabled()
        session_id = f"voice_session_{uuid4().hex}"
        yield {"type": "session.open", "session_id": session_id, "sequence": 0}
        yield {"type": "audio.done", "session_id": session_id, "sequence": 1}

    def disabled_frame(self) -> dict[str, object]:
        return {
            "type": "error",
            "code": "voice_provider_disabled",
            "message": "Voice provider is not configured.",
        }

    def _ensure_provider_enabled(self) -> None:
        if self.settings.voice_provider == "disabled":
            raise ApiError(
                code="voice_provider_disabled",
                message="Voice provider is not configured.",
                status=503,
            )

    def _validate_chunk(
        self,
        *,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> None:
        if not body:
            raise ApiError(code="validation_failed", message="Audio chunk is empty.", status=422)
        if len(body) > self.settings.file_upload_max_bytes:
            raise ApiError(
                code="payload_too_large",
                message="Uploaded audio chunk is too large.",
                status=413,
                details={"max_bytes": self.settings.file_upload_max_bytes},
            )
        if len(filename) > 255:
            raise ApiError(code="validation_failed", message="Filename is too long.", status=422)
        if len(content_type) > 255:
            raise ApiError(code="validation_failed", message="Content type is too long.", status=422)
        if language is not None and len(language) > 32:
            raise ApiError(code="validation_failed", message="Language is too long.", status=422)

    def _validate_text(self, text: str) -> None:
        if not text.strip():
            raise ApiError(code="validation_failed", message="Text is required.", status=422)
        if len(text) > 4000:
            raise ApiError(code="validation_failed", message="Text is too long.", status=422)


async def _empty_pcm_stream() -> AsyncIterator[bytes]:
    if False:
        yield b""
