import json
from pathlib import Path

from app.core.settings import Settings
from app.factory import create_app
from app.api.surface import API_CLIENT_FIELD, API_OWNER_FIELD, API_STABILITY_FIELD, API_SURFACE_FIELD
from scripts.export_api_surface_catalog import render_api_surface_catalog
from scripts.export_openapi import build_openapi_schema


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_SNAPSHOT = ROOT / "docs" / "openapi.generated.json"
API_SURFACE_CATALOG = ROOT / "docs" / "api-surface-catalog.md"
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
VALID_API_SURFACES = {
    "public_app_api",
    "runtime_stream_api",
    "admin_ops_api",
    "internal_service_api",
    "infra_probe_api",
    "deprecated_api",
}


def test_openapi_snapshot_matches_current_schema() -> None:
    current = build_openapi_schema()
    snapshot = json.loads(OPENAPI_SNAPSHOT.read_text())

    assert snapshot == current


def test_api_surface_catalog_matches_current_schema() -> None:
    assert API_SURFACE_CATALOG.read_text() == render_api_surface_catalog(build_openapi_schema())


def test_openapi_operations_declare_api_surface_metadata() -> None:
    missing = []
    invalid = []
    for method, path, operation in _iter_operations(build_openapi_schema()):
        surface = operation.get(API_SURFACE_FIELD)
        owner = operation.get(API_OWNER_FIELD)
        clients = operation.get(API_CLIENT_FIELD)
        stability = operation.get(API_STABILITY_FIELD)
        if not all([surface, owner, isinstance(clients, list), stability]):
            missing.append(f"{method.upper()} {path}")
        elif surface not in VALID_API_SURFACES:
            invalid.append(f"{method.upper()} {path}: {surface}")

    assert missing == []
    assert invalid == []


def test_public_product_internal_and_infra_surfaces_are_separate() -> None:
    schema = build_openapi_schema()

    assert schema["paths"]["/v1/profile/me"]["get"][API_SURFACE_FIELD] == "public_app_api"
    assert schema["paths"]["/v1/internal/agent/profile"]["get"][API_SURFACE_FIELD] == "internal_service_api"
    assert schema["paths"]["/v1/health/ready"]["get"][API_SURFACE_FIELD] == "infra_probe_api"


def test_jwks_is_an_unauthenticated_agent_runtime_auth_contract() -> None:
    operation = build_openapi_schema()["paths"]["/.well-known/jwks.json"]["get"]

    assert operation[API_SURFACE_FIELD] == "internal_service_api"
    assert operation[API_OWNER_FIELD] == "auth"
    assert operation[API_CLIENT_FIELD] == ["agent-runtime"]
    assert "security" not in operation


def test_openapi_contains_core_flutter_handoff_paths() -> None:
    paths = build_openapi_schema()["paths"]

    for path in [
        "/v1/auth/signup",
        "/v1/auth/login",
        "/v1/auth/invite-login",
        "/v1/assets",
        "/v1/assets/{asset_id}",
        "/v1/files/upload",
        "/v1/files/{file_id}/vision/events/stream",
        "/v1/profile/me",
        "/v1/profile/infants",
        "/v1/records/feeding",
        "/v1/records/milk-trends",
        "/v1/plans",
        "/v1/devices/pump-telemetry",
        "/v1/devices/pump-workstate",
        "/v1/devices/pump-workstate/latest",
        "/v1/devices/pump-threshold",
        "/v1/devices/pump-threshold/latest",
        "/v1/devices/pump-health",
        "/v1/devices/pump-health/latest",
        "/v1/devices/pump-energy-target",
        "/v1/speech/transcribe-chunk",
        "/v1/realtime-voice-stream",
    ]:
        assert path in paths


def test_openapi_contains_runtime_to_product_internal_contracts_only() -> None:
    paths = build_openapi_schema()["paths"]
    expected = {
        "/v1/internal/agent/actions/lactation.record/apply",
        "/v1/internal/agent/actions/notifications.milk_reminder/apply",
        "/v1/internal/agent/actions/plans/apply",
        "/v1/internal/agent/actions/diary.entry/apply",
        "/v1/internal/agent/actions/profile.update/apply",
        "/v1/internal/agent/actions/support.ticket/apply",
        "/v1/internal/agent/diary",
        "/v1/internal/agent/files/resolve",
        "/v1/internal/agent/lactation/milk-analysis-snapshot",
        "/v1/internal/agent/plans/calendar",
        "/v1/internal/agent/plans/current",
        "/v1/internal/agent/plans/{plan_id}",
        "/v1/internal/agent/profile",
        "/v1/internal/agent/schedule-timeline",
    }

    assert expected <= set(paths)
    assert not any(path.startswith("/v1/agent/") or path == "/v1/agent" for path in paths)


def test_openapi_excludes_retired_legacy_pump_fallback_paths() -> None:
    paths = build_openapi_schema()["paths"]

    for retired_path in [
        "/images/Air_img/{asset_path}",
        "/skill-assets/{skill_id}/{asset_path}",
        "/v1/pump/workstate",
        "/v1/pump/workstate/pending-replies",
        "/v1/pump/process",
        "/v1/pump/process/data",
        "/v1/pump/session-summary",
        "/v1/pump/threshold/upload",
        "/v1/pump/threshold/get",
        "/v1/pump/energy/get",
        "/v1/pump/health/upload",
        "/v1/pump/health/get",
        "/v1/pump/info/get",
        "/v1/profile/status-summary",
        "/v1/status-page/today",
    ]:
        assert retired_path not in paths


def test_file_vision_stream_contract_keeps_tokens_out_of_query_parameters() -> None:
    operation = build_openapi_schema()["paths"]["/v1/files/{file_id}/vision/events/stream"]["get"]
    parameters = operation.get("parameters", [])
    query_names = {parameter["name"] for parameter in parameters if parameter.get("in") == "query"}
    purpose = next(parameter for parameter in parameters if parameter.get("in") == "query" and parameter["name"] == "purpose")

    assert "token" not in query_names
    assert purpose["schema"]["enum"] == ["general", "schedule"]
    assert purpose["schema"]["default"] == "general"


def test_retryable_writes_declare_idempotency_header() -> None:
    schema = create_app(Settings(app_env="test")).openapi()

    for method, path in [
        ("post", "/v1/files/upload"),
        ("delete", "/v1/files/{file_id}"),
        ("post", "/v1/profile/infants"),
        ("post", "/v1/records/feeding"),
        ("post", "/v1/plans"),
        ("post", "/v1/devices/pump-telemetry"),
        ("post", "/v1/devices/pump-workstate"),
        ("post", "/v1/devices/pump-threshold"),
        ("post", "/v1/devices/pump-health"),
        ("post", "/v1/support/tickets"),
        ("post", "/v1/internal/agent/actions/lactation.record/apply"),
        ("post", "/v1/internal/agent/actions/notifications.milk_reminder/apply"),
        ("post", "/v1/internal/agent/actions/plans/apply"),
        ("post", "/v1/internal/agent/actions/diary.entry/apply"),
        ("post", "/v1/internal/agent/actions/profile.update/apply"),
        ("post", "/v1/internal/agent/actions/support.ticket/apply"),
    ]:
        operation = schema["paths"][path][method]
        header_names = {parameter["name"] for parameter in operation.get("parameters", []) if parameter.get("in") == "header"}
        assert "Idempotency-Key" in header_names


def _iter_operations(schema: dict):
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield method, path, operation
