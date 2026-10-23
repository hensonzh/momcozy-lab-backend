"""Only one authenticated stateless analysis stream is exposed."""
import json
from pathlib import Path

from scripts.export_stool_specialty_openapi import build_schema


def test_independent_stream_snapshot_and_bearer_scope_are_stable():
    root = Path(__file__).resolve().parents[1]
    path = root / "docs/contracts/stool-specialty.openapi.v1.json"
    schema = build_schema()
    assert schema == json.loads(path.read_text())
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {"type": "http", "scheme": "bearer"}
    assert set(schema["paths"]) == {"/healthz", "/v1/analyze/stream"}
    assert schema["paths"]["/v1/analyze/stream"]["post"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/healthz"]["get"]
    command = schema["components"]["schemas"]["AnalysisInput"]
    assert command["additionalProperties"] is False
    assert command["properties"]["photos"]["maxItems"] == 3
    snapshot = path.read_text()
    for forbidden in ("object_key", "owner_user_id", "stool.field.ready", "stool.analysis.completed", "/v1/jobs"):
        assert forbidden not in snapshot
