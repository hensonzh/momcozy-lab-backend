from app.agents.cozymate.tools import (
    LactationTimelineManageToolHandler,
    PlanTaskCompleteProposeToolHandler,
    PlanTaskCreateProposeToolHandler,
    PlanTaskDeleteProposeToolHandler,
    PlanTaskUpdateProposeToolHandler,
    PlanTaskWriteToolHandler,
    PregnancyDiaryDeleteToolHandler,
    PregnancyDiarySaveToolHandler,
    PregnancyDiaryWriteToolHandler,
    default_tool_registry,
)
from app.agents.cozymate.tools.handlers import build_default_tool_handlers


EXPECTED_MODEL_TOOL_NAMES = {
    "conversation_history_image_read",
    "devices_guidance_manage",
    "hospital_bag_cart_write",
    "hospital_bag_manage",
    "ibclc_consult_card_write",
    "lactation_timeline_read",
    "lactation_timeline_write",
    "milk_analysis_manage",
    "notifications_milk_reminder_write",
    "plans_calendar_read",
    "plans_current_read",
    "plans_milk_plan_write",
    "plans_plan_write",
    "plans_task_write",
    "pregnancy_diary_read",
    "pregnancy_diary_write",
    "pregnancy_plan_manage",
    "profile_read",
    "profile_write",
    "pump_models_read",
    "support_ticket_write",
}


def test_model_tools_use_the_canonical_operation_suffixes() -> None:
    registry = default_tool_registry()
    names = set(registry.names_for_sdk())

    assert names == EXPECTED_MODEL_TOOL_NAMES
    assert all(name.endswith(("_read", "_write", "_evaluate", "_manage")) for name in names)


def test_every_write_tool_requires_an_explicit_operation() -> None:
    registry = default_tool_registry()

    for name in EXPECTED_MODEL_TOOL_NAMES:
        if not name.endswith("_write"):
            continue
        schema = registry.get(name).input_schema
        assert "operation" in schema["required"], name
        assert schema["properties"]["operation"]["type"] == "string", name


def test_consolidated_resource_writes_expose_only_supported_operations() -> None:
    registry = default_tool_registry()

    assert registry.get("profile_write").input_schema["properties"]["operation"]["enum"] == [
        "update",
    ]
    assert registry.get("pregnancy_diary_write").input_schema["properties"]["operation"]["enum"] == [
        "create",
        "update",
        "delete",
    ]
    assert registry.get("plans_task_write").input_schema["properties"]["operation"]["enum"] == [
        "create",
        "update",
        "delete",
    ]
    assert registry.get("lactation_timeline_write").input_schema["properties"]["operation"]["enum"] == [
        "create",
        "update",
        "delete",
        "set_status",
        "reschedule",
    ]


def test_consolidated_write_handlers_keep_precise_internal_operation_boundaries() -> None:
    runtime_service = object()
    diary = PregnancyDiaryWriteToolHandler(runtime_service=runtime_service)
    tasks = PlanTaskWriteToolHandler(runtime_service=runtime_service)

    assert isinstance(diary.operations["create"], PregnancyDiarySaveToolHandler)
    assert diary.operations["create"] is diary.operations["update"]
    assert isinstance(diary.operations["delete"], PregnancyDiaryDeleteToolHandler)
    assert isinstance(tasks.create_handler, PlanTaskCreateProposeToolHandler)
    assert isinstance(tasks.complete_handler, PlanTaskCompleteProposeToolHandler)
    assert isinstance(tasks.update_handler, PlanTaskUpdateProposeToolHandler)
    assert isinstance(tasks.delete_handler, PlanTaskDeleteProposeToolHandler)


def test_timeline_write_uses_the_composite_timeline_handler() -> None:
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

    assert isinstance(handlers["lactation_timeline_write"], LactationTimelineManageToolHandler)
