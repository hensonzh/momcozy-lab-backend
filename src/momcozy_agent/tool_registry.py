from __future__ import annotations

from typing import Any, Callable

from .contexts import DEFAULT_LOCALE, DEFAULT_TIMEZONE
from .health_guidance import health_guidance_web_search_tool, needs_breast_triage_first
from .tool_handlers.cards import (
    create_birth_journey_plan_card,
    delete_birth_journey_plan,
    update_birth_journey_plan_todo,
    manage_birth_journey_intake,
    create_labor_communication_card,
    create_birth_plan_form,
    create_form,
    create_hospital_bag_card,
    update_hospital_bag_cart,
    create_hospital_bag_form,
    recommend_hospital_bag_pump,
)
from .tool_handlers.common import decode_json_argument_strings
from .tool_handlers.device import create_support_ticket_draft, search_device_manual
from .tool_handlers.handoff import generate_handoff_summary
from .tool_handlers.ibclc import create_ibclc_consult_card
from .tool_handlers.milk_management import execute_milk_management_tool
from .tool_handlers.pregnancy_diary import manage_pregnancy_diary
from .tool_handlers.profile import get_profile, update_profile
from .tool_handlers.skill_runtime import (
    list_skills,
    load_skill,
    read_skill_file,
    run_approved_skill_script,
    search_skill_assets,
)
from .tool_handlers.ui import create_quick_replies
from .tool_schemas import FUNCTION_TOOLS
from .types import FunctionToolDefinition, RuntimeInputs, ToolDefinition, ToolName

ToolHandler = Callable[[dict[str, Any], RuntimeInputs], dict[str, Any]]

ALWAYS_ON_TOOLS: list[ToolName] = ["profile_get", "profile_update"]
SKILL_RUNTIME_TOOLS: list[ToolName] = ["list_skills", "load_skill", "search_skill_assets", "read_skill_file", "run_approved_skill_script"]
CORE_IMMEDIATE_TOOLS: list[ToolName] = [
    *ALWAYS_ON_TOOLS,
    *SKILL_RUNTIME_TOOLS,
    "ui_quick_replies_create",
    "ibclc_consult_card_create",
    "pregnancy_diary_manage",
]
MILK_MANAGEMENT_TOOLS: list[ToolName] = [
    "milk_snapshot_get",
    "milk_status_query",
    "milk_analysis_intake_manage",
    "milk_analysis_evaluate",
    "milk_plan_preview_create",
    "infant_growth_evaluate",
    "infant_growth_mutate",
    "milk_records_query",
    "milk_record_mutate",
    "milk_plan_query",
    "milk_plan_mutate",
    "milk_calendar_query",
    "milk_calendar_change_preview",
    "milk_calendar_reschedule_preview",
    "milk_calendar_mutate",
    "milk_task_complete",
]
MILK_MANAGEMENT_READ_ONLY_TOOLS: set[ToolName] = {
    "milk_snapshot_get",
    "milk_status_query",
    "milk_analysis_intake_manage",
    "milk_analysis_evaluate",
    "milk_plan_preview_create",
    "infant_growth_evaluate",
    "milk_records_query",
    "milk_plan_query",
    "milk_calendar_query",
    "milk_calendar_change_preview",
    "milk_calendar_reschedule_preview",
}

DEFERRED_TOOL_NAMESPACES: dict[str, dict[str, Any]] = {
    "care_handoffs": {
        "description": "用于已经决定转接人工或专业支持后的交接摘要生成。只有在用户明确需要/同意转接，或服务流程要求转接时加载；不要用于普通建议、设备售后工单、吸奶器使用排障或购物车调整。",
        "tool_names": ["handoff_summary_generate"],
    },
    "device_support": {
        "description": "用于用户已经拥有或正在使用 Momcozy 吸奶器/设备时的说明书、FAQ、配件、故障排查和售后工单信息。不要用于购买前型号选型/价格比较，也不要用于奶量记录、喂养计划或待产包购物车调整。",
        "tool_names": ["device_manual_search", "support_ticket_draft_create"],
    },
    "milk_management": {
        "description": "用于基于用户自身吸奶、亲喂、瓶喂、奶粉、宝宝生长和 calendar 数据的奶量管理：读取事实、评估状态、生成追奶/稳奶/减奶计划、调整日程或写入记录。不要用于吸奶器型号购买选型、设备故障排查、客服工单或待产包购物车调整。",
        "tool_names": MILK_MANAGEMENT_TOOLS,
    },
    "hospital_bag_cart": {
        "description": "用于当前对话已经进入待产包购物车后的购物车调整：预算上限优化、删除或加回商品、基础款替换、医院提供、家里已有、数量调整，以及把已推荐的 Momcozy 吸奶器型号同步到购物车。不要用于生成待产包清单、独立吸奶器型号选型或设备排障。",
        "tool_names": ["hospital_bag_cart_update"],
    },
    "pump_recommendation": {
        "description": "用于购买前的 Momcozy 吸奶器型号选型、价格比较和按使用场景推荐。用户单独询问哪款吸奶器适合自己、型号差异、预算内怎么选、某型号多少钱或点名追问某型号时使用。Air 1 属于高价轻薄款，不能作为降预算选择描述。不要用于已购设备故障/说明书问题、奶量是否正常或购物车直接修改。",
        "tool_names": ["hospital_bag_pump_recommend"],
    },
    "birth_prep": {
        "description": "用于产前准备服务已经进入具体产物流程后的专用表单和结构化内容：孕期计划、待产包清单、分娩沟通单，以及删除已保存的孕期计划或更新孕期计划 7 天行动清单完成状态。不要用于普通孕期问答；用户确认开始待产包整理后可直接创建待产包信息表，通过表单采集或确认必要信息；生成孕期计划、待产包清单或分娩沟通单前必须已有对应确认信息；删除孕期计划前必须已有用户明确确认。",
        "tool_names": [
            "birth_plan_form_create",
            "labor_communication_card_create",
            "birth_journey_intake_manage",
            "birth_journey_plan_card_create",
            "birth_journey_plan_delete",
            "birth_journey_plan_todo_update",
            "hospital_bag_form_create",
            "hospital_bag_card_create",
        ],
    },
}

