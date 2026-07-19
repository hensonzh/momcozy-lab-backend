from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=120)
    description: str = ""
    input_schema: dict[str, Any]
    loading_mode: Literal["eager", "deferred"] = "deferred"
    read_or_write: Literal["read", "write"]
    side_effect_level: Literal["none", "low", "medium", "high"]
    blocking_policy: Literal["must_wait", "enqueue_and_continue", "wait_for_confirmation"]
    result_dependency: Literal["none", "final_response", "next_tool_call", "resource_id"]
    requires_confirmation: bool
    idempotency_required: bool
    audit_required: bool
    timeout_seconds: int = Field(default=30, ge=1, le=300)
