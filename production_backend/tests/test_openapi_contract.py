import json
from pathlib import Path

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.api.surface import API_CLIENT_FIELD, API_OWNER_FIELD, API_STABILITY_FIELD, API_SURFACE_FIELD
from production_backend.scripts.export_api_surface_catalog import render_api_surface_catalog
from production_backend.scripts.export_openapi import build_openapi_schema


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_SNAPSHOT = ROOT / "production_backend" / "docs" / "openapi.generated.json"
API_SURFACE_CATALOG = ROOT / "production_backend" / "docs" / "api-surface-catalog.md"
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


def test_public_app_api_paths_are_separate_from_ops_and_infra_surfaces() -> None:
    schema = build_openapi_schema()

    assert schema["paths"]["/v1/profile/me"]["get"][API_SURFACE_FIELD] == "public_app_api"
    assert schema["paths"]["/v1/agent/runs/{run_id}/stream"]["get"][API_SURFACE_FIELD] == "runtime_stream_api"
    assert schema["paths"]["/v1/agent/admin/runs/{run_id}/replay"]["get"][API_SURFACE_FIELD] == "admin_ops_api"
    assert schema["paths"]["/v1/notifications"]["post"][API_SURFACE_FIELD] == "internal_service_api"
    assert schema["paths"]["/v1/health/ready"]["get"][API_SURFACE_FIELD] == "infra_probe_api"


def test_openapi_contains_core_flutter_handoff_paths() -> None:
    paths = build_openapi_schema()["paths"]

    for path in [
        "/v1/auth/signup",
        "/v1/auth/login",
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
        "/v1/agent/artifacts/{artifact_id}",
        "/v1/agent/memories",
        "/v1/agent/memories/settings",
        "/v1/agent/memories/{memory_id}",
        "/v1/agent/runs",
        "/v1/agent/runs/{run_id}/client-events",
        "/v1/agent/runs/{run_id}/stream",
    ]:
        assert path in paths


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


def test_agent_stream_contract_keeps_tokens_out_of_query_parameters() -> None:
    operation = build_openapi_schema()["paths"]["/v1/agent/runs/{run_id}/stream"]["get"]
    parameters = operation.get("parameters", [])
    query_names = {parameter["name"] for parameter in parameters if parameter.get("in") == "query"}

    assert "token" not in query_names
    assert {"after_sequence", "limit"} <= query_names


def test_file_vision_stream_contract_keeps_tokens_out_of_query_parameters() -> None:
    operation = build_openapi_schema()["paths"]["/v1/files/{file_id}/vision/events/stream"]["get"]
    parameters = operation.get("parameters", [])
    query_names = {parameter["name"] for parameter in parameters if parameter.get("in") == "query"}

    assert "token" not in query_names


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
        ("post", "/v1/notifications"),
        ("post", "/v1/support/tickets"),
        ("post", "/v1/agent/runs"),
        ("post", "/v1/agent/actions/{action_id}/confirm"),
    ]:
        operation = schema["paths"][path][method]
        header_names = {parameter["name"] for parameter in operation.get("parameters", []) if parameter.get("in") == "header"}
        assert "Idempotency-Key" in header_names


def _iter_operations(schema: dict):
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield method, path, operation
