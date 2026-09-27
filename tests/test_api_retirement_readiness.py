"""A breaking API removal needs independently verifiable client-exit evidence."""

from copy import deepcopy
from datetime import date, timedelta

from scripts.check_api_retirement_readiness import assess_retirement
from scripts.summarize_retirement_access import summarize_access


TODAY = date(2026, 9, 27)
OLD = {
    "paths": {
        "/v1/legacy": {"get": {"responses": {"200": {"description": "ok"}}}},
        "/v1/health/ready": {"get": {"responses": {"200": {"description": "ok"}}}},
    }
}
NEW = {
    "paths": {
        "/v1/health/ready": OLD["paths"]["/v1/health/ready"],
        "/v2/legacy": OLD["paths"]["/v1/legacy"],
    }
}


def evidence() -> dict:
    return {
        "contract_sha256": "a" * 64,
        "scope_host": "backend.example",
        "window_start": "2026-08-27",
        "window_end": "2026-09-26",
        "observed_days": [
            (TODAY - timedelta(days=offset)).isoformat() for offset in range(1, 32)
        ],
        "access_summary": {
            "scope_host": "backend.example",
            "contract_sha256": "a" * 64,
            "window_start": "2026-08-27",
            "window_end": "2026-09-26",
            "days_with_requests": [],
            "operation_calls": {"GET /v1/legacy": 0},
            "unattributed_v1_requests": 0,
            "malformed_log_lines": 0,
            "host_mismatch_lines": 0,
            "parsed_lines_in_window": 0,
            "log_stream_sha256": "b" * 64,
        },
        "unexpired_refresh_tokens": 0,
        "unmigrated_service_consumers": 0,
        "client_inventory_complete": True,
        "released_clients": [{"build": "android-59", "state": "migrated"}],
        "legacy_distribution_disabled": True,
    }


def assess(payload: dict | None, *, candidate: dict = NEW) -> list[str]:
    return assess_retirement(
        OLD, candidate, payload, contract_sha256="a" * 64, as_of=TODAY, host="backend.example"
    )


def test_valid_complete_evidence_is_only_eligible_for_manual_review() -> None:
    assert assess(evidence()) == []


def test_missing_evidence_fails_closed() -> None:
    assert any("missing evidence" in reason for reason in assess(None))


def test_recent_calls_or_active_sessions_block_even_when_route_calls_are_zero() -> None:
    item = evidence()
    item["access_summary"]["operation_calls"]["GET /v1/legacy"] = 1
    item["unexpired_refresh_tokens"] = 3
    item["unmigrated_service_consumers"] = 1
    reasons = assess(item)
    assert any("1 request" in reason for reason in reasons)
    assert any("3 unexpired" in reason for reason in reasons)
    assert any("1 unmigrated" in reason for reason in reasons)


def test_audit_period_gaps_or_stale_evidence_block() -> None:
    item = evidence()
    item["observed_days"].remove("2026-09-10")
    item["window_end"] = "2026-09-25"
    reasons = assess(item)
    assert any("missing daily coverage" in reason for reason in reasons)
    assert any("stale" in reason for reason in reasons)


def test_incomplete_route_or_client_inventory_blocks() -> None:
    item = evidence()
    item["access_summary"]["operation_calls"] = {}
    item["client_inventory_complete"] = False
    item["released_clients"] = [{"build": "android-59", "state": "active"}]
    item["legacy_distribution_disabled"] = False
    reasons = assess(item)
    assert any("GET /v1/legacy" in reason for reason in reasons)
    assert any("incomplete" in reason for reason in reasons)
    assert any("android-59" in reason for reason in reasons)
    assert any("distribution" in reason for reason in reasons)


def test_retired_operation_does_not_require_an_equivalent_v2_feature() -> None:
    candidate = {"paths": {
        "/v1/health/ready": OLD["paths"]["/v1/health/ready"],
        "/v2/health/ready": OLD["paths"]["/v1/health/ready"],
    }}
    assert assess(evidence(), candidate=candidate) == []


