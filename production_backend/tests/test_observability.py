import json
import logging

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app


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
