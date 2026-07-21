from __future__ import annotations

from app.core.errors import ApiError


SDK_ONLY_RUNTIME_PATTERN = "sdk_only"
DEFAULT_RUNTIME_VERSION = "momcozy-agent-v1"


def validate_runtime(*, version: str, pattern: str) -> None:
    if version != DEFAULT_RUNTIME_VERSION:
        raise ApiError(code="not_found", message="Agent runtime version not found.", status=404)
    if pattern != SDK_ONLY_RUNTIME_PATTERN:
        raise ApiError(
            code="runtime_pattern_mismatch",
            message="Run runtime pattern does not match its runtime version.",
            status=409,
        )
