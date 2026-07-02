import json
from pathlib import Path


SMOKE_FLOWS = Path(__file__).resolve().parents[1] / "docs" / "flutter-smoke-flows.json"


def test_flutter_smoke_flows_cover_auth_core_and_agent() -> None:
    payload = json.loads(SMOKE_FLOWS.read_text())
    flow_names = {flow["name"] for flow in payload["flows"]}

    assert {"auth_session", "core_records_plans_files", "agent_replay"} <= flow_names


def test_flutter_smoke_flows_do_not_put_tokens_in_urls() -> None:
    payload = json.loads(SMOKE_FLOWS.read_text())

    assert payload["rules"]["no_tokens_in_urls"] is True
    for flow in payload["flows"]:
        for step in flow["steps"]:
            assert "token" + "=" not in step["path"]


def test_flutter_smoke_flows_use_idempotency_for_retryable_writes() -> None:
    payload = json.loads(SMOKE_FLOWS.read_text())
    retryable_paths = {
        "/v1/files/upload",
        "/v1/records/feeding",
        "/v1/plans",
        "/v1/agent/runs",
    }

    for flow in payload["flows"]:
        for step in flow["steps"]:
            if step["method"] in {"POST", "PUT", "PATCH", "DELETE"} and step["path"] in retryable_paths:
                assert "${idempotency_header}" in step.get("headers", [])
