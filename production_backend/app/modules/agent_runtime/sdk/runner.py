from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any, Protocol

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
    def __init__(self, *, backend: SdkRunnerBackend | None = None) -> None:
        self.backend = backend

    async def run_reasoning(self, request: SdkNodeRequest) -> SdkNodeResult:
        if self.backend is not None:
            return await self.backend.run(request)
        if importlib.util.find_spec("agents") is None:
            raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK is not installed.", status=503)
        raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK backend is not configured.", status=503)