def test_changed_v1_operation_requires_the_matching_v2_operation() -> None:
    current = deepcopy(OLD)
    current["paths"]["/v1/legacy"]["get"]["requestBody"] = {
        "content": {"application/json": {"schema": {
            "type": "object", "properties": {"code": {"type": "string"}}
        }}}
    }
    candidate = deepcopy(current)
    candidate["paths"]["/v1/legacy"]["get"]["requestBody"]["content"]["application/json"]["schema"]["required"] = ["code"]
    candidate["paths"]["/v2/health/ready"] = current["paths"]["/v1/health/ready"]
    reasons = assess_retirement(
        current, candidate, evidence(), contract_sha256="a" * 64, as_of=TODAY,
    )
    assert any("GET /v2/legacy" in reason for reason in reasons)
    candidate["paths"]["/v2/legacy"] = current["paths"]["/v1/legacy"]
    reasons = assess_retirement(
        current, candidate, evidence(), contract_sha256="a" * 64, as_of=TODAY,
    )
    assert any("must remain compatible" in reason for reason in reasons)


def test_contract_identity_and_versioned_replacement_are_required() -> None:
    item = evidence()
    item["contract_sha256"] = "b" * 64
    reasons = assess(item, candidate={"paths": {"/v1/health/ready": OLD["paths"]["/v1/health/ready"]}})
    assert any("contract SHA-256" in reason for reason in reasons)
    assert any("/v2/" in reason for reason in reasons)


def test_shared_v1_activity_from_real_parser_blocks_even_when_removed_route_is_quiet() -> None:
    item = evidence()
    # The removed /v1/legacy route has zero calls, but an old install can still
    # use shared auth/profile paths; neither edge format carries its build ID.
    line = ('192.0.2.1 - - [26/Sep/2026:04:11:35 +0000] '
            '"GET /v1/profile/me?private=SECRET HTTP/2.0" 200 1 "-" "Flutter"\n')
    item["access_summary"] = summarize_access(
        [line], current=OLD, candidate=NEW, host="backend.example",
        contract_sha256="a" * 64,
        start=date(2026, 8, 27), end=date(2026, 9, 26),
    )
    reasons = assess(item)
    assert item["access_summary"]["operation_calls"] == {"GET /v1/legacy": 0}
    assert any("unattributed" in reason for reason in reasons)


def test_absent_or_unattributed_log_summary_blocks() -> None:
    item = evidence()
    item["access_summary"]["unattributed_v1_requests"] = 2
    item["access_summary"]["malformed_log_lines"] = 1
    item["access_summary"]["host_mismatch_lines"] = 1
    reasons = assess(item)
    assert any("unattributed" in reason for reason in reasons)
    assert any("malformed" in reason for reason in reasons)
    assert any("host mismatch" in reason for reason in reasons)

    item.pop("access_summary")
    assert any("access_summary" in reason for reason in assess(item))


def test_log_summary_contract_and_coverage_must_match_reviewed_evidence() -> None:
    item = evidence()
    item["access_summary"]["contract_sha256"] = "c" * 64
    item["access_summary"]["window_end"] = "2026-09-25"
    item["access_summary"]["days_with_requests"] = ["2026-08-26"]
    reasons = assess(item)
    assert any("access_summary contract" in reason for reason in reasons)
    assert any("access_summary window" in reason for reason in reasons)
    assert any("access_summary days" in reason for reason in reasons)


def test_invalid_or_fabricated_types_fail_closed() -> None:
    item = evidence()
    item["unexpired_refresh_tokens"] = False  # bool is not a measured count
    item["access_summary"]["operation_calls"]["GET /v1/legacy"] = -1
    item["observed_days"] = "2026-09-27"
    item["released_clients"] = [{"build": "android-59", "state": {"untrusted": "value"}}]
    reasons = assess(item)
    assert any("unexpired_refresh_tokens" in reason for reason in reasons)
    assert any("operation_calls" in reason for reason in reasons)
    assert any("observed_days" in reason for reason in reasons)
    assert any("android-59" in reason for reason in reasons)


def test_nonbreaking_candidate_needs_no_retirement_evidence() -> None:
    assert assess_retirement(OLD, OLD, None, contract_sha256="a" * 64, as_of=TODAY) == []
