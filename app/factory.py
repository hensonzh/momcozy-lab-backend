from __future__ import annotations

from fastapi import FastAPI

from .agents.cozymate.context.client import sanitize_cozymate_client_context
from .agents.cozymate.replay import project_cozymate_workflow_replay_state
from .agents.cozymate.actions import cozymate_action_policy
from .agents.cozymate.actions.support_form import create_agent_form_ticket
from .api.error_handlers import install_error_handlers
from .api.v1.router import router as v1_router
from .core.cors import install_cors_middleware
from .core.lifespan import build_lifespan
from .core.logging import configure_logging
from .core.metrics import RequestMetrics
from .core.middleware import install_http_middleware
from .core.settings import Settings, get_settings
from .core.trusted_hosts import install_trusted_host_middleware


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.validate_for_startup()
    configure_logging(resolved_settings)
    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        lifespan=build_lifespan(resolved_settings),
    )
    app.state.settings = resolved_settings
    app.state.request_metrics = RequestMetrics()
    app.state.agent_client_context_sanitizer = sanitize_cozymate_client_context
    app.state.agent_workflow_replay_projector = project_cozymate_workflow_replay_state
    app.state.agent_action_policy = cozymate_action_policy()
    app.state.support_agent_form_submitter = create_agent_form_ticket
    install_trusted_host_middleware(app, resolved_settings)
    install_cors_middleware(app, resolved_settings)
    install_http_middleware(app)
    install_error_handlers(app)
    app.include_router(v1_router)
    return app
