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


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._total = 0
        self._error_count = 0
        self._routes: dict[tuple[str, str], RouteMetrics] = {}

    def record(self, *, method: str, route: str, status_code: int, duration_ms: float) -> None:
        key = (method.upper(), route)
        with self._lock:
            self._total += 1
            if status_code >= 500:
                self._error_count += 1
            route_metrics = self._routes.setdefault(key, RouteMetrics())
            route_metrics.record(status_code=status_code, duration_ms=duration_ms)

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
            }
