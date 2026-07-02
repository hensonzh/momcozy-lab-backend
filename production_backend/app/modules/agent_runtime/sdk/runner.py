from __future__ import annotations

import importlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

from ....core.metrics import RequestMetrics
from ....core.errors import ApiError


SdkToolInvoker = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class SdkToolDefinition:
    contract_name: str
    sdk_name: str
    description: str
    params_json_schema: dict[str, Any]
    invoke_json: SdkToolInvoker


@dataclass(frozen=True)
class SdkNodeRequest:
    run_id: str
    thread_id: str
    actor_user_id: str
    instructions: str
    model_input: list[dict[str, Any]]
    tool_names: tuple[str, ...] = ()
    tools: tuple[SdkToolDefinition, ...] = ()
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


class OpenAIAgentsSdkBackend:
    def __init__(self, *, model: str) -> None:
        self.model = model

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        try:
            agents_module = importlib.import_module("agents")
        except ImportError as exc:
            raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK is not installed.", status=503) from exc

        agent_cls = getattr(agents_module, "Agent", None)
        runner_cls = getattr(agents_module, "Runner", None)
        if agent_cls is None or runner_cls is None:
            raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK Agent/Runner is unavailable.", status=503)

        agent = agent_cls(
            name="MomCozy assistant",
            instructions=request.instructions,
            model=self.model,
            tools=tuple(_build_function_tool(agents_module=agents_module, definition=definition) for definition in request.tools),
        )
        result = await runner_cls.run(agent, _flatten_model_input(request.model_input))
        final_output = getattr(result, "final_output", "")
        return SdkNodeResult(final_text=str(final_output or ""))


class OpenAIAgentsSdkRunner:
    def __init__(self, *, backend: SdkRunnerBackend | None = None, metrics: RequestMetrics | None = None, model: str = "gpt-5.5") -> None:
        self.backend = backend
        self.metrics = metrics
        self.model = model

    async def run_reasoning(self, request: SdkNodeRequest) -> SdkNodeResult:
        started_at = perf_counter()
        try:
            backend = self.backend or OpenAIAgentsSdkBackend(model=self.model)
            result = await backend.run(request)
            self._record(outcome="completed", error_code="", started_at=started_at)
            return result
        except ApiError as exc:
            self._record(outcome="failed", error_code=exc.code, started_at=started_at)
            raise
        except Exception as exc:
            self._record(outcome="failed", error_code="sdk_run_failed", started_at=started_at)
            raise ApiError(code="sdk_run_failed", message="OpenAI Agents SDK run failed.", status=502) from exc

    def _record(self, *, outcome: str, error_code: str, started_at: float) -> None:
        if self.metrics is not None:
            self.metrics.record_agent_sdk(
                node_name="openai_agents_sdk",
                outcome=outcome,
                error_code=error_code,
                duration_ms=(perf_counter() - started_at) * 1000,
            )


def _flatten_model_input(model_input: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in model_input:
        role = str(item.get("role") or "user")
        content = item.get("content")
        lines.append(f"{role}: {_stringify_content(content)}")
    return "\n".join(lines)


def _stringify_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict | list):
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return str(content)


def sdk_tool_name(contract_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", contract_name).strip("_")
    return normalized or "tool"


def _build_function_tool(*, agents_module: Any, definition: SdkToolDefinition) -> Any:
    function_tool_cls = getattr(agents_module, "FunctionTool", None)
    if function_tool_cls is None:
        raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK FunctionTool is unavailable.", status=503)

    async def invoke_tool(_ctx: Any, args: str) -> str:
        try:
            return await definition.invoke_json(args)
        except ApiError as exc:
            return json.dumps(
                {"error": {"code": exc.code, "message": "Tool call was rejected by application policy."}},
                sort_keys=True,
            )

    return function_tool_cls(
        name=definition.sdk_name,
        description=definition.description,
        params_json_schema=definition.params_json_schema,
        on_invoke_tool=invoke_tool,
    )
