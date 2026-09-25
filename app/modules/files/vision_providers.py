from __future__ import annotations

import asyncio
import base64
import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from ...core.errors import ApiError
from ...core.settings import Settings


VisionPurpose = Literal["general", "schedule"]
ScheduleVisionEventType = Literal["pump", "breastfeed", "custom"]
MAX_SCHEDULE_VISION_TASKS = 32
MAX_SCHEDULE_VISION_EVENT_CHARS = 80
OPENAI_VISION_MAX_OUTPUT_TOKENS = 1200
SUPPORTED_OPENAI_VISION_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


class ScheduleVisionTaskPreview(BaseModel):
    time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    event: str = Field(min_length=1, max_length=MAX_SCHEDULE_VISION_EVENT_CHARS)
    event_type: ScheduleVisionEventType

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ScheduleVisionOutput(BaseModel):
    tasks: list[ScheduleVisionTaskPreview] = Field(max_length=MAX_SCHEDULE_VISION_TASKS)

    model_config = ConfigDict(extra="forbid", frozen=True)


class GeneralVisionOutput(BaseModel):
    summary: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


@dataclass(frozen=True)
class VisionAnalysis:
    provider: str
    summary: str
    bytes_read: int
    schedule_tasks: tuple[ScheduleVisionTaskPreview, ...] = ()


class VisionProvider(Protocol):
    provider_name: str

    def ensure_available(self) -> None: ...

    async def analyze_image(
        self,
        *,
        body: bytes,
        content_type: str,
        original_filename: str,
        purpose: VisionPurpose,
    ) -> VisionAnalysis: ...


class DisabledVisionProvider:
    provider_name = "disabled"

    def ensure_available(self) -> None:
        raise ApiError(code="vision_provider_disabled", message="Vision provider is not configured.", status=503)

    async def analyze_image(
        self,
        *,
        body: bytes,
        content_type: str,
        original_filename: str,
        purpose: VisionPurpose,
    ) -> VisionAnalysis:
        self.ensure_available()
        raise AssertionError("unreachable")


class LocalStubVisionProvider:
    provider_name = "local_stub"

    def ensure_available(self) -> None:
        return None

    async def analyze_image(
        self,
        *,
        body: bytes,
        content_type: str,
        original_filename: str,
        purpose: VisionPurpose,
    ) -> VisionAnalysis:
        if purpose == "schedule":
            return VisionAnalysis(
                provider=self.provider_name,
                summary="",
                bytes_read=len(body),
                schedule_tasks=(
                    ScheduleVisionTaskPreview(time="09:00", event="Pumping", event_type="pump"),
                    ScheduleVisionTaskPreview(time="12:00", event="Nursing", event_type="breastfeed"),
                ),
            )
        return VisionAnalysis(
            provider=self.provider_name,
            summary="Vision provider local stub processed the image.",
            bytes_read=len(body),
        )


OpenAIClientFactory = Callable[..., Any]


class OpenAIVisionProvider:
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        client_factory: OpenAIClientFactory | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory

    def ensure_available(self) -> None:
        if not self.api_key or not self.model:
            raise ApiError(
                code="vision_provider_not_configured",
                message="OpenAI vision provider is not configured.",
                status=503,
            )

    async def analyze_image(
        self,
        *,
        body: bytes,
        content_type: str,
        original_filename: str,
        purpose: VisionPurpose,
    ) -> VisionAnalysis:
        self.ensure_available()
        normalized_content_type = content_type.partition(";")[0].strip().lower()
        if normalized_content_type not in SUPPORTED_OPENAI_VISION_CONTENT_TYPES:
            raise ApiError(
                code="validation_failed",
                message="OpenAI vision supports PNG, JPEG, or WEBP images.",
                status=422,
            )

        output_model: type[ScheduleVisionOutput] | type[GeneralVisionOutput]
        if purpose == "schedule":
            output_model = ScheduleVisionOutput
            instructions = _SCHEDULE_VISION_INSTRUCTIONS
            prompt = "Extract the schedule tasks shown in this image."
        else:
            output_model = GeneralVisionOutput
            instructions = _GENERAL_VISION_INSTRUCTIONS
            prompt = "Briefly describe this image."

        try:
            client_factory = self.client_factory or _openai_client_factory()
            client = client_factory(
                api_key=self.api_key,
                max_retries=0,
                timeout=self.timeout_seconds,
            )
            async with client as openai_client:
                responses = getattr(openai_client, "responses", None)
                parse_response = getattr(responses, "parse", None)
                if not callable(parse_response):
                    raise ApiError(
                        code="vision_provider_not_configured",
                        message="OpenAI Responses structured output client is unavailable.",
                        status=503,
                    )
                response = await asyncio.wait_for(
                    parse_response(
                        model=self.model,
                        instructions=instructions,
                        input=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": prompt},
                                    {
                                        "type": "input_image",
                                        "image_url": _image_data_url(body=body, content_type=normalized_content_type),
                                        "detail": "high",
                                    },
                                ],
                            }
                        ],
                        text_format=output_model,
                        store=False,
                        max_output_tokens=OPENAI_VISION_MAX_OUTPUT_TOKENS,
                    ),
                    timeout=self.timeout_seconds,
                )
        except ApiError:
            raise
        except TimeoutError as exc:
            raise ApiError(
                code="vision_provider_timeout",
                message="Vision provider request timed out.",
                status=504,
            ) from exc
        except Exception as exc:
            mapped = _map_openai_error(exc)
            raise ApiError(
                code=mapped.code,
                message=mapped.message,
                status=mapped.status,
                details=mapped.details,
            ) from exc

        if str(getattr(response, "status", "completed") or "") != "completed":
            raise _invalid_openai_response()
        parsed = getattr(response, "output_parsed", None)
        try:
            output = output_model.model_validate(parsed)
        except Exception as exc:
            raise _invalid_openai_response() from exc

        if isinstance(output, ScheduleVisionOutput):
            return VisionAnalysis(
                provider=self.provider_name,
                summary="",
                bytes_read=len(body),
                schedule_tasks=tuple(output.tasks),
            )
        return VisionAnalysis(
            provider=self.provider_name,
            summary=output.summary,
            bytes_read=len(body),
        )


