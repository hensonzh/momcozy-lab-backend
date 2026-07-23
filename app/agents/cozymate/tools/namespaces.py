from __future__ import annotations

from app.agent_runtime.tools.contracts import ToolContract
from app.agent_runtime.tools.namespaces import ToolNamespace, ToolNamespaceRegistry
from app.agent_runtime.tools.registry import ToolContractRegistry


SCENARIO_NAMESPACE_DEFINITIONS: tuple[ToolNamespace, ...] = (
    ToolNamespace(
        name="milk_management",
        description=(
            "管理当前用户的喂养、吸奶、奶量、生长记录及相关计划和提醒。"
            "用户要查看数据、记录一次喂养/吸奶/生长、分析趋势，或创建和调整相关计划与提醒时使用。"
        ),
        tool_contracts=(
            "lactation_context_read",
            "records_milk_status_read",
            "records_milk_summary_read",
            "records_milk_analysis_read",
            "records_milk_analysis_intake",
            "records_milk_analysis_evaluate",
            "records_feeding_record_propose",
            "records_feeding_record_delete_propose",
            "records_pumping_record_propose",
            "records_pumping_record_delete_propose",
            "records_growth_record_propose",
            "records_growth_record_update_propose",
            "records_growth_record_delete_propose",
            "plans_current_read",
            "plans_calendar_read",
            "plans_milk_plan_propose",
            "plans_milk_schedule_propose",
            "plans_task_complete_propose",
            "plans_task_create_propose",
            "plans_milk_task_update_propose",
            "plans_milk_task_delete_propose",
            "notifications_milk_reminder_propose",
        ),
    ),
    ToolNamespace(
        name="birth_prep",
        description=(
            "处理当前用户的孕期计划、分娩沟通和待产包表单/卡片。用户要制定或调整孕期待办、梳理分娩偏好、生成沟通单或整理待产包时使用。"
        ),
        tool_contracts=(
            "pregnancy_plan_intake_start",
            "pregnancy_plan_intake_analyze",
            "pregnancy_plan_intake_advance",
            "pregnancy_plan_propose",
            "pregnancy_plan_todo_propose",
            "plans_plan_delete_propose",
            "plans_task_update_propose",
            "plans_task_delete_propose",
            "birth_plan_form_create",
            "labor_communication_card_create",
            "hospital_bag_form_create",
            "hospital_bag_card_create",
        ),
    ),
    ToolNamespace(
        name="hospital_bag_cart",
        description="调整当前用户已有的待产包购物车。用户要控制预算、增删物品、修改数量或同步吸奶器推荐时使用。",
        tool_contracts=("hospital_bag_cart_update",),
    ),
    ToolNamespace(
        name="pump_recommendation",
        description="根据当前用户预算、使用场景和偏好推荐 Momcozy 吸奶器。用户在购买前询问型号、差异、价格或如何选择时使用。",
        tool_contracts=("hospital_bag_pump_recommend",),
    ),
    ToolNamespace(
        name="device_support",
        description="处理当前用户吸奶器的状态查询、官方操作指导和售后工单。用户需要查看设备状态、安装使用、排查问题或联系售后时使用。",
        tool_contracts=(
            "devices_pump_status_read",
            "devices_guidance_read",
            "devices_unboxing_advance",
            "support_ticket_propose",
        ),
    ),
    ToolNamespace(
        name="health_consultation",
        description="为当前用户创建 IBCLC 哺乳顾问咨询入口。用户希望联系专业哺乳顾问或需要进一步人工咨询时使用。",
        tool_contracts=("ibclc_consult_card_create",),
    ),
    ToolNamespace(
        name="pregnancy_diary",
        description="管理当前用户孕期日记；用户需要读取、记录、修改或删除日记时使用。",
        tool_contracts=("pregnancy_diary_query", "pregnancy_diary_save", "pregnancy_diary_delete"),
    ),
)


def default_tool_namespace_registry(tool_registry: ToolContractRegistry | None = None) -> ToolNamespaceRegistry:
    registry = tool_registry or _default_tool_registry()
    registered_names = set(registry.names_for_sdk())
    configured_names = {tool_name for namespace in SCENARIO_NAMESPACE_DEFINITIONS for tool_name in namespace.tool_contracts}
    unknown_names = sorted(configured_names - registered_names)
    if unknown_names:
        raise ValueError(f"agent tool namespace references unknown contracts: {unknown_names}")
    return ToolNamespaceRegistry(
        namespaces=tuple(_scenario_namespace(namespace=namespace, registry=registry) for namespace in SCENARIO_NAMESPACE_DEFINITIONS)
    )


def _scenario_namespace(*, namespace: ToolNamespace, registry: ToolContractRegistry) -> ToolNamespace:
    contracts = [registry.get(tool_name) for tool_name in namespace.tool_contracts]
    tool_contracts = tuple(contract.name for contract in contracts)
    deferred_tool_contracts = tuple(contract.name for contract in contracts if _defer_loading(contract))
    return ToolNamespace(
        name=namespace.name,
        description=namespace.description,
        tool_contracts=tool_contracts,
        deferred_tool_contracts=deferred_tool_contracts,
    )


def _defer_loading(contract: ToolContract) -> bool:
    return contract.loading_mode == "deferred"


def _default_tool_registry() -> ToolContractRegistry:
    from .registry import default_tool_registry

    return default_tool_registry()
