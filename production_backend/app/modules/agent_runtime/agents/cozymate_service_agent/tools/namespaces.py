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
        description="奶量、喂养、吸奶、生长记录、奶量计划、提醒和奶量任务工具包。",
        tool_contracts=(
            "records.milk_status.read",
            "records.milk_summary.read",
            "records.milk_analysis.read",
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
            "plans.milk_plan_preview.create",
            "plans.task_complete.propose",
            "plans.task_create.propose",
            "notifications.milk_reminder.propose",
        ),
    ),
    ToolNamespace(
        name="birth_prep",
        description="孕期计划、分娩沟通、待产包表单与待产包卡片工具包。",
        tool_contracts=(
            "pregnancy.plan_create.propose",
            "plans.plan_delete.propose",
            "plans.task_update.propose",
            "plans.task_delete.propose",
            "birth_journey_plan_card_create",
            "birth_plan_form_create",
            "labor_communication_card_create",
            "hospital_bag_form_create",
            "hospital_bag_card_create",
        ),
    ),
    ToolNamespace(
        name="hospital_bag_cart",
        description="待产包购物车调整工具包。",
        tool_contracts=("hospital_bag_cart_update",),
    ),
    ToolNamespace(
        name="pump_recommendation",
        description="待产包相关吸奶器推荐工具包。",
        tool_contracts=("hospital_bag_pump_recommend",),
    ),
    ToolNamespace(
        name="device_support",
        description="吸奶器设备状态、官方指导素材和售后工单工具包。",
        tool_contracts=(
            "devices.pump_status.read",
            "devices.guidance_assets.read",
            "support.ticket.propose",
        ),
    ),
    ToolNamespace(
        name="health_consultation",
        description="健康咨询中用于提出日记记录和创建 IBCLC 咨询卡片的工具包。",
        tool_contracts=(
            "diary.entry_upsert.propose",
            "ibclc_consult_card_create",
        ),
    ),
)
def default_tool_namespace_registry(tool_registry: ToolContractRegistry | None = None) -> ToolNamespaceRegistry:
    registry = tool_registry or _default_tool_registry()
    registered_names = set(registry.names_for_sdk())
    configured_names = {
        tool_name
        for namespace in SCENARIO_NAMESPACE_DEFINITIONS
        for tool_name in namespace.tool_contracts
    }
    unknown_names = sorted(configured_names - registered_names)
    if unknown_names:
        raise ValueError(f"agent tool namespace references unknown contracts: {unknown_names}")
    return ToolNamespaceRegistry(
        namespaces=tuple(
            _scenario_namespace(namespace=namespace, registry=registry)
            for namespace in SCENARIO_NAMESPACE_DEFINITIONS
        )
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
