from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

from ....core.metrics import RequestMetrics
from ....core.errors import ApiError


SdkToolInvoker = Callable[[str], Awaitable[str]]
SdkTextDeltaHandler = Callable[[str], Awaitable[None]]
THINK_TAG = "<think>"


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
    prompt_version: str = ""
    trace_id: str = ""
    service_skill_id: str = "main_agent"
    on_text_delta: SdkTextDeltaHandler | None = None


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
    def __init__(
        self,
        *,
        model: str,
        max_turns: int = 10,
        trace_enabled: bool = False,
        provider: str = "openai",
        api_key: str = "",
        base_url: str = "",
        use_responses: bool | None = None,
        buffer_streamed_tool_calls: bool = False,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.trace_enabled = trace_enabled
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.use_responses = use_responses
        self.buffer_streamed_tool_calls = buffer_streamed_tool_calls

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        try:
            agents_module = importlib.import_module("agents")
        except ImportError as exc:
            raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK is not installed.", status=503) from exc

        agent_cls = getattr(agents_module, "Agent", None)
        runner_cls = getattr(agents_module, "Runner", None)
        if agent_cls is None or runner_cls is None:
            raise ApiError(code="dependency_not_configured", message="OpenAI Agents SDK Agent/Runner is unavailable.", status=503)
        if _is_real_agents_module(agents_module) and not _has_provider_credentials(provider=self.provider, api_key=self.api_key):
            raise ApiError(
                code="dependency_not_configured",
                message=f"OpenAI Agents SDK credentials are not configured for provider '{self.provider}'.",
                status=503,
            )

        agent = agent_cls(
            name="MomCozy assistant",
            instructions=request.instructions,
            model=self.model,
            tools=[_build_function_tool(agents_module=agents_module, definition=definition) for definition in request.tools],
        )
        run_kwargs: dict[str, Any] = {"max_turns": self.max_turns}
        run_config = _build_run_config(
            agents_module=agents_module,
            request=request,
            trace_enabled=self.trace_enabled,
            provider=self.provider,
            api_key=self.api_key,
            base_url=self.base_url,
            use_responses=self.use_responses,
            buffer_streamed_tool_calls=self.buffer_streamed_tool_calls,
        )
        if run_config is not None:
            run_kwargs["run_config"] = run_config
        if request.on_text_delta is not None and hasattr(runner_cls, "run_streamed"):
            result = await _run_streamed(
                runner_cls=runner_cls,
                agent=agent,
                model_input=_flatten_model_input(request.model_input),
                run_kwargs=run_kwargs,
                on_text_delta=request.on_text_delta,
            )
            return result

        result = await runner_cls.run(agent, _flatten_model_input(request.model_input), **run_kwargs)
        final_output = getattr(result, "final_output", "")
        return SdkNodeResult(final_text=_sanitize_model_text(str(final_output or "")))


class OpenAIAgentsSdkRunner:
    def __init__(
        self,
        *,
        backend: SdkRunnerBackend | None = None,
        metrics: RequestMetrics | None = None,
        model: str = "gpt-5.5",
        max_turns: int = 10,
        timeout_seconds: float = 60,
        trace_enabled: bool = False,
        provider: str = "openai",
        api_key: str = "",
        base_url: str = "",
        use_responses: bool | None = None,
        buffer_streamed_tool_calls: bool = False,
    ) -> None:
        self.backend = backend
        self.metrics = metrics
        self.model = model
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self.trace_enabled = trace_enabled
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.use_responses = use_responses
        self.buffer_streamed_tool_calls = buffer_streamed_tool_calls

    async def run_reasoning(self, request: SdkNodeRequest) -> SdkNodeResult:
        started_at = perf_counter()
        try:
            backend = self.backend or OpenAIAgentsSdkBackend(
                model=self.model,
                max_turns=self.max_turns,
                trace_enabled=self.trace_enabled,
                provider=self.provider,
                api_key=self.api_key,
                base_url=self.base_url,
                use_responses=self.use_responses,
                buffer_streamed_tool_calls=self.buffer_streamed_tool_calls,
            )
            result = await asyncio.wait_for(backend.run(request), timeout=self.timeout_seconds)
            self._record(outcome="completed", error_code="", started_at=started_at)
            return result
        except TimeoutError as exc:
            self._record(outcome="failed", error_code="sdk_run_timed_out", started_at=started_at)
            raise ApiError(code="sdk_run_timed_out", message="OpenAI Agents SDK run timed out.", status=504) from exc
        except ApiError as exc:
            self._record(outcome="failed", error_code=exc.code, started_at=started_at)
            raise
        except Exception as exc:
            mapped = _provider_error_mapping(exc)
            self._record(outcome="failed", error_code=mapped.code, started_at=started_at)
            raise ApiError(
                code=mapped.code,
                message=mapped.message,
                status=mapped.status,
                details=_provider_error_details(exc),
            ) from exc

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


async def _run_streamed(
    *,
    runner_cls: Any,
    agent: Any,
    model_input: str,
    run_kwargs: dict[str, Any],
    on_text_delta: SdkTextDeltaHandler,
) -> SdkNodeResult:
    streamed = runner_cls.run_streamed(agent, model_input, **run_kwargs)
    raw_text = ""
    emitted_text = ""
    async for event in streamed.stream_events():
        delta = _text_delta_from_stream_event(event)
        if delta:
            raw_text += delta
            sanitized_text = _sanitize_model_text(raw_text)
            if sanitized_text.startswith(emitted_text):
                sanitized_delta = sanitized_text[len(emitted_text) :]
            else:
                sanitized_delta = sanitized_text
            if sanitized_delta:
                await on_text_delta(sanitized_delta)
                emitted_text = sanitized_text
    final_output = getattr(streamed, "final_output", "")
    return SdkNodeResult(final_text=_sanitize_model_text(str(final_output or "")))


def _text_delta_from_stream_event(event: Any) -> str:
    if str(getattr(event, "type", "") or "") != "raw_response_event":
        return ""
    data = getattr(event, "data", None)
    event_type = str(getattr(data, "type", "") or "")
    if event_type and "delta" not in event_type:
        return ""
    for attr in ("delta", "text", "content"):
        value = getattr(data, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _stringify_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict | list):
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return str(content)


def _sanitize_model_text(text: str) -> str:
    without_closed_blocks = re.sub(r"(?is)<think>.*?</think>\s*", "", text)
    without_open_block = re.sub(r"(?is)<think>.*$", "", without_closed_blocks)
    lower_text = without_open_block.lower()
    max_partial_len = min(len(THINK_TAG) - 1, len(lower_text))
    for size in range(max_partial_len, 0, -1):
        if THINK_TAG.startswith(lower_text[-size:]):
            return without_open_block[:-size].strip()
    return without_open_block.strip()


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
        strict_json_schema=False,
    )


