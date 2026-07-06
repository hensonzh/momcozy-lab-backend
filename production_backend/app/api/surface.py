from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from fastapi import APIRouter


ApiSurface = Literal[
    "public_app_api",
    "runtime_stream_api",
    "admin_ops_api",
    "internal_service_api",
    "infra_probe_api",
    "deprecated_api",
]

API_SURFACE_FIELD = "x-momcozy-api-surface"
API_OWNER_FIELD = "x-momcozy-owner"
API_CLIENT_FIELD = "x-momcozy-client"
API_STABILITY_FIELD = "x-momcozy-stability"
API_NOTES_FIELD = "x-momcozy-notes"


def api_surface(
    surface: ApiSurface,
    *,
    owner: str,
    clients: Sequence[str] = (),
    stability: str = "stable",
    notes: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        API_SURFACE_FIELD: surface,
        API_OWNER_FIELD: owner,
        API_CLIENT_FIELD: list(clients),
        API_STABILITY_FIELD: stability,
    }
    if notes:
        metadata[API_NOTES_FIELD] = notes
    return metadata


class SurfaceAPIRouter(APIRouter):
    def __init__(
        self,
        *args: Any,
        api_surface_metadata: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        self._api_surface_metadata = dict(api_surface_metadata)
        super().__init__(*args, **kwargs)

    def add_api_route(
        self,
        path: str,
        endpoint: Callable[..., Any],
        *,
        openapi_extra: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        metadata = dict(self._api_surface_metadata)
        if openapi_extra:
            metadata.update(openapi_extra)
        return super().add_api_route(path, endpoint, openapi_extra=metadata, **kwargs)