READ_ONLY_TOOL_NAMES = {
    "profile_get",
    "handoff_summary_generate",
    "ibclc_consult_card_create",
    "birth_plan_form_create",
    "labor_communication_card_create",
    "birth_journey_intake_manage",
    "birth_journey_plan_card_create",
    "hospital_bag_form_create",
    "hospital_bag_card_create",
    "hospital_bag_cart_update",
    "hospital_bag_pump_recommend",
    "ui_quick_replies_create",
    "device_manual_search",
    "support_ticket_draft_create",
    *MILK_MANAGEMENT_READ_ONLY_TOOLS,
}

TOOL_HANDLERS: dict[ToolName, ToolHandler] = {
    "list_skills": list_skills,
    "load_skill": load_skill,
    "search_skill_assets": search_skill_assets,
    "read_skill_file": read_skill_file,
    "run_approved_skill_script": run_approved_skill_script,
    "ui_form_create": create_form,
    "ui_quick_replies_create": create_quick_replies,
    "birth_plan_form_create": create_birth_plan_form,
    "labor_communication_card_create": create_labor_communication_card,
    "birth_journey_intake_manage": manage_birth_journey_intake,
    "birth_journey_plan_card_create": create_birth_journey_plan_card,
    "birth_journey_plan_delete": delete_birth_journey_plan,
    "birth_journey_plan_todo_update": update_birth_journey_plan_todo,
    "pregnancy_diary_manage": manage_pregnancy_diary,
    "hospital_bag_form_create": create_hospital_bag_form,
    "hospital_bag_card_create": create_hospital_bag_card,
    "hospital_bag_cart_update": update_hospital_bag_cart,
    "hospital_bag_pump_recommend": recommend_hospital_bag_pump,
    "ibclc_consult_card_create": create_ibclc_consult_card,
    "profile_get": get_profile,
    "profile_update": update_profile,
    "handoff_summary_generate": generate_handoff_summary,
    "device_manual_search": search_device_manual,
    "support_ticket_draft_create": create_support_ticket_draft,
}
TOOL_HANDLERS.update({tool_name: execute_milk_management_tool for tool_name in MILK_MANAGEMENT_TOOLS})


def select_runtime_tools(inputs: RuntimeInputs | None = None) -> list[ToolDefinition]:
    tools: list[ToolDefinition] = [{"type": "tool_search"}]
    if not needs_breast_triage_first(inputs or {}):
        tools.append(health_guidance_web_search_tool())  # type: ignore[arg-type]
    tools.extend(FUNCTION_TOOLS[name] for name in CORE_IMMEDIATE_TOOLS)
    tools.extend(_deferred_tool_namespaces())

    return tools


def execute_tool(name: str, arguments: dict[str, Any], inputs: RuntimeInputs | None = None) -> dict[str, Any]:
    args = decode_json_argument_strings(arguments)
    runtime_inputs: RuntimeInputs = inputs or {"user_message": "", "locale": DEFAULT_LOCALE, "timezone": DEFAULT_TIMEZONE, "message_sent_at": ""}
    handler_args = dict(args)
    handler_args["_tool_name"] = name

    handler = TOOL_HANDLERS.get(name)  # type: ignore[arg-type]
    if handler is not None:
        return handler(handler_args, runtime_inputs)
    raise ValueError(f"Unknown or unavailable tool: {name}")


def execute_business_tool(name: str, arguments: dict[str, Any], inputs: RuntimeInputs | None = None) -> dict[str, Any]:
    if name in SKILL_RUNTIME_TOOLS:
        raise ValueError(f"{name} is a skill runtime tool; use execute_tool instead.")
    return execute_tool(name, arguments, inputs)


def _deferred_tool_namespaces() -> list[ToolDefinition]:
    namespaces: list[ToolDefinition] = []
    for namespace_name, namespace in DEFERRED_TOOL_NAMESPACES.items():
        namespaces.append(
            {
                "type": "namespace",
                "name": namespace_name,
                "description": namespace["description"],
                "tools": [_deferred_tool(tool_name) for tool_name in namespace["tool_names"]],
            }
        )
    return namespaces


def _deferred_tool(tool_name: ToolName) -> FunctionToolDefinition:
    tool = dict(FUNCTION_TOOLS[tool_name])
    tool["defer_loading"] = True
    return tool  # type: ignore[return-value]
