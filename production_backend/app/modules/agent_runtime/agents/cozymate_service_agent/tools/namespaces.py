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


NAMESPACE_DOMAIN_ORDER = (
    "profiles",
    "business_context",
    "records",
    "plans",
    "diary",
    "memory",
    "devices",
    "files",
    "birth_prep",
    "hospital_bag",
    "notifications",
    "support",
)
NAMESPACE_DESCRIPTIONS = {
    "profiles": "读取当前用户基础资料的工具。",
    "business_context": "读取当前用户业务上下文摘要的工具。",
    "records": "读取奶量、喂养、吸奶记录，并提出记录草稿的工具。",
    "plans": "读取和提出孕期、奶量、任务计划的工具。",
    "diary": "读取和提出孕期日记更新的工具。",
    "memory": "提出长期记忆写入的工具。",
    "devices": "读取吸奶器状态和官方设备指导素材的工具。",
    "files": "读取用户上传图片视觉摘要的工具。",
    "birth_prep": "创建产前准备表单和分娩沟通产物的工具。",
    "hospital_bag": "创建待产包卡片、购物车调整和吸奶器推荐产物的工具。",
    "notifications": "提出提醒创建的工具。",
    "support": "提出售后或人工支持工单的工具。",
}
EAGER_READ_CONTRACTS = frozenset(
    {
        "profile.read",
        "business.context.read",
        "records.milk_status.read",
        "records.milk_analysis.read",
        "records.milk_summary.read",
        "records.growth.read",
        "plans.calendar.read",
        "plans.current.read",
        "diary.recent.read",
        "pregnancy.plan_context.read",
        "devices.pump_status.read",
        "devices.guidance_assets.read",
    }
)


def default_tool_namespace_registry(tool_registry: ToolContractRegistry | None = None) -> ToolNamespaceRegistry:
    registry = tool_registry or _default_tool_registry()
    contracts_by_domain: dict[str, list[ToolContract]] = {}
    for contract in registry.list():
        contracts_by_domain.setdefault(contract.domain, []).append(contract)

    ordered_domains = [
        domain
        for domain in NAMESPACE_DOMAIN_ORDER
        if domain in contracts_by_domain
    ]
    ordered_domains.extend(sorted(domain for domain in contracts_by_domain if domain not in set(ordered_domains)))
    return ToolNamespaceRegistry(
        namespaces=tuple(_namespace_for_domain(domain=domain, contracts=contracts_by_domain[domain]) for domain in ordered_domains)
    )


def _namespace_for_domain(*, domain: str, contracts: list[ToolContract]) -> ToolNamespace:
    tool_contracts = tuple(sorted(contract.name for contract in contracts))
    deferred_tool_contracts = tuple(
        sorted(contract.name for contract in contracts if _defer_loading(contract))
    )
    return ToolNamespace(
        name=domain,
        description=NAMESPACE_DESCRIPTIONS.get(domain, f"{domain} 领域工具。"),
        tool_contracts=tool_contracts,
        deferred_tool_contracts=deferred_tool_contracts,
    )


def _defer_loading(contract: ToolContract) -> bool:
    if contract.name in EAGER_READ_CONTRACTS:
        return False
    return True


def _default_tool_registry() -> ToolContractRegistry:
    from .registry import default_tool_registry

    return default_tool_registry()
