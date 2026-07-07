from __future__ import annotations

from ....core.metrics import RequestMetrics
from ....core.settings import SUPPORTED_AGENT_MODEL_PROVIDERS, Settings
from .runner import OpenAIAgentsSdkRunner


def create_agent_sdk_runner(
    *,
    settings: Settings,
    metrics: RequestMetrics | None = None,
    trace_enabled: bool | None = None,
) -> OpenAIAgentsSdkRunner:
    if settings.agent_model_provider not in SUPPORTED_AGENT_MODEL_PROVIDERS:
        raise ValueError(
            "AGENT_MODEL_PROVIDER must be one of "
            + ", ".join(sorted(SUPPORTED_AGENT_MODEL_PROVIDERS))
        )
    if settings.agent_model_provider == "minimax":
        return OpenAIAgentsSdkRunner(
            model=settings.minimax_model,
            max_turns=settings.openai_agent_max_turns,
            timeout_seconds=settings.openai_agent_timeout_seconds,
            trace_enabled=settings.openai_agent_trace_enabled if trace_enabled is None else trace_enabled,
            provider="minimax",
            api_key=settings.minimax_api_key,
            base_url=settings.minimax_base_url,
            use_responses=False,
            buffer_streamed_tool_calls=True,
            metrics=metrics,
        )

    return OpenAIAgentsSdkRunner(
        model=settings.openai_model,
        max_turns=settings.openai_agent_max_turns,
        timeout_seconds=settings.openai_agent_timeout_seconds,
        trace_enabled=settings.openai_agent_trace_enabled if trace_enabled is None else trace_enabled,
        provider="openai",
        api_key=settings.openai_api_key,
        metrics=metrics,
    )
