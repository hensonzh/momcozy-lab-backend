from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...core.errors import ApiError
from ...core.settings import Settings


@dataclass(frozen=True)
class VisionAnalysis:
    provider: str
    summary: str
    bytes_read: int


class VisionProvider(Protocol):
    provider_name: str

    def ensure_available(self) -> None: ...

    async def analyze_image(self, *, body: bytes, content_type: str, original_filename: str) -> VisionAnalysis: ...


class DisabledVisionProvider:
    provider_name = "disabled"

    def ensure_available(self) -> None:
        raise ApiError(code="vision_provider_disabled", message="Vision provider is not configured.", status=503)

    async def analyze_image(self, *, body: bytes, content_type: str, original_filename: str) -> VisionAnalysis:
        self.ensure_available()
        raise AssertionError("unreachable")


class LocalStubVisionProvider:
    provider_name = "local_stub"

    def ensure_available(self) -> None:
        return None

    async def analyze_image(self, *, body: bytes, content_type: str, original_filename: str) -> VisionAnalysis:
        return VisionAnalysis(
            provider=self.provider_name,
            summary="Vision provider local stub processed the image.",
            bytes_read=len(body),
        )


def create_vision_provider(settings: Settings) -> VisionProvider:
    if settings.vision_provider == "disabled":
        return DisabledVisionProvider()
    if settings.vision_provider == "local_stub":
        return LocalStubVisionProvider()
    raise ApiError(code="vision_provider_invalid", message="Vision provider is not supported.", status=500)
