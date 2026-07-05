from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from ...core.errors import ApiError
from ...core.settings import Settings


@dataclass(frozen=True)
class SpeechTranscription:
    text: str


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


def create_voice_provider(settings: Settings) -> VoiceProvider:
    if settings.voice_provider == "disabled":
        return DisabledVoiceProvider()
    if settings.voice_provider == "local_stub":
        return LocalStubVoiceProvider()
    raise ApiError(code="voice_provider_invalid", message="Voice provider is not supported.", status=500)


async def _empty_pcm_stream() -> AsyncIterator[bytes]:
    if False:
        yield b""
