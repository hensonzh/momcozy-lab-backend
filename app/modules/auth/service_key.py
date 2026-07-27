from __future__ import annotations

import hmac
from dataclasses import dataclass

from ...core.errors import ApiError
from ...core.settings import Settings


@dataclass(frozen=True)
class ServiceClient:
    name: str = "internal-service"


def authenticate_service_key(raw_key: str, settings: Settings) -> ServiceClient:
    return _authenticate_configured_service_key(
        raw_key=raw_key,
        configured_key=settings.service_api_key,
        client_name="internal-service",
        not_configured_message="Service authentication is not configured.",
    )


def authenticate_agent_runtime_service_key(raw_key: str, settings: Settings) -> ServiceClient:
    return _authenticate_configured_service_key(
        raw_key=raw_key,
        configured_key=settings.agent_runtime_service_api_key,
        client_name="agent-runtime",
        not_configured_message="Agent Runtime service authentication is not configured.",
    )


def _authenticate_configured_service_key(
    *,
    raw_key: str,
    configured_key: str,
    client_name: str,
    not_configured_message: str,
) -> ServiceClient:
    if not configured_key:
        raise ApiError(code="service_auth_not_configured", message=not_configured_message, status=500)
    if not hmac.compare_digest(raw_key, configured_key):
        raise ApiError(code="authentication_required", message="Service key is invalid.", status=401)
    return ServiceClient(name=client_name)
