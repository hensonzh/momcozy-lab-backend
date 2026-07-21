from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_retired_backend_copy_and_agents_sdk_rollback_do_not_return() -> None:
    assert not (ROOT / "production_backend").exists()
    assert "openai-agents" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")

    runtime_source = (ROOT / "app/agent_runtime/providers/openai_responses.py").read_text(encoding="utf-8")
    factory_source = (ROOT / "app/agent_runtime/providers/factory.py").read_text(encoding="utf-8")
    assert 'import_module("agents")' not in runtime_source
    assert "OpenAIAgentsSdkRunner" not in runtime_source
    assert "OPENAI_AGENT_USE_RESPONSES" not in factory_source


def test_retired_contract_fields_and_safety_metrics_do_not_return() -> None:
    event_semantics = (ROOT / "app/agent_runtime/events/semantics.py").read_text(encoding="utf-8")
    runtime_models = (ROOT / "app/agent_runtime/runs/models.py").read_text(encoding="utf-8")
    metrics = (ROOT / "app/core/metrics.py").read_text(encoding="utf-8")

    assert '"visibility"' not in event_semantics
    assert "AgentRoutingDecision" not in runtime_models
    assert "routing_summary_json" not in runtime_models
    assert "record_agent_safety" not in metrics
    assert '"agent_safety"' not in metrics


def test_quick_reply_generation_chain_is_intentionally_preserved() -> None:
    quick_replies = ROOT / "app/agents/cozymate/quick_replies.py"
    executor_source = (ROOT / "app/agents/cozymate/executor.py").read_text(encoding="utf-8")

    assert quick_replies.is_file()
    assert "QuickReplyFinalizer" in executor_source
    assert "quick_reply_finalizer" in executor_source


def test_main_agent_prompt_has_no_version_selection_layer() -> None:
    prompt_source = (ROOT / "app/agents/cozymate/prompts/instructions.py").read_text(encoding="utf-8")
    settings_source = (ROOT / "app/core/settings.py").read_text(encoding="utf-8")

    assert "CURRENT_AGENT_PROMPT_VERSION" not in prompt_source
    assert "resolve_agent_prompt" not in prompt_source
    assert "OPENAI_AGENT_PROMPT_VERSION" not in settings_source
