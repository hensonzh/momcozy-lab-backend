from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    domain: str = Field(min_length=1, max_length=120)
    description: str = ""
    input_schema: dict[str, Any]
    loading_mode: Literal["eager", "deferred"] = "deferred"
    effect_scope: Literal["none", "agent_internal", "user_resource", "external_resource"]
    action_type: str | None = Field(default=None, min_length=1, max_length=120)
    blocking_policy: Literal["must_wait", "enqueue_and_continue", "wait_for_confirmation"]
    result_dependency: Literal["none", "final_response", "next_tool_call", "resource_id"]
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def validate_action_boundary(self) -> "ToolContract":
        action_backed = self.effect_scope in {"user_resource", "external_resource"}
        if action_backed and self.action_type is None:
            raise ValueError("Business and external resource tools must declare action_type.")
        if not action_backed and self.action_type is not None:
            raise ValueError("Only business and external resource tools may declare action_type.")
        return self
