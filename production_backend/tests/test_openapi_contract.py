import json
from pathlib import Path

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.scripts.export_openapi import build_openapi_schema


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_SNAPSHOT = ROOT / "production_backend" / "docs" / "openapi.generated.json"


def test_openapi_snapshot_matches_current_schema() -> None:
    current = build_openapi_schema()
    snapshot = json.loads(OPENAPI_SNAPSHOT.read_text())

    assert snapshot == current


def test_openapi_contains_core_flutter_handoff_paths() -> None:
    paths = build_openapi_schema()["paths"]

    for path in [
        "/v1/auth/signup",
        "/v1/auth/login",
        "/v1/files/upload",
        "/v1/records/feeding",
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
        "/v1/agent/runs",
        "/v1/agent/runs/{run_id}/stream",
    ]:
        assert path in paths


def test_agent_stream_contract_keeps_tokens_out_of_query_parameters() -> None:
    operation = build_openapi_schema()["paths"]["/v1/agent/runs/{run_id}/stream"]["get"]
    parameters = operation.get("parameters", [])
    query_names = {parameter["name"] for parameter in parameters if parameter.get("in") == "query"}

    assert "token" not in query_names
    assert {"after_sequence", "limit"} <= query_names


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
