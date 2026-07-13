from __future__ import annotations

from dataclasses import dataclass

from production_backend.app.core.errors import ApiError

from .contracts import ToolContract
from .registry import ToolContractRegistry


@dataclass(frozen=True)
class ToolNamespace:
    name: str
    description: str
    tool_contracts: tuple[str, ...]
    deferred_tool_contracts: tuple[str, ...] = ()

    def state_summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "tool_contracts": list(self.tool_contracts),
            "deferred_tool_contracts": list(self.deferred_tool_contracts),
        }


class ToolNamespaceRegistry:
    def __init__(self, namespaces: tuple[ToolNamespace, ...]) -> None:
        self._namespaces = namespaces
        self._by_name = {namespace.name: namespace for namespace in namespaces}
        if len(self._by_name) != len(namespaces):
            raise ValueError("duplicate agent tool namespace name")
        assigned_contracts: list[str] = []
        for namespace in namespaces:
            assigned_contracts.extend(namespace.tool_contracts)
            unknown_deferred = set(namespace.deferred_tool_contracts) - set(namespace.tool_contracts)
            if unknown_deferred:
                raise ValueError(f"deferred tool contracts must belong to namespace {namespace.name}: {sorted(unknown_deferred)}")
        if len(set(assigned_contracts)) != len(assigned_contracts):
            raise ValueError("agent tool contract assigned to multiple namespaces")

    def get(self, name: str) -> ToolNamespace:
        namespace = self._by_name.get(name)
        if namespace is None:
            raise ApiError(code="agent_tool_namespace_not_found", message="Agent tool namespace is not registered.", status=500)
        return namespace

    def list(self) -> tuple[ToolNamespace, ...]:
        return self._namespaces

    def names_for_sdk(self) -> tuple[str, ...]:
        names = {tool_name for namespace in self._namespaces for tool_name in namespace.tool_contracts}
        return tuple(sorted(names))


SCENARIO_NAMESPACE_DEFINITIONS: tuple[ToolNamespace, ...] = (
    ToolNamespace(
        name="milk_management",
        description=(
            "管理当前用户的喂养、吸奶、奶量、生长记录及相关计划和提醒。"
            "用户要查看数据、记录一次喂养/吸奶/生长、分析趋势，或创建和调整相关计划与提醒时使用。"
        ),
        tool_contracts=(
            "records.milk_status.read",
            "records.milk_summary.read",
            "records.milk_analysis.read",
            "records.milk_analysis.intake",
            "records.milk_analysis.evaluate",
            "records.growth.read",
            "records.feeding_record.propose",
            "records.feeding_record_delete.propose",
            "records.pumping_record.propose",
            "records.pumping_record_delete.propose",
            "records.growth_record.propose",
            "records.growth_record_update.propose",
            "records.growth_record_delete.propose",
            "plans.current.read",
            "plans.calendar.read",
            "plans.milk_plan.propose",
            "plans.milk_schedule.propose",
            "plans.task_complete.propose",
            "plans.task_create.propose",
            "plans.milk_task_update.propose",
            "plans.milk_task_delete.propose",
            "notifications.milk_reminder.propose",
        ),
    ),
    ToolNamespace(
        name="birth_prep",
        description=(
            "处理当前用户的孕期计划、分娩沟通和待产包表单/卡片。用户要制定或调整孕期待办、梳理分娩偏好、生成沟通单或整理待产包时使用。"
        ),
        tool_contracts=(
            "pregnancy.plan_intake.start",
            "pregnancy.plan_intake.analyze",
            "pregnancy.plan_intake.advance",
            "pregnancy.plan.propose",
            "plans.plan_delete.propose",
            "plans.task_update.propose",
            "plans.task_delete.propose",
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
            "devices.pump_status.read",
            "devices.guidance.read",
            "devices.unboxing.advance",
            "support.ticket.propose",
        ),
    ),
    ToolNamespace(
        name="health_consultation",
        description="为当前用户创建 IBCLC 哺乳顾问咨询入口。用户希望联系专业哺乳顾问或需要进一步人工咨询时使用。",
        tool_contracts=("ibclc_consult_card_create",),
    ),
    ToolNamespace(
        name="pregnancy_diary",
        description=(
            "管理当前用户孕期日记的读取、记录、完整重写和删除。"
            "用户要查看某日记录，或明确要求保存时使用；用户第一人称具体讲述值得留存的孕期事实时也应主动记录，不要求先说‘记一下’。"
            "只保存用户说过的事实，不保存模型建议；纯科普、孕期计划意图、明确拒绝记录时不写入。"
        ),
        tool_contracts=("pregnancy_diary.manage",),
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