def _build_run_config(
    *,
    agents_module: Any,
    request: SdkNodeRequest,
    trace_enabled: bool,
    provider: str,
    api_key: str,
    base_url: str,
    use_responses: bool | None,
    buffer_streamed_tool_calls: bool,
) -> Any | None:
    run_config_cls = getattr(agents_module, "RunConfig", None)
    if run_config_cls is None:
        return None
    model_provider = _build_model_provider(
        agents_module=agents_module,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        use_responses=use_responses,
        buffer_streamed_tool_calls=buffer_streamed_tool_calls,
    )
    if provider != "openai" and model_provider is None:
        raise ApiError(
            code="sdk_provider_not_supported",
            message=f"OpenAI Agents SDK provider '{provider}' is not supported by the installed SDK.",
            status=503,
        )
    try:
        kwargs: dict[str, Any] = {
            "tracing_disabled": not trace_enabled,
            "trace_id": request.trace_id or None,
            "group_id": request.thread_id or None,
            "workflow_name": "MomCozy agent runtime",
            "trace_metadata": {
                "run_id": request.run_id,
                "thread_id": request.thread_id,
                "actor_user_id": request.actor_user_id,
                "prompt_version": request.prompt_version,
                "service_skill_id": request.service_skill_id,
                "tool_names": list(request.tool_names),
                "model_provider": provider,
            },
        }
        if model_provider is not None:
            kwargs["model_provider"] = model_provider
        return run_config_cls(**kwargs)
    except TypeError as exc:
        if provider != "openai":
            raise ApiError(
                code="sdk_provider_not_supported",
                message=f"OpenAI Agents SDK RunConfig does not support provider '{provider}'.",
                status=503,
            ) from exc
        return None


