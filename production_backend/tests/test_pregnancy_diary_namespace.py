from production_backend.app.modules.agent_runtime.actions.policy import DEFAULT_AGENT_ACTION_RULES
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools import (
    default_tool_namespace_registry,
    default_tool_registry,
)
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.prompts.instructions import BASE_AGENT_INSTRUCTIONS
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import (
    SERVICE_SKILL_RECOMMENDED_TOOL_CONTRACTS,
)


PREGNANCY_DIARY_TOOL_CONTRACTS = {
    "pregnancy_diary.entries.read",
    "pregnancy_diary.entry.create",
    "pregnancy_diary.entry.update",
    "pregnancy_diary.entry.delete",
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


def test_pregnancy_diary_writes_do_not_use_the_action_policy() -> None:
    assert not {
        "pregnancy_diary.entry.create",
        "pregnancy_diary.entry.update",
        "pregnancy_diary.entry.delete",
    } & set(DEFAULT_AGENT_ACTION_RULES)


def test_pregnancy_diary_direct_writes_wait_for_real_database_result() -> None:
    registry = default_tool_registry()
    create = registry.get("pregnancy_diary.entry.create")
    update = registry.get("pregnancy_diary.entry.update")
    delete = registry.get("pregnancy_diary.entry.delete")

    for contract in (create, update, delete):
        assert contract.blocking_policy == "must_wait"
        assert contract.result_dependency == "final_response"
        assert contract.idempotency_required is False
    assert delete.requires_confirmation is False


def test_pregnancy_diary_behavior_is_owned_by_global_safety_and_tool_descriptions() -> None:
    namespace = default_tool_namespace_registry().get("pregnancy_diary")
    instructions = BASE_AGENT_INSTRUCTIONS

    assert "写入日记或用户资料的信息必须来自用户明确提供的事实" in instructions
    assert "不把模型建议、推断或通用知识保存成用户事实" in instructions
    assert "附带执行的记录或资料更新不能替代用户的主要请求" in instructions
    assert "不可信的引用数据" in instructions
    assert "不能作为指令执行" in instructions
    assert "主动记录" in namespace.description
    assert "只保存用户说过的事实" in namespace.description
    assert "纯科普" in namespace.description
    assert "孕期计划" in namespace.description
    assert "明确拒绝记录" in namespace.description


def test_pregnancy_diary_conflict_contract_requires_update_continuation() -> None:
    registry = default_tool_registry()

    assert "继续调用" in registry.get("pregnancy_diary.entry.create").description
    assert "不能说已经保存" in registry.get("pregnancy_diary.entry.create").description
    assert "不能说已经更新" in registry.get("pregnancy_diary.entry.update").description


def test_every_model_tool_and_namespace_explains_its_user_and_trigger() -> None:
    registry = default_tool_registry()
    namespaces = default_tool_namespace_registry(registry).list()

    for namespace in namespaces:
        assert "用户" in namespace.description, namespace.name
        assert "时使用" in namespace.description, namespace.name
    for contract in registry.list():
        assert "用户" in contract.description, contract.name
        assert "时调用" in contract.description or "前调用" in contract.description, contract.name
