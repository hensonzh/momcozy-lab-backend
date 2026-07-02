from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

from ....core.metrics import RequestMetrics
from ....core.errors import ApiError


@dataclass(frozen=True)
class SdkNodeRequest:
    run_id: str
    thread_id: str
    actor_user_id: str
    instructions: str
    model_input: list[dict[str, Any]]
    tool_names: tuple[str, ...] = ()
    trace_id: str = ""


@dataclass(frozen=True)
class SdkNodeResult:
    final_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    action_proposals: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    safety_decision: dict[str, Any] | None = None


class SdkRunnerBackend(Protocol):
    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        ...


class OpenAIAgentsSdkRunner:
    def __init__(self, *, backend: SdkRunnerBackend | None = None, metrics: RequestMetrics | None = None) -> None:
        self.backend = backend
        self.metrics = metrics

    async def run_reasoning(self, request: SdkNodeRequest) -> SdkNodeResult:
        started_at = perf_counter()
        try:
            if self.backend is not None:
                result = await self.backend.run(request)
                self._record(outcome="completed", error_code="", started_at=started_at)
                return result
            if importlib.util.find_spec("agents") is None:
                raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK is not installed.", status=503)
            raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK backend is not configured.", status=503)
        except ApiError as exc:
            self._record(outcome="failed", error_code=exc.code, started_at=started_at)
            raise

    def _record(self, *, outcome: str, error_code: str, started_at: float) -> None:
        if self.metrics is not None:
            self.metrics.record_agent_sdk(
                node_name="openai_agents_sdk",
                outcome=outcome,
                error_code=error_code,
                duration_ms=(perf_counter() - started_at) * 1000,
            )
