import asyncio

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.modules.files.vision_providers import (
    DisabledVisionProvider,
    LocalStubVisionProvider,
    create_vision_provider,
)


def test_vision_provider_factory_returns_stable_disabled_and_local_stub_providers() -> None:
    assert isinstance(create_vision_provider(Settings(app_env="test", vision_provider="disabled")), DisabledVisionProvider)
    assert isinstance(create_vision_provider(Settings(app_env="test", vision_provider="local_stub")), LocalStubVisionProvider)


def test_disabled_vision_provider_fails_before_provider_work() -> None:
    provider = DisabledVisionProvider()

    with pytest.raises(ApiError) as exc_info:
        provider.ensure_available()

    assert exc_info.value.code == "vision_provider_disabled"


def test_local_stub_vision_provider_returns_bounded_analysis() -> None:
    analysis = asyncio.run(
        LocalStubVisionProvider().analyze_image(
            body=b"image-bytes",
            content_type="image/png",
            original_filename="photo.png",
        )
    )

    assert analysis.provider == "local_stub"
    assert analysis.bytes_read == len(b"image-bytes")
    assert analysis.summary
