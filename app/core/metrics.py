from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class RouteMetrics:
    count: int = 0
    error_count: int = 0
    latency_ms_total: float = 0.0
    latency_ms_max: float = 0.0
    status_counts: dict[int, int] = field(default_factory=dict)

    def record(self, *, status_code: int, duration_ms: float) -> None:
        self.count += 1
        if status_code >= 500:
            self.error_count += 1
        self.latency_ms_total += duration_ms
        self.latency_ms_max = max(self.latency_ms_max, duration_ms)
        self.status_counts[status_code] = self.status_counts.get(status_code, 0) + 1

    def to_body(self) -> dict[str, Any]:
        average = self.latency_ms_total / self.count if self.count else 0.0
        return {
            "count": self.count,
            "error_count": self.error_count,
            "status_counts": {str(status): count for status, count in sorted(self.status_counts.items())},
            "latency_ms_avg": round(average, 3),
            "latency_ms_max": round(self.latency_ms_max, 3),
        }


@dataclass
class OperationMetrics:
    count: int = 0
    error_count: int = 0
    latency_ms_total: float = 0.0
    latency_ms_max: float = 0.0
    outcome_counts: dict[str, int] = field(default_factory=dict)
    error_code_counts: dict[str, int] = field(default_factory=dict)

    def record(self, *, outcome: str, error_code: str, duration_ms: float) -> None:
        self.count += 1
        if error_code:
            self.error_count += 1
            self.error_code_counts[error_code] = self.error_code_counts.get(error_code, 0) + 1
        self.latency_ms_total += duration_ms
        self.latency_ms_max = max(self.latency_ms_max, duration_ms)
        self.outcome_counts[outcome] = self.outcome_counts.get(outcome, 0) + 1

    def to_body(self) -> dict[str, Any]:
        average = self.latency_ms_total / self.count if self.count else 0.0
        return {
            "count": self.count,
            "error_count": self.error_count,
            "outcome_counts": dict(sorted(self.outcome_counts.items())),
            "error_code_counts": dict(sorted(self.error_code_counts.items())),
            "latency_ms_avg": round(average, 3),
            "latency_ms_max": round(self.latency_ms_max, 3),
        }


@dataclass
class SafetyMetrics:
    count: int = 0
    decision_counts: dict[str, int] = field(default_factory=dict)
    severity_counts: dict[str, int] = field(default_factory=dict)

    def record(self, *, decision: str, severity: str) -> None:
        self.count += 1
        self.decision_counts[decision] = self.decision_counts.get(decision, 0) + 1
        self.severity_counts[severity] = self.severity_counts.get(severity, 0) + 1

    def to_body(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "decision_counts": dict(sorted(self.decision_counts.items())),
            "severity_counts": dict(sorted(self.severity_counts.items())),
        }


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._total = 0
        self._error_count = 0
        self._routes: dict[tuple[str, str], RouteMetrics] = {}
        self._worker_jobs: dict[str, OperationMetrics] = {}
        self._agent_tools: dict[str, OperationMetrics] = {}
        self._agent_sdk: dict[str, OperationMetrics] = {}
        self._agent_safety: dict[str, SafetyMetrics] = {}

    def record(self, *, method: str, route: str, status_code: int, duration_ms: float) -> None:
        key = (method.upper(), route)
        with self._lock:
            self._total += 1
            if status_code >= 500:
                self._error_count += 1
            route_metrics = self._routes.setdefault(key, RouteMetrics())
            route_metrics.record(status_code=status_code, duration_ms=duration_ms)

    def record_worker_job(self, *, job_type: str, outcome: str, error_code: str = "", duration_ms: float = 0.0) -> None:
        self._record_operation(
            bucket=self._worker_jobs,
            key=job_type,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
        )

    def record_agent_tool(self, *, tool_name: str, outcome: str, error_code: str = "", duration_ms: float = 0.0) -> None:
        self._record_operation(
            bucket=self._agent_tools,
            key=tool_name,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
        )

    def record_agent_sdk(self, *, node_name: str, outcome: str, error_code: str = "", duration_ms: float = 0.0) -> None:
        self._record_operation(
            bucket=self._agent_sdk,
            key=node_name,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
        )

    def record_agent_safety(self, *, category: str, decision: str, severity: str) -> None:
        with self._lock:
            safety_metrics = self._agent_safety.setdefault(category, SafetyMetrics())
            safety_metrics.record(decision=decision, severity=severity)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = [
                {"method": method, "path": path, **metrics.to_body()}
                for (method, path), metrics in sorted(self._routes.items())
            ]
            return {
                "status": "ok",
                "requests": {
                    "total": self._total,
                    "error_count": self._error_count,
                },
                "routes": routes,
                "workers": _operation_snapshot(self._worker_jobs, key_name="job_type"),
                "agent_tools": _operation_snapshot(self._agent_tools, key_name="tool_name"),
                "agent_sdk": _operation_snapshot(self._agent_sdk, key_name="node_name"),
                "agent_safety": _safety_snapshot(self._agent_safety),
            }

    def _record_operation(
        self,
        *,
        bucket: dict[str, OperationMetrics],
        key: str,
        outcome: str,
        error_code: str,
        duration_ms: float,
    ) -> None:
        with self._lock:
            operation_metrics = bucket.setdefault(key, OperationMetrics())
            operation_metrics.record(outcome=outcome, error_code=error_code, duration_ms=duration_ms)


def _operation_snapshot(bucket: dict[str, OperationMetrics], *, key_name: str) -> list[dict[str, Any]]:
    return [{key_name: key, **metrics.to_body()} for key, metrics in sorted(bucket.items())]


def _safety_snapshot(bucket: dict[str, SafetyMetrics]) -> list[dict[str, Any]]:
    return [{"category": category, **metrics.to_body()} for category, metrics in sorted(bucket.items())]
