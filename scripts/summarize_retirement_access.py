#!/usr/bin/env python3
"""Summarize Nginx access logs for an API retirement without emitting raw requests.

Inputs contain IPs, opaque URL segments and user agents. Keep raw logs on the
approved host when possible; this program emits only route templates/counts.
Days with requests are NOT proof that the log pipeline covered empty days.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

if not __package__:  # executed as python scripts/summarize_retirement_access.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_api_retirement_readiness import _breaking_operations

_COMBINED = re.compile(
    r'\[(?P<date>[^]]+)\] "(?P<method>[A-Z]+) (?P<url>\S+) HTTP/[^\"]+" (?P<status>[0-9]{3})(?:\s|$)'
)
_PLACEHOLDER = re.compile(r"\\\{[^/]+\\\}")


def _parse(line: str) -> tuple[date, str, str, int, str | None] | None:
    if line.lstrip().startswith("{"):
        try:
            record = json.loads(line)
            timestamp = datetime.fromisoformat(record["time"])
            method, url, status, host = record["method"], record["uri"], int(record["status"]), record["host"]
            if not isinstance(method, str) or not isinstance(url, str) or not isinstance(host, str):
                return None
        except (ValueError, TypeError, KeyError):
            return None
    else:
        match = _COMBINED.search(line)
        if match is None:
            return None
        try:
            timestamp = datetime.strptime(match["date"], "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            return None
        method, url, status, host = match["method"], match["url"], int(match["status"]), None
    if timestamp.tzinfo is None or not (100 <= status <= 599) or not url.startswith("/"):
        return None
    return timestamp.astimezone(timezone.utc).date(), method.upper(), url.split("?", 1)[0], status, host


def _route_patterns(operations: set[tuple[str, str]]) -> list[tuple[str, str, re.Pattern[str]]]:
    result = []
    for method, template in sorted(operations, key=lambda item: (item[1].count("{"), -len(item[1]), item)):
        pattern = _PLACEHOLDER.sub("[^/]+", re.escape(template))
        result.append((method, template, re.compile(f"^{pattern}$")))
    return result


def summarize_access(
    lines: Iterable[str],
    *,
    current: dict[str, Any],
    candidate: dict[str, Any],
    host: str,
    contract_sha256: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Report all affected operation calls and unknown /v1 traffic, including errors."""
    operations = _breaking_operations(current, candidate)
    patterns = _route_patterns(operations)
    counts = {f"{method} {path}": 0 for method, path in sorted(operations)}
    days: set[str] = set()
    status_groups: Counter[str] = Counter()
    malformed = mismatched_host = v1_requests = parsed = 0
    stream_sha256 = hashlib.sha256()
    for line in lines:
        stream_sha256.update(line.encode("utf-8", errors="replace"))
        result = _parse(line)
        if result is None:
            malformed += 1
            continue
        day, method, path, status, actual_host = result
        if actual_host is not None and actual_host.split(":", 1)[0] != host:
            mismatched_host += 1
            continue
        if day < start or day > end:
            continue
        parsed += 1
        days.add(day.isoformat())
        status_groups[f"{status // 100}xx"] += 1
        if path.startswith("/v1/") and not path.startswith("/v1/health/"):
            # Existing edge formats contain no trustworthy App build identifier.
            # Shared /v1 routes may still serve old installs even when *removed*
            # endpoints are quiet. Fail closed until all traffic is attributed.
            v1_requests += 1
        for expected_method, template, pattern in patterns:
            if method == expected_method and pattern.fullmatch(path):
                counts[f"{method} {template}"] += 1
                break
    return {
        "scope_host": host,
        "contract_sha256": contract_sha256,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "days_with_requests": sorted(days),
        "operation_calls": counts,
        "unattributed_v1_requests": v1_requests,
        "parsed_lines_in_window": parsed,
        "malformed_log_lines": malformed,
        "host_mismatch_lines": mismatched_host,
        "status_groups": dict(sorted(status_groups.items())),
        "log_stream_sha256": stream_sha256.hexdigest(),
    }


def _lines(paths: list[str]) -> Iterator[str]:
    for name in paths:
        if name == "-":
            yield from sys.stdin
            continue
        opener = gzip.open if name.endswith(".gz") else open
        with opener(name, "rt", encoding="utf-8", errors="replace") as stream:
            yield from stream


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="first complete UTC day")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="last complete UTC day")
    parser.add_argument("logs", nargs="+", help="access log files (.gz supported), or '-' for stdin")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must not be after --end")
    current_bytes = args.current.read_bytes()
    summary = summarize_access(
        _lines(args.logs),
        current=json.loads(current_bytes), candidate=json.loads(args.candidate.read_text()),
        host=args.host, contract_sha256=hashlib.sha256(current_bytes).hexdigest(),
        start=args.start, end=args.end,
    )
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
