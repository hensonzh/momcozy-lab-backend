import pytest

from app.agents.cozymate.actions import COZYMATE_ACTION_RULES, build_cozymate_action_handlers
from app.agents.cozymate.tools import default_tool_registry
from app.agent_runtime.providers.openai_responses import SdkNodeResult
from app.agent_runtime.tools.contracts import ToolContract
from app.agent_runtime.tools.registry import ToolContractRegistry


def test_model_visible_tool_contract_does_not_duplicate_internal_action_bindings() -> None:
    read_contract = _contract(name="profile_read", effect_scope="none")
    write_contract = _contract(effect_scope="user_resource")

    assert read_contract.effect_scope == "none"
    assert write_contract.effect_scope == "user_resource"
    assert set(ToolContract.model_fields) == {
        "name",
        "domain",
        "description",
        "input_schema",
        "output_schema",
        "effect_scope",
        "timeout_seconds",
    }


def test_cozymate_registry_exposes_new_effect_scopes_and_no_legacy_mixed_write_tools() -> None:
    registry = default_tool_registry()

    assert "maternal_infant_profile_update" in registry.names_for_sdk()
    assert "pregnancy_diary.manage" not in registry.names_for_sdk()

    assert registry.get("maternal_infant_profile_update").effect_scope == "user_resource"
    assert registry.get("pregnancy_diary_query").effect_scope == "none"

    assert registry.get("milk_analysis").effect_scope == "agent_internal"
    assert registry.get("support_ticket_propose").effect_scope == "agent_internal"


def test_every_action_backed_tool_is_registered_in_action_policy() -> None:
    registry = default_tool_registry()
    dependency = object()
    handlers = build_cozymate_action_handlers(
        notifications_service=dependency,
        plans_service=dependency,
        profile_service=dependency,
        lactation_context_service=dependency,
        records_service=dependency,
        diary_service=dependency,
        support_service=dependency,
    )
    registry.validate_action_bindings(
        policy_action_types=COZYMATE_ACTION_RULES,
        handler_action_types=handlers,
    )


def test_registry_rejects_missing_action_policy_or_handler_binding() -> None:
    registry = ToolContractRegistry()
    registry.register(_contract(effect_scope="user_resource"))

    with pytest.raises(ValueError, match="policy"):
        registry.validate_action_bindings(policy_action_types=set(), handler_action_types={"profile.update"})
    with pytest.raises(ValueError, match="handler"):
        registry.validate_action_bindings(policy_action_types={"profile.update"}, handler_action_types=set())


def test_model_result_has_no_out_of_band_action_proposal_channel() -> None:
    assert "action_proposals" not in SdkNodeResult.__dataclass_fields__


def _contract(*, effect_scope: str, name: str = "test_tool") -> ToolContract:
    return ToolContract(
        name=name,
        domain="test",
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
        effect_scope=effect_scope,
        timeout_seconds=10,
    )
