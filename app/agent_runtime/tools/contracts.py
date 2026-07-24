from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    domain: str = Field(min_length=1, max_length=120)
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    effect_scope: Literal["none", "agent_internal", "user_resource", "external_resource"]
    timeout_seconds: int = Field(default=30, ge=1, le=300)
