from __future__ import annotations

from fastapi import Request, Response

from ...api.surface import SurfaceAPIRouter, api_surface
from .jwt import jwt_key_id, public_jwks


router = SurfaceAPIRouter(
    prefix="/.well-known",
    tags=["auth"],
    api_surface_metadata=api_surface(
        "internal_service_api",
        owner="auth",
        clients=["agent-runtime"],
    ),
)


@router.get("/jwks.json")
async def jwks(request: Request, response: Response) -> dict[str, list[dict[str, str]]]:
    settings = request.app.state.settings
    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["ETag"] = f'"{jwt_key_id(settings)}"'
    return public_jwks(settings)
