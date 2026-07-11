from production_backend.app.modules.agent_runtime.actions.policy import DEFAULT_AGENT_ACTION_RULES
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools import (
    default_tool_namespace_registry,
    default_tool_registry,
)
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import (
    SERVICE_SKILL_RECOMMENDED_TOOL_CONTRACTS,
)


PREGNANCY_DIARY_TOOL_CONTRACTS = {
    "pregnancy_diary.entries.read",
    "pregnancy_diary.entry_create.propose",
    "pregnancy_diary.entry_update.propose",
    "pregnancy_diary.entry_delete.propose",
}


def test_pregnancy_diary_is_an_independent_global_namespace() -> None:
    registry = default_tool_registry()
    namespaces = {item.name: item for item in default_tool_namespace_registry(registry).list()}

    assert set(namespaces["pregnancy_diary"].tool_contracts) == PREGNANCY_DIARY_TOOL_CONTRACTS
    assert PREGNANCY_DIARY_TOOL_CONTRACTS <= set(registry.names_for_sdk())
    assert "diary.entry_upsert.propose" not in registry.names_for_sdk()


def test_pregnancy_diary_tools_are_not_recommended_by_any_service_skill() -> None:
    recommended = {contract for contracts in SERVICE_SKILL_RECOMMENDED_TOOL_CONTRACTS.values() for contract in contracts}

    assert recommended.isdisjoint(PREGNANCY_DIARY_TOOL_CONTRACTS)
    assert "diary.entry_upsert.propose" not in recommended


def test_pregnancy_diary_action_policy_auto_applies_create_and_update_only() -> None:
    create = DEFAULT_AGENT_ACTION_RULES["pregnancy_diary.entry.create"]
    update = DEFAULT_AGENT_ACTION_RULES["pregnancy_diary.entry.update"]
    delete = DEFAULT_AGENT_ACTION_RULES["pregnancy_diary.entry.delete"]

    assert create.requires_confirmation is False
    assert update.requires_confirmation is False
    assert delete.requires_confirmation is True
    assert {create.target_type, update.target_type, delete.target_type} == {"pregnancy_diary_entry"}
