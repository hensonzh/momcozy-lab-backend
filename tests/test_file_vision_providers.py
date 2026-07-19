import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.core.settings import Settings
from app.modules.files.vision_providers import (
    DisabledVisionProvider,
    LocalStubVisionProvider,
    MAX_SCHEDULE_VISION_TASKS,
    OpenAIVisionProvider,
    ScheduleVisionOutput,
    ScheduleVisionTaskPreview,
    create_vision_provider,
)


class ProviderError(Exception):
    def __init__(self, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"provider status {status_code}")


class APITimeoutError(Exception):
    pass


def test_vision_provider_factory_returns_stable_disabled_and_local_stub_providers() -> None:
    assert isinstance(create_vision_provider(Settings(app_env="test", vision_provider="disabled")), DisabledVisionProvider)
    assert isinstance(create_vision_provider(Settings(app_env="test", vision_provider="local_stub")), LocalStubVisionProvider)
    assert isinstance(
        create_vision_provider(
            Settings(
                app_env="test",
                vision_provider="openai",
                openai_api_key="test-key",
                vision_openai_model="gpt-test",
            )
        ),
        OpenAIVisionProvider,
    )


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
            purpose="general",
        )
    )

    assert analysis.provider == "local_stub"
    assert analysis.bytes_read == len(b"image-bytes")
    assert analysis.summary
    assert analysis.schedule_tasks == ()


def test_local_stub_schedule_analysis_is_deterministic_and_bounded() -> None:
    provider = LocalStubVisionProvider()

    first = asyncio.run(
        provider.analyze_image(
            body=b"first-image",
            content_type="image/png",
            original_filename="first.png",
            purpose="schedule",
        )
    )
    second = asyncio.run(
        provider.analyze_image(
            body=b"different-image",
            content_type="image/jpeg",
            original_filename="different.jpg",
            purpose="schedule",
        )
    )

    assert first.schedule_tasks == second.schedule_tasks
    assert 0 < len(first.schedule_tasks) <= MAX_SCHEDULE_VISION_TASKS
    assert [task.model_dump() for task in first.schedule_tasks] == [
        {"time": "09:00", "event": "吸奶", "event_type": "pump"},
        {"time": "12:00", "event": "亲喂", "event_type": "breastfeed"},
    ]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"time": "9:00", "event": "吸奶", "event_type": "pump"}, "time"),
        ({"time": "0９:0５", "event": "吸奶", "event_type": "pump"}, "time"),
        ({"time": "09:00", "event": "", "event_type": "pump"}, "event"),
        ({"time": "09:00", "event": "吸奶", "event_type": "feed"}, "event_type"),
    ],
)
def test_schedule_vision_task_contract_rejects_unbounded_or_unknown_fields(payload: dict, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        ScheduleVisionTaskPreview.model_validate(payload)

    with pytest.raises(ValidationError, match="extra"):
        ScheduleVisionTaskPreview.model_validate({**payload, "extra": "not allowed"})


def test_schedule_vision_output_limits_task_count() -> None:
    task = {"time": "09:00", "event": "吸奶", "event_type": "pump"}

    with pytest.raises(ValidationError, match="tasks"):
        ScheduleVisionOutput.model_validate({"tasks": [task] * (MAX_SCHEDULE_VISION_TASKS + 1)})


def test_openai_vision_provider_uses_private_strict_schedule_request() -> None:
    fake_client = FakeOpenAIClient(
        output_parsed=ScheduleVisionOutput(
            tasks=[
                ScheduleVisionTaskPreview(time="08:30", event="吸奶", event_type="pump"),
                ScheduleVisionTaskPreview(time="11:45", event="散步", event_type="custom"),
            ]
        )
    )
    provider = OpenAIVisionProvider(
        api_key="test-key",
        model="gpt-test",
        timeout_seconds=2,
        client_factory=lambda **kwargs: fake_client.capture_client_kwargs(kwargs),
    )

    analysis = asyncio.run(
        provider.analyze_image(
            body=b"image-bytes",
            content_type="image/png",
            original_filename="private-schedule.png",
            purpose="schedule",
        )
    )

    assert [task.event for task in analysis.schedule_tasks] == ["吸奶", "散步"]
    assert fake_client.client_kwargs == {"api_key": "test-key", "max_retries": 0, "timeout": 2}
    assert fake_client.parse_kwargs["model"] == "gpt-test"
    assert fake_client.parse_kwargs["store"] is False
    assert fake_client.parse_kwargs["text_format"] is ScheduleVisionOutput
    assert fake_client.parse_kwargs["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Extract the schedule tasks shown in this image."},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,aW1hZ2UtYnl0ZXM=",
                    "detail": "high",
                },
            ],
        }
    ]
    assert "private-schedule.png" not in repr(fake_client.parse_kwargs)


@pytest.mark.parametrize(
    ("error", "code", "status"),
    [
        (TimeoutError(), "vision_provider_timeout", 504),
        (APITimeoutError("provider deadline"), "vision_provider_timeout", 504),
        (ProviderError(status_code=429), "vision_provider_rate_limited", 429),
        (ProviderError(status_code=401), "vision_provider_auth_failed", 503),
        (ProviderError(status_code=503), "vision_provider_unavailable", 503),
        (ProviderError(status_code=400), "vision_provider_rejected_request", 502),
        (RuntimeError("provider token sk-secret-value"), "vision_provider_failed", 502),
    ],
)
def test_openai_vision_provider_maps_errors_without_leaking_provider_text(error: Exception, code: str, status: int) -> None:
    fake_client = FakeOpenAIClient(error=error)
    provider = OpenAIVisionProvider(
        api_key="test-key",
        model="gpt-test",
        timeout_seconds=2,
        client_factory=lambda **kwargs: fake_client.capture_client_kwargs(kwargs),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            provider.analyze_image(
                body=b"image-bytes",
                content_type="image/png",
                original_filename="private.png",
                purpose="schedule",
            )
        )

    assert exc_info.value.code == code
    assert exc_info.value.status == status
    assert "sk-secret-value" not in exc_info.value.message
    assert "sk-secret-value" not in repr(exc_info.value.details)


def test_openai_vision_provider_rejects_missing_credentials_and_unsupported_image_before_network() -> None:
    missing_key = OpenAIVisionProvider(api_key="", model="gpt-test", timeout_seconds=2)

    with pytest.raises(ApiError) as exc_info:
        missing_key.ensure_available()

    assert exc_info.value.code == "vision_provider_not_configured"

    configured = OpenAIVisionProvider(api_key="test-key", model="gpt-test", timeout_seconds=2)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            configured.analyze_image(
                body=b"svg",
                content_type="image/svg+xml",
                original_filename="diagram.svg",
                purpose="schedule",
            )
        )

    assert exc_info.value.code == "validation_failed"


class FakeOpenAIClient:
    def __init__(self, *, output_parsed=None, error: Exception | None = None) -> None:
        self.responses = self
        self.output_parsed = output_parsed
        self.error = error
        self.client_kwargs = {}
        self.parse_kwargs = {}

    def capture_client_kwargs(self, kwargs: dict):
        self.client_kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def parse(self, **kwargs):
        self.parse_kwargs = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status="completed", output_parsed=self.output_parsed)
