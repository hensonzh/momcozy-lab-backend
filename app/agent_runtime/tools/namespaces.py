from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ApiError


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
