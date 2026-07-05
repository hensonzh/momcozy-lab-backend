from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from ...core.errors import ApiError
from ...core.settings import Settings
from .providers import SpeechTranscription, VoiceProvider, create_voice_provider


class VoiceService:
    def __init__(self, *, settings: Settings, provider: VoiceProvider | None = None) -> None:
        self.settings = settings
        self.provider = provider or create_voice_provider(settings)

    async def transcribe_chunk(
        self,
        *,
        actor_user_id: UUID,
        body: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> SpeechTranscription:
        self.provider.ensure_available()
        self._validate_chunk(body=body, filename=filename, content_type=content_type, language=language)
        return await self.provider.transcribe_chunk(
            actor_user_id=actor_user_id,
            body=body,
            filename=filename,
            content_type=content_type,
            language=language,
        )

    def synthesize_pcm_stream(self, *, actor_user_id: UUID, text: str) -> AsyncIterator[bytes]:
        self.provider.ensure_available()
        self._validate_text(text)
        return self.provider.synthesize_pcm_stream(actor_user_id=actor_user_id, text=text)

    async def realtime_session_events(self, *, actor_user_id: UUID) -> AsyncIterator[dict[str, object]]:
        self.provider.ensure_available()
        async for event in self.provider.realtime_session_events(actor_user_id=actor_user_id):
            yield event

    def disabled_frame(self) -> dict[str, object]:
        return {
            "type": "error",
            "code": "voice_provider_disabled",
            "message": "Voice provider is not configured.",
        }

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
