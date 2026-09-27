"""Retirement log evidence must be aggregated without exposing user data."""

import gzip
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from scripts.summarize_retirement_access import summarize_access


ROOT = Path(__file__).resolve().parents[1]
LIVE = {"paths": {
    "/v1/legacy/{id}": {"get": {"responses": {"200": {"description": "ok"}}}},
    "/v1/auth/login": {"post": {"responses": {"200": {"description": "ok"}}}},
    "/v1/health/ready": {"get": {"responses": {"200": {"description": "ok"}}}},
}}
CANDIDATE = {"paths": {
    "/v1/auth/login": LIVE["paths"]["/v1/auth/login"],
    "/v1/health/ready": LIVE["paths"]["/v1/health/ready"],
    "/v2/auth/login": LIVE["paths"]["/v1/auth/login"],
}}
START, END = date(2026, 9, 20), date(2026, 9, 26)


def combined(method: str, path: str, status: int, *, day: int = 21) -> str:
    return f'192.0.2.1 - - [{day:02d}/Sep/2026:04:11:35 +0000] "{method} {path} HTTP/2.0" {status} 1 "-" "Flutter secret agent"\n'


def legacy(method: str, path: str, status: int, *, host: str = "backend.example") -> str:
    return json.dumps({
        "time": "2026-09-22T04:11:35+00:00", "method": method, "uri": path,
        "status": str(status), "host": host, "remote_addr": "192.0.2.1",
        "user_agent": "private device", "request_id": "PRIVATE-ID",
    }) + "\n"


def aggregate(lines: list[str]) -> dict:
    return summarize_access(
        lines, current=LIVE, candidate=CANDIDATE, host="backend.example",
        contract_sha256="a" * 64, start=START, end=END,
    )


def test_combined_logs_count_removed_routes_and_unknown_shared_v1_without_leaking_pii() -> None:
    result = aggregate([
        combined("GET", "/v1/legacy/SECRET?code=SENSITIVE", 200),
        combined("GET", "/v1/legacy/OTHER", 401),
        combined("POST", "/v1/auth/login", 200),
        combined("GET", "/v1/health/ready", 200),
        combined("GET", "/v2/auth/login", 200),
        combined("GET", "/v1/legacy/OLD", 200, day=18),
    ])
    assert result["operation_calls"] == {"GET /v1/legacy/{id}": 2}
    assert result["unattributed_v1_requests"] == 3
    assert result["days_with_requests"] == ["2026-09-21"]
    assert result["status_groups"] == {"2xx": 4, "4xx": 1}
    assert result["malformed_log_lines"] == 0
    report = json.dumps(result)
    for forbidden in ("SECRET", "SENSITIVE", "OTHER", "192.0.2.1", "Flutter", "PRIVATE-ID"):
        assert forbidden not in report


def test_json_logs_reject_host_mismatch_and_malformed_data_without_echoing_lines() -> None:
    result = aggregate([
        legacy("GET", "/v1/legacy/SECRET?token=PRIVATE-ID", 404),
        legacy("GET", "/v1/legacy/SECRET", 200, host="other.example"),
        '{"time":"2026-09-22", "uri":"/v1/SECRET"}',
        "bad raw PRIVATE-ID\n",
    ])
    assert result["operation_calls"] == {"GET /v1/legacy/{id}": 1}
    assert result["unattributed_v1_requests"] == 1
    assert result["host_mismatch_lines"] == 1
    assert result["malformed_log_lines"] == 2
    assert result["days_with_requests"] == ["2026-09-22"]
    assert "PRIVATE-ID" not in json.dumps(result)


def test_cli_reads_gzip_and_emits_only_aggregate(tmp_path: Path) -> None:
    live_path, candidate_path, log_path = (tmp_path / name for name in ("live.json", "candidate.json", "access.log.gz"))
    live_path.write_text(json.dumps(LIVE))
    candidate_path.write_text(json.dumps(CANDIDATE))
    with gzip.open(log_path, "wt") as output:
        output.write(combined("GET", "/v1/legacy/SECRET?token=PRIVATE-ID", 200))
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/summarize_retirement_access.py"),
         "--current", str(live_path), "--candidate", str(candidate_path),
         "--host", "backend.example", "--start", START.isoformat(),
         "--end", END.isoformat(), str(log_path)],
        text=True, capture_output=True, check=True,
    )
    body = json.loads(result.stdout)
    assert body["operation_calls"] == {"GET /v1/legacy/{id}": 1}
    assert body["contract_sha256"] and len(body["contract_sha256"]) == 64
    assert "SECRET" not in result.stdout + result.stderr
    assert "PRIVATE-ID" not in result.stdout + result.stderr
