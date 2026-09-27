#!/usr/bin/env python3
"""Read-only, fail-closed review of evidence for a versioned Product API retirement.

A zero exit status means *eligible for human review*, not permission to deploy.
The release workflow's live compatibility gate remains mandatory; this tool
cannot override it. Do not use self-attested evidence as a production signal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import sys

if not __package__:  # executed as python scripts/check_api_retirement_readiness.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_deployed_openapi_compatibility import incompatibilities

_OPERATION = re.compile(r"^(?:Removed live operation: |)(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (/\S+?)(?::|$)")
_MIN_OBSERVATION_DAYS = 30


def _breaking_operations(current: dict[str, Any], candidate: dict[str, Any]) -> set[tuple[str, str]]:
    affected: set[tuple[str, str]] = set()
    for error in incompatibilities(current, candidate):
        match = _OPERATION.match(error)
        if not match:
            raise ValueError(f"Unrecognized compatibility finding: {error}")
        affected.add((match.group(1), match.group(2)))
    return affected


def _nonnegative_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _calendar_day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def assess_retirement(
    current: dict[str, Any],
    candidate: dict[str, Any],
    evidence: dict[str, Any] | None,
    *,
    contract_sha256: str,
    as_of: date,
    host: str = "",
) -> list[str]:
    """Find blockers; assessment alone NEVER authorizes a release or route removal."""
    try:
        affected = _breaking_operations(current, candidate)
    except ValueError as exc:
        return [str(exc)]
    if not affected:
        return []

    reasons: list[str] = []
    paths = candidate.get("paths", {})
    versioned_paths = [path for path in paths if path.startswith("/v2/")] if isinstance(paths, dict) else []
    if not versioned_paths:
        reasons.append("candidate has no /v2/ operations; versioned clients have no independent API")
    for method, path in sorted(affected):
        if not path.startswith("/v1/"):
            reasons.append(f"{method} {path}: breaking a non-v1 route needs a separate migration plan")
            continue
        new_methods = paths.get(path, {}) if isinstance(paths, dict) else {}
        if isinstance(new_methods, dict) and method.lower() in new_methods:
            # The changed behavior belongs in v2. v1 may remain only if its
            # published contract still accepts old requests and responses.
            v2_path = "/v2/" + path[len("/v1/"):]
            replacement = paths.get(v2_path, {}) if isinstance(paths, dict) else {}
            if not isinstance(replacement, dict) or method.lower() not in replacement:
                reasons.append(f"{method} {path}: changed v1 contract needs {method} {v2_path}")
            else:
                reasons.append(f"{method} {path}: existing /v1 operation must remain compatible; move the breaking change to {v2_path}")

    if not isinstance(evidence, dict):
        return reasons + ["missing evidence for breaking changes"]
    if evidence.get("contract_sha256") != contract_sha256 or not re.fullmatch(r"[a-f0-9]{64}", contract_sha256):
        reasons.append("evidence contract SHA-256 does not match the live OpenAPI input")
    if host and evidence.get("scope_host") != host:
        reasons.append("evidence scope_host does not match the live API host")

    start = _calendar_day(evidence.get("window_start"))
    end = _calendar_day(evidence.get("window_end"))
    if start is None or end is None or end < start or (end - start).days + 1 < _MIN_OBSERVATION_DAYS:
        reasons.append("observation window must contain at least 30 completed UTC days")
    elif end != as_of - timedelta(days=1):
        reasons.append("observation window is stale or includes a partial/future day; end yesterday UTC")
    days = evidence.get("observed_days")
    if not isinstance(days, list) or not all(_calendar_day(day) for day in days) or len(set(map(str, days))) != len(days):
        reasons.append("observed_days must be a unique list of complete UTC dates")
    elif start is not None and end is not None and end >= start:
        expected = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
        if set(days) != expected:
            reasons.append("missing daily coverage or dates outside the observation window")

    summary = evidence.get("access_summary")
    if not isinstance(summary, dict):
        reasons.append("access_summary from the read-only log collector is required")
        summary = {}
    if summary.get("contract_sha256") != contract_sha256 or summary.get("scope_host") != evidence.get("scope_host"):
        reasons.append("access_summary contract SHA-256 or scope_host does not match the reviewed live API")
    if summary.get("window_start") != evidence.get("window_start") or summary.get("window_end") != evidence.get("window_end"):
        reasons.append("access_summary window differs from the independently reviewed coverage window")
    days_with_requests = summary.get("days_with_requests")
    if not isinstance(days_with_requests, list) or not all(_calendar_day(day) for day in days_with_requests):
        reasons.append("access_summary days_with_requests must be a list of UTC dates")
    elif isinstance(days, list) and not set(days_with_requests).issubset(set(map(str, days))):
        reasons.append("access_summary days with requests are missing from the coverage record")
    digest = summary.get("log_stream_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        reasons.append("access_summary log_stream_sha256 is missing or invalid")
    for key in ("unattributed_v1_requests", "malformed_log_lines", "host_mismatch_lines"):
        count = summary.get(key)
        if not _nonnegative_count(count):
            reasons.append(f"access_summary {key} must be a measured nonnegative integer")
        elif count:
            reasons.append(f"access_summary has {count} {key.replace('_', ' ')}")
    if not _nonnegative_count(summary.get("parsed_lines_in_window")):
        reasons.append("access_summary parsed_lines_in_window must be a measured nonnegative integer")
    calls = summary.get("operation_calls")
    if not isinstance(calls, dict):
        reasons.append("operation_calls must cover every breaking operation")
    else:
        for method, path in sorted(affected):
            operation = f"{method} {path}"
            count = calls.get(operation)
            if not _nonnegative_count(count):
                reasons.append(f"operation_calls missing or invalid for {operation}")
            elif count:
                reasons.append(f"{operation}: {count} request(s) in observation window")

    for key in ("unexpired_refresh_tokens", "unmigrated_service_consumers"):
        count = evidence.get(key)
        if not _nonnegative_count(count):
            reasons.append(f"{key} must be a measured nonnegative integer")
        elif count:
            reasons.append(f"{count} {key.replace('_', ' ')} remain")
    if evidence.get("client_inventory_complete") is not True:
        reasons.append("released client inventory is incomplete or unverified")
    clients = evidence.get("released_clients")
    if not isinstance(clients, list) or not clients:
        reasons.append("released_clients must include every shipped build and service client")
    else:
        seen: set[str] = set()
        for client in clients:
            if not isinstance(client, dict) or not isinstance(client.get("build"), str) or not client["build"]:
                reasons.append("invalid released client entry")
                continue
            build = client["build"]
            if build in seen:
                reasons.append(f"duplicate released client: {build}")
            seen.add(build)
            if not isinstance(client.get("state"), str) or client["state"] not in {"migrated", "retired"}:
                reasons.append(f"{build}: client is not confirmed migrated or retired")
    if evidence.get("legacy_distribution_disabled") is not True:
        reasons.append("legacy distribution must be disabled to prevent new old-client installations")
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True, help="TLS-verified live OpenAPI from the host being retired")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True, help="Reviewed, non-sensitive evidence summary")
    parser.add_argument("--host", required=True, help="Hostname of the live API; prevents cross-host evidence reuse")
    args = parser.parse_args()
    current_bytes = args.current.read_bytes()
    current = json.loads(current_bytes)
    candidate = json.loads(args.candidate.read_text())
    evidence = json.loads(args.evidence.read_text())
    if not isinstance(current, dict) or not isinstance(candidate, dict):
        parser.error("OpenAPI inputs must be JSON objects")
    reasons = assess_retirement(
        current, candidate, evidence,
        contract_sha256=hashlib.sha256(current_bytes).hexdigest(),
        as_of=datetime.now(timezone.utc).date(),
        host=args.host,
    )
    if reasons:
        for reason in reasons:
            print(f"RETIREMENT BLOCKED: {reason}")
        return 1
    print("Eligible for independent human review ONLY. The deployment compatibility gate has not been bypassed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
