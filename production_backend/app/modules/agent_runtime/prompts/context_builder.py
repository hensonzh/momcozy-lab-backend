from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextProjection:
    stable_system_prompt: str
    stable_developer_prompt: str
    selected_conversation_history: list[dict[str, Any]] = field(default_factory=list)
    current_state_projection: dict[str, Any] = field(default_factory=dict)
    fresh_business_facts: dict[str, Any] = field(default_factory=dict)


class ModelInputBuilder:
    def build(self, *, projection: ContextProjection, current_user_message: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": projection.stable_system_prompt},
            {"role": "developer", "content": projection.stable_developer_prompt},
            *projection.selected_conversation_history,
            {"role": "developer", "content": {"state": projection.current_state_projection}},
            {"role": "developer", "content": {"business_facts": projection.fresh_business_facts}},
            current_user_message,
        ]
