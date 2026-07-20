from __future__ import annotations

from ....core.metrics import RequestMetrics
from ....core.settings import Settings
from .runner import AgentModelRunner, OpenAIResponsesRunner


def create_agent_model_runner(
    *,
    settings: Settings,
    metrics: RequestMetrics | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    timeout_seconds: float | None = None,
    reasoning_effort: str | None = None,
    metrics_node_name: str | None = None,
) -> AgentModelRunner:
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
