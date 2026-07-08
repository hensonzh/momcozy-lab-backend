from __future__ import annotations

from dataclasses import dataclass

from ....core.errors import ApiError


@dataclass(frozen=True)
class AgentGraphDefinition:
    version: str
    runtime_pattern: str
    node_names: tuple[str, ...]
    edge_names: tuple[str, ...]


class AgentGraphRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, AgentGraphDefinition] = {}

    def register(self, definition: AgentGraphDefinition) -> None:
        if definition.runtime_pattern != "langgraph_sdk":
            raise ApiError(code="validation_failed", message="Agent graph must use langgraph_sdk runtime.", status=422)
        self._definitions[definition.version] = definition

    def get(self, version: str) -> AgentGraphDefinition:
        definition = self._definitions.get(version)
        if definition is None:
            raise ApiError(code="not_found", message="Agent graph version not found.", status=404)
        return definition


def default_graph_registry() -> AgentGraphRegistry:
    registry = AgentGraphRegistry()
    registry.register(
        AgentGraphDefinition(
            version="momcozy-agent-v1",
            runtime_pattern="langgraph_sdk",
            node_names=(
                "load_context",
                "safety_gate",
                "select_service_skill",
                "sdk_reasoning",
                "tool_result_review",
                "action_policy",
                "confirmation_interrupt",
                "final_response",
                "finish",
            ),
            edge_names=(
                "load_context->safety_gate",
                "safety_gate->select_service_skill",
                "select_service_skill->sdk_reasoning",
                "sdk_reasoning->tool_result_review",
                "tool_result_review->action_policy",
                "action_policy->confirmation_interrupt",
                "action_policy->final_response",
                "confirmation_interrupt->finish",
                "final_response->finish",
            ),
        )
    )
    return registry
