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
    "pregnancy_diary.entry.create",
    "pregnancy_diary.entry.update",
    "pregnancy_diary.entry.delete.propose",
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


def test_only_pregnancy_diary_delete_uses_the_action_policy() -> None:
    delete = DEFAULT_AGENT_ACTION_RULES["pregnancy_diary.entry.delete"]

    assert "pregnancy_diary.entry.create" not in DEFAULT_AGENT_ACTION_RULES
    assert "pregnancy_diary.entry.update" not in DEFAULT_AGENT_ACTION_RULES
    assert delete.requires_confirmation is True
    assert delete.target_type == "pregnancy_diary_entry"


def test_pregnancy_diary_direct_writes_wait_for_real_database_result() -> None:
    registry = default_tool_registry()
    create = registry.get("pregnancy_diary.entry.create")
    update = registry.get("pregnancy_diary.entry.update")
    delete = registry.get("pregnancy_diary.entry.delete.propose")

    for contract in (create, update):
        assert contract.blocking_policy == "must_wait"
        assert contract.result_dependency == "final_response"
        assert contract.idempotency_required is False
    assert delete.blocking_policy == "wait_for_confirmation"
    assert delete.requires_confirmation is True


def test_every_model_tool_and_namespace_explains_its_user_and_trigger() -> None:
    registry = default_tool_registry()
    namespaces = default_tool_namespace_registry(registry).list()

    for namespace in namespaces:
        assert "用户" in namespace.description, namespace.name
        assert "时使用" in namespace.description, namespace.name
    for contract in registry.list():
        assert "用户" in contract.description, contract.name
        assert "时调用" in contract.description or "前调用" in contract.description, contract.name
