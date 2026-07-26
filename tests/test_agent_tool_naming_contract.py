from app.agents.cozymate.tools import (
    DiaryDeleteToolHandler,
    DiarySaveToolHandler,
    DiaryMutateToolHandler,
    PlanMutateToolHandler,
    PlanReadToolHandler,
    ScheduleTimelineMutateToolHandler,
    default_tool_registry,
)
from app.agents.cozymate.tools.handlers import build_default_tool_handlers


EXPECTED_MODEL_TOOL_NAMES = {
    "conversation_history_image_read",
    "devices_guidance_manage",
    "hospital_bag_cart_mutate",
    "hospital_bag_manage",
    "ibclc_consult_card_create",
    "milk_analysis_manage",
    "plan_mutate",
    "plan_read",
    "diary_read",
    "diary_mutate",
    "pregnancy_intake_manage",
    "profile_read",
    "profile_update",
    "pump_models_read",
    "schedule_timeline_read",
    "schedule_timeline_mutate",
    "support_ticket_create",
}

MULTI_OPERATION_MUTATION_TOOLS = {
    "hospital_bag_cart_mutate",
    "plan_mutate",
    "diary_mutate",
    "schedule_timeline_mutate",
}

SINGLE_OPERATION_TOOLS = {
    "ibclc_consult_card_create": "create",
    "support_ticket_create": "create",
}


def test_model_tools_use_the_canonical_capability_suffixes() -> None:
    registry = default_tool_registry()
    names = set(registry.names_for_sdk())

    assert names == EXPECTED_MODEL_TOOL_NAMES
    assert all(
        name.endswith(("_read", "_mutate", "_manage", "_create", "_update", "_delete"))
        for name in names
    )


def test_every_mutate_tool_requires_an_explicit_operation() -> None:
    registry = default_tool_registry()

    for name in MULTI_OPERATION_MUTATION_TOOLS:
        schema = registry.get(name).input_schema
        assert "operation" in schema["required"], name
        assert schema["properties"]["operation"]["type"] == "string", name


def test_single_operation_tool_names_match_their_operation() -> None:
    registry = default_tool_registry()

    for name, operation in SINGLE_OPERATION_TOOLS.items():
        schema = registry.get(name).input_schema
        assert "operation" in schema["required"], name
        assert schema["properties"]["operation"]["enum"] == [operation], name


def test_single_purpose_profile_update_does_not_repeat_operation_in_arguments() -> None:
    schema = default_tool_registry().get("profile_update").input_schema

    assert "operation" not in schema["properties"]
    assert "required" not in schema


def test_consolidated_resource_mutations_expose_only_supported_operations() -> None:
    registry = default_tool_registry()

    assert registry.get("diary_mutate").input_schema["properties"]["operation"]["enum"] == [
        "create",
        "update",
        "delete",
    ]
    assert registry.get("schedule_timeline_mutate").input_schema["properties"]["operation"]["enum"] == [
        "create",
        "update",
        "delete",
        "set_status",
        "reschedule",
    ]
    assert registry.get("plan_mutate").input_schema["properties"]["operation"]["enum"] == [
        "create",
        "update",
        "delete",
    ]


def test_consolidated_mutation_handlers_keep_precise_internal_operation_boundaries() -> None:
    runtime_service = object()
    diary = DiaryMutateToolHandler(runtime_service=runtime_service)

    assert isinstance(diary.operations["create"], DiarySaveToolHandler)
    assert diary.operations["create"] is diary.operations["update"]
    assert isinstance(diary.operations["delete"], DiaryDeleteToolHandler)


def test_schedule_timeline_mutation_is_the_only_model_visible_timeline_handler() -> None:
    dependency = object()
    handlers = build_default_tool_handlers(
        profile_service=dependency,
        lactation_context_service=dependency,
        records_service=dependency,
        plans_service=dependency,
        diary_service=dependency,
        asset_service=dependency,
        agent_runtime_service=dependency,
    )

    assert isinstance(handlers["schedule_timeline_mutate"], ScheduleTimelineMutateToolHandler)
    assert isinstance(handlers["plan_read"], PlanReadToolHandler)
    assert isinstance(handlers["plan_mutate"], PlanMutateToolHandler)
    assert set(handlers) == EXPECTED_MODEL_TOOL_NAMES