def _build_model_provider(
    *,
    agents_module: Any,
    provider: str,
    api_key: str,
    base_url: str,
    use_responses: bool | None,
    buffer_streamed_tool_calls: bool,
) -> Any | None:
    openai_provider_cls = getattr(agents_module, "OpenAIProvider", None)
    if openai_provider_cls is None:
        return None
    if provider == "openai":
        return _instantiate_openai_provider(
            openai_provider_cls,
            {
                "api_key": api_key or None,
                "base_url": base_url or None,
                "use_responses": use_responses,
                "buffer_streamed_tool_calls": buffer_streamed_tool_calls,
            },
        )
    if provider == "minimax":
        return _instantiate_openai_provider(
            openai_provider_cls,
            {
                "api_key": api_key or None,
                "base_url": base_url or None,
                "use_responses": False if use_responses is None else use_responses,
                "strict_feature_validation": False,
                "buffer_streamed_tool_calls": True if not buffer_streamed_tool_calls else buffer_streamed_tool_calls,
            },
        )
    return None


def _instantiate_openai_provider(openai_provider_cls: Any, kwargs: dict[str, Any]) -> Any | None:
    try:
        return openai_provider_cls(**kwargs)
    except TypeError:
        compatible_kwargs = {key: value for key, value in kwargs.items() if key in {"api_key", "base_url", "use_responses"}}
        try:
            return openai_provider_cls(**compatible_kwargs)
        except TypeError:
            return None


def _is_real_agents_module(agents_module: Any) -> bool:
    return bool(getattr(agents_module, "__file__", ""))


def _has_provider_credentials(*, provider: str, api_key: str) -> bool:
    if api_key:
        return True
    if provider == "minimax":
        return bool(os.getenv("MINIMAX_API_KEY"))
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_ADMIN_KEY"))


@dataclass(frozen=True)
class _ProviderErrorMapping:
    code: str
    message: str
    status: int


def _provider_error_mapping(exc: Exception) -> _ProviderErrorMapping:
    status_code = _provider_status_code(exc)
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    if status_code == 429 or "rate" in name and "limit" in name or "rate limit" in text:
        return _ProviderErrorMapping(
            code="sdk_rate_limited",
            message="OpenAI Agents SDK provider rate limit was reached.",
            status=429,
        )
    if status_code in {401, 403} or "auth" in name or "permission" in name:
        return _ProviderErrorMapping(
            code="sdk_auth_failed",
            message="OpenAI Agents SDK provider authentication failed.",
            status=503,
        )
    if status_code is not None and status_code >= 500:
        return _ProviderErrorMapping(
            code="sdk_provider_unavailable",
            message="OpenAI Agents SDK provider is unavailable.",
            status=503,
        )
    if status_code in {400, 422}:
        return _ProviderErrorMapping(
            code="sdk_bad_request",
            message="OpenAI Agents SDK provider rejected the request.",
            status=502,
        )
    return _ProviderErrorMapping(
        code="sdk_run_failed",
        message="OpenAI Agents SDK run failed.",
        status=502,
    )


def _provider_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _provider_error_details(exc: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {
        "exception_type": exc.__class__.__name__,
    }
    status_code = _provider_status_code(exc)
    if status_code is not None:
        details["provider_status_code"] = status_code
    provider_error = getattr(exc, "body", None) or getattr(exc, "error", None)
    if provider_error is not None:
        details["provider_error_type"] = provider_error.__class__.__name__
    message = _redact_exception_text(str(exc))
    if message:
        details["message_excerpt"] = message[:500]
    return details


def _redact_exception_text(text: str) -> str:
    redacted = re.sub(r"(?i)(api[_-]?key|authorization|bearer|token)(['\"\\s:=]+)[^\\s,'\"]+", r"\1\2[redacted]", text)
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[redacted]", redacted)
    return redacted.strip()
