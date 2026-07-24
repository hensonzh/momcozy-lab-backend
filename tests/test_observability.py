import json
import logging

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.core.metrics import RequestMetrics
from app.core.logging import log_agent_runtime_event


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"


def test_request_metrics_record_route_status_and_latency() -> None:
    client = TestClient(create_app(Settings(app_env="test")))

    response = client.get("/v1/health/live")
    metrics = client.get("/v1/health/metrics").json()

    assert response.status_code == 200
    route = next(item for item in metrics["routes"] if item["path"] == "/v1/health/live")
    assert route["method"] == "GET"
    assert route["count"] == 1
    assert route["status_counts"]["200"] == 1
    assert route["latency_ms_avg"] >= 0


def test_metrics_endpoint_can_require_service_key() -> None:
    client = TestClient(
        create_app(
            Settings(
                app_env="test",
                metrics_require_service_key=True,
                service_api_key=SERVICE_KEY,
            )
        )
    )

    missing = client.get("/v1/health/metrics")
    invalid = client.get("/v1/health/metrics", headers={"X-Service-Key": "wrong"})
    valid = client.get("/v1/health/metrics", headers={"X-Service-Key": SERVICE_KEY})

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200
    assert valid.json()["status"] == "ok"


def test_request_log_is_structured_and_uses_request_id(caplog) -> None:
    caplog.set_level(logging.INFO, logger="production_backend.http")
    client = TestClient(create_app(Settings(app_env="test")))

    response = client.get("/v1/health/live", headers={"X-Request-ID": "req_log"})

    assert response.status_code == 200
    payloads = [json.loads(record.getMessage()) for record in caplog.records if record.name == "production_backend.http"]
    assert {
        "event": "http.request",
        "request_id": "req_log",
        "method": "GET",
        "path": "/v1/health/live",
        "route": "/v1/health/live",
        "status_code": 200,
    }.items() <= payloads[-1].items()


def test_agent_runtime_log_is_structured(caplog) -> None:
    caplog.set_level(logging.INFO, logger="production_backend.agent_runtime")

    log_agent_runtime_event("agent.run.executor_turn", run_id="run_1", duration_ms=12.345)

    payloads = [json.loads(record.getMessage()) for record in caplog.records if record.name == "production_backend.agent_runtime"]
    assert payloads[-1] == {
        "duration_ms": 12.345,
        "event": "agent.run.executor_turn",
        "run_id": "run_1",
    }


def test_backend_metrics_record_tool_and_sdk_operations() -> None:
    metrics = RequestMetrics()

    metrics.record_agent_tool(
        tool_name="profile_read",
        outcome="failed",
        error_code="permission_denied",
        duration_ms=3.0,
    )
    metrics.record_agent_sdk(node_name="openai_responses", outcome="failed", error_code="dependency_not_configured", duration_ms=4.0)

    snapshot = metrics.snapshot()

    assert "workers" not in snapshot
    assert snapshot["agent_tools"][0]["error_code_counts"]["permission_denied"] == 1
    assert snapshot["agent_sdk"][0]["error_code_counts"]["dependency_not_configured"] == 1
    assert "agent_safety" not in snapshot