def create_vision_provider(settings: Settings) -> VisionProvider:
    if settings.vision_provider == "disabled":
        return DisabledVisionProvider()
    if settings.vision_provider == "local_stub":
        return LocalStubVisionProvider()
    if settings.vision_provider == "openai":
        return OpenAIVisionProvider(
            api_key=settings.openai_api_key,
            model=settings.vision_openai_model,
            timeout_seconds=settings.vision_request_timeout_seconds,
        )
    raise ApiError(code="vision_provider_invalid", message="Vision provider is not supported.", status=500)


def _openai_client_factory() -> OpenAIClientFactory:
    try:
        openai_module = importlib.import_module("openai")
    except ImportError as exc:
        raise ApiError(
            code="vision_provider_not_configured",
            message="OpenAI Python SDK is not installed.",
            status=503,
        ) from exc
    async_openai = getattr(openai_module, "AsyncOpenAI", None)
    if async_openai is None:
        raise ApiError(
            code="vision_provider_not_configured",
            message="OpenAI Python SDK AsyncOpenAI is unavailable.",
            status=503,
        )
    return cast(OpenAIClientFactory, async_openai)


def _image_data_url(*, body: bytes, content_type: str) -> str:
    encoded = base64.b64encode(body).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


@dataclass(frozen=True)
class _MappedProviderError:
    code: str
    message: str
    status: int
    details: dict[str, Any]


def _map_openai_error(exc: Exception) -> _MappedProviderError:
    status_code = _provider_status_code(exc)
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return _MappedProviderError(
            code="vision_provider_timeout",
            message="Vision provider request timed out.",
            status=504,
            details=_safe_provider_details(status_code),
        )
    if status_code == 429 or "ratelimit" in name:
        return _MappedProviderError(
            code="vision_provider_rate_limited",
            message="Vision provider rate limit was reached.",
            status=429,
            details=_safe_provider_details(status_code),
        )
    if status_code in {401, 403} or "authentication" in name or "permission" in name:
        return _MappedProviderError(
            code="vision_provider_auth_failed",
            message="Vision provider authentication failed.",
            status=503,
            details=_safe_provider_details(status_code),
        )
    if status_code is not None and status_code >= 500:
        return _MappedProviderError(
            code="vision_provider_unavailable",
            message="Vision provider is unavailable.",
            status=503,
            details=_safe_provider_details(status_code),
        )
    if status_code in {400, 422}:
        return _MappedProviderError(
            code="vision_provider_rejected_request",
            message="Vision provider rejected the request.",
            status=502,
            details=_safe_provider_details(status_code),
        )
    return _MappedProviderError(
        code="vision_provider_failed",
        message="Vision provider request failed.",
        status=502,
        details=_safe_provider_details(status_code),
    )


def _provider_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _safe_provider_details(status_code: int | None) -> dict[str, Any]:
    return {"provider_status_code": status_code} if status_code is not None else {}


def _invalid_openai_response() -> ApiError:
    return ApiError(
        code="vision_provider_invalid_response",
        message="Vision provider returned an invalid structured response.",
        status=502,
    )


_SCHEDULE_VISION_INSTRUCTIONS = """
You extract an editable schedule preview from one user-provided screenshot.

Return only tasks that are explicitly visible and have a clearly readable time.
- time: normalize to 24-hour HH:mm.
- event: a short title copied or faithfully summarized from the screenshot, at most 80 characters.
- event_type: pump for breast-pumping or expressing; breastfeed for direct breastfeeding or feeding; custom for every other task.
- Preserve chronological order. Do not invent missing times or tasks.
- If the image is unrelated, unclear, or contains no valid schedule task, return an empty tasks list.
- This is read-only extraction. Never claim that a plan, task, or record was saved.
""".strip()

_GENERAL_VISION_INSTRUCTIONS = """
Describe the user-provided image briefly and factually in at most 500 characters.
Do not infer private identity, medical diagnosis, or actions that are not visibly supported.
""".strip()
