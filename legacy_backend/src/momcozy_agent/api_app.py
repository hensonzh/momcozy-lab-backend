from __future__ import annotations

import os
from pathlib import Path

from .config import load_project_env
from .server import STATIC_CONTENT_TYPES, legacy_air_image_path
from .services.paths import ensure_runtime_dirs

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "skills"


def create_app():
    try:
        from fastapi import FastAPI
        from fastapi.responses import FileResponse, JSONResponse
    except ImportError as exc:
        raise RuntimeError("FastAPI is not installed. Install the 'server' optional dependencies.") from exc

    from .api.chat_ws_bridge import router as chat_ws_router
    from .api.routes import router
    from .api.vision_stream import router as vision_router

    load_project_env()
    ensure_runtime_dirs()
    app = FastAPI(title="Momcozy Agent API")
    app.include_router(router)
    app.include_router(vision_router)
    app.include_router(chat_ws_router)

    @app.api_route("/", methods=["GET", "HEAD"])
    async def service_index():
        return JSONResponse(_service_info())

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health():
        return JSONResponse(_service_info())

    @app.api_route("/skill-assets/{skill_id}/{asset_path:path}", methods=["GET", "HEAD"])
    async def skill_asset(skill_id: str, asset_path: str):
        asset_full = (SKILLS_ROOT / skill_id / "assets" / asset_path).resolve()
        skill_assets_root = (SKILLS_ROOT / skill_id / "assets").resolve()
        if skill_assets_root not in asset_full.parents or not asset_full.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        content_type = STATIC_CONTENT_TYPES.get(asset_full.suffix.lower())
        if content_type is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(asset_full, media_type=content_type)

    @app.api_route("/images/Air_img/{asset_path:path}", methods=["GET", "HEAD"])
    async def legacy_air_image(asset_path: str):
        asset_full = legacy_air_image_path(asset_path)
        if asset_full is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(asset_full, media_type=STATIC_CONTENT_TYPES.get(asset_full.suffix.lower()))

    return app


app = create_app()


def _service_info() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "momcozy-api",
        "web_demo": "removed",
        "endpoints": {
            "ag_ui_ws": "/api/ag-ui-ws",
            "ag_ui_prewarm": "/api/ag-ui-prewarm",
            "ag_ui_cancel": "/api/ag-ui-cancel",
            "client_event": "/api/client-event",
            "support_ticket_submit": "/api/support-ticket-submit",
            "skill_assets": "/skill-assets/{skill_id}/{asset_path}",
        },
    }


def main() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is not installed. Install the 'server' optional dependencies.") from exc

    load_project_env()
    host = os.getenv("ENTRY_HOST", "0.0.0.0")
    port = int(os.getenv("ENTRY_PORT", "8769"))
    uvicorn.run("momcozy_agent.api_app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
