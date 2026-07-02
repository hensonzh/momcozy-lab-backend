from __future__ import annotations

import hmac
from dataclasses import dataclass

from ...core.errors import ApiError
from ...core.settings import Settings


@dataclass(frozen=True)
class ServiceClient:
    name: str = "internal-service"


def authenticate_service_key(raw_key: str, settings: Settings) -> ServiceClient:
    configured = settings.service_api_key
    if not configured:
        raise ApiError(code="service_auth_not_configured", message="Service authentication is not configured.", status=500)
    if not hmac.compare_digest(raw_key, configured):
        raise ApiError(code="authentication_required", message="Service key is invalid.", status=401)
    return ServiceClient()
