from __future__ import annotations

from ....core.metrics import RequestMetrics
from ....core.settings import SUPPORTED_AGENT_MODEL_PROVIDERS, Settings
from .runner import AgentModelRunner, OpenAIAgentsSdkRunner, OpenAIResponsesRunner


def create_agent_model_runner(
    *,
    settings: Settings,
    metrics: RequestMetrics | None = None,
    trace_enabled: bool | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    timeout_seconds: float | None = None,
    reasoning_effort: str | None = None,
    metrics_node_name: str | None = None,
) -> AgentModelRunner:
    if settings.agent_model_provider not in SUPPORTED_AGENT_MODEL_PROVIDERS:
        raise ValueError(
            "AGENT_MODEL_PROVIDER must be one of "
            + ", ".join(sorted(SUPPORTED_AGENT_MODEL_PROVIDERS))
        )
    if settings.agent_model_provider == "minimax":
        return OpenAIAgentsSdkRunner(
            model=model or settings.minimax_model,
            max_turns=max_turns or settings.openai_agent_max_turns,
            timeout_seconds=timeout_seconds or settings.openai_agent_timeout_seconds,
            trace_enabled=settings.openai_agent_trace_enabled if trace_enabled is None else trace_enabled,
            provider="minimax",
            api_key=settings.minimax_api_key,
            base_url=settings.minimax_base_url,
            use_responses=False,
            buffer_streamed_tool_calls=True,
            metrics=metrics,
            metrics_node_name=metrics_node_name or "openai_agents_sdk",
        )

    if not settings.openai_agent_use_responses:
        return OpenAIAgentsSdkRunner(
            model=model or settings.openai_model,
            max_turns=max_turns or settings.openai_agent_max_turns,
            timeout_seconds=timeout_seconds or settings.openai_agent_timeout_seconds,
            trace_enabled=settings.openai_agent_trace_enabled if trace_enabled is None else trace_enabled,
            provider="openai",
            api_key=settings.openai_api_key,
            use_responses=False,
            metrics=metrics,
            metrics_node_name=metrics_node_name or "openai_agents_sdk",
        )

    return OpenAIResponsesRunner(
        model=model or settings.openai_model,
        max_turns=max_turns or settings.openai_agent_max_turns,
        timeout_seconds=timeout_seconds or settings.openai_agent_timeout_seconds,
        api_key=settings.openai_api_key,
        reasoning_effort=reasoning_effort or settings.openai_reasoning_effort,
        store_responses=settings.openai_responses_store,
        metrics=metrics,
        metrics_node_name=metrics_node_name or "openai_responses",
    )


def create_agent_sdk_runner(
    *,
    settings: Settings,
    metrics: RequestMetrics | None = None,
    trace_enabled: bool | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    timeout_seconds: float | None = None,
    reasoning_effort: str | None = None,
    metrics_node_name: str | None = None,
) -> AgentModelRunner:
    return create_agent_model_runner(
        settings=settings,
        metrics=metrics,
        trace_enabled=trace_enabled,
        model=model,
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        reasoning_effort=reasoning_effort,
        metrics_node_name=metrics_node_name,
    )
