from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.modules.profiles.lactation_context_schema import (
    MaternalInfantProfileReadOutput,
    MaternalInfantProfileUpdateOutput,
)
from app.modules.records.lactation_timeline_schema import (
    LactationTimelineManageOutput,
    LactationTimelineReadOutput,
)
from app.modules.records.milk_analysis_schema import MilkAnalysisOutput


JsonSchema = dict[str, Any]


_TOOL_OUTPUT_SCHEMAS: dict[str, JsonSchema] = {
    "lactation_timeline_manage": LactationTimelineManageOutput.model_json_schema(),
    "lactation_timeline_read": LactationTimelineReadOutput.model_json_schema(),
    "milk_analysis": MilkAnalysisOutput.model_json_schema(),
    "maternal_infant_profile_read": MaternalInfantProfileReadOutput.model_json_schema(),
    "maternal_infant_profile_update": MaternalInfantProfileUpdateOutput.model_json_schema(),
}


def output_schema_for_tool(tool_name: str) -> JsonSchema | None:
    schema = _TOOL_OUTPUT_SCHEMAS.get(tool_name)
    return deepcopy(schema) if schema is not None else None
