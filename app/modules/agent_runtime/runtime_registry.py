from __future__ import annotations

from dataclasses import dataclass

from ...core.errors import ApiError


SDK_ONLY_RUNTIME_PATTERN = "sdk_only"
DEFAULT_RUNTIME_VERSION = "momcozy-agent-v1"


@dataclass(frozen=True)
class AgentRuntimeDefinition:
    version: str
    runtime_pattern: str


class AgentRuntimeRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, AgentRuntimeDefinition] = {}

    def register(self, definition: AgentRuntimeDefinition) -> None:
        if definition.runtime_pattern != SDK_ONLY_RUNTIME_PATTERN:
            raise ApiError(code="validation_failed", message="Agent runtime must use the sdk_only pattern.", status=422)
        self._definitions[definition.version] = definition

    def get(self, version: str) -> AgentRuntimeDefinition:
        definition = self._definitions.get(version)
        if definition is None:
            raise ApiError(code="not_found", message="Agent runtime version not found.", status=404)
        return definition


def default_runtime_registry() -> AgentRuntimeRegistry:
    registry = AgentRuntimeRegistry()
    registry.register(
        AgentRuntimeDefinition(
            version=DEFAULT_RUNTIME_VERSION,
            runtime_pattern=SDK_ONLY_RUNTIME_PATTERN,
        )
    )
    return registry
