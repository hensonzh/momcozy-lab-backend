from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.modules.profiles.lactation_context_schema import LactationContextReadOutput


JsonSchema = dict[str, Any]


_TOOL_OUTPUT_SCHEMAS: dict[str, JsonSchema] = {
    "lactation_context_read": LactationContextReadOutput.model_json_schema(),
}


def output_schema_for_tool(tool_name: str) -> JsonSchema | None:
    schema = _TOOL_OUTPUT_SCHEMAS.get(tool_name)
    return deepcopy(schema) if schema is not None else None
