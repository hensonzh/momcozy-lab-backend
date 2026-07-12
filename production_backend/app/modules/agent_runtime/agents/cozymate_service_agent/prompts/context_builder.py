from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextProjection:
    stable_system_prompt: str
    selected_conversation_history: list[dict[str, Any]] = field(default_factory=list)
    user_context: dict[str, Any] = field(default_factory=dict)
    memory_projection: list[dict[str, Any]] = field(default_factory=list)
    working_context: dict[str, Any] = field(
        default_factory=lambda: {
            "skills": [],
            "ongoing_work": [],
            "known_information": [],
        }
    )


class ModelInputBuilder:
    def build(self, *, projection: ContextProjection, current_user_message: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            *projection.selected_conversation_history,
            {
                "role": "developer",
                "content": {
                    "runtime_context": {
                        "user_context": projection.user_context,
                        "memory": projection.memory_projection,
                        "working_context": projection.working_context,
                    }
                },
            },
            current_user_message,
        ]
