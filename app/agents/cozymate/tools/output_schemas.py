from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.modules.profiles.lactation_context_schema import (
    MaternalInfantProfileReadOutput,
    MaternalInfantProfileUpdateOutput,
)
from app.modules.plans.schedule_timeline_schema import (
    ScheduleTimelineReadOutput,
    ScheduleTimelineMutateOutput,
)
from app.modules.plans.plan_tool_schema import PlanMutateOutput, PlanReadOutput
from app.modules.records.milk_analysis_schema import MilkAnalysisOutput
from app.modules.diary.tool_schema import DiaryMutateOutput, DiaryReadOutput


JsonSchema = dict[str, Any]

_CANONICAL_OBJECT_OUTPUT_SCHEMA: JsonSchema = {
    "type": "object",
    "minProperties": 1,
}


_TOOL_OUTPUT_SCHEMAS: dict[str, JsonSchema] = {
    "schedule_timeline_read": ScheduleTimelineReadOutput.model_json_schema(),
    "schedule_timeline_mutate": ScheduleTimelineMutateOutput.model_json_schema(),
    "plan_read": PlanReadOutput.model_json_schema(),
    "plan_mutate": PlanMutateOutput.model_json_schema(),
    "milk_analysis_manage": MilkAnalysisOutput.model_json_schema(),
    "profile_read": MaternalInfantProfileReadOutput.model_json_schema(),
    "profile_update": MaternalInfantProfileUpdateOutput.model_json_schema(),
    "diary_read": DiaryReadOutput.model_json_schema(),
    "diary_mutate": DiaryMutateOutput.model_json_schema(),
    "devices_guidance_manage": _CANONICAL_OBJECT_OUTPUT_SCHEMA,
    "conversation_history_image_read": {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "image_url", "detail", "agent_instruction"],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["image_context_ready"],
            },
            "image_url": {
                "type": "string",
                "minLength": 1,
            },
            "detail": {
                "type": "string",
                "enum": ["low", "high"],
            },
            "agent_instruction": {
                "type": "string",
                "minLength": 1,
                "description": "模型理解该历史图片时必须遵循的范围约束。",
            },
            "asset_id": {
                "type": "string",
                "minLength": 1,
            },
            "content_type": {
                "type": "string",
                "minLength": 1,
            },
        },
    },
    "pregnancy_intake_manage": _CANONICAL_OBJECT_OUTPUT_SCHEMA,
    "hospital_bag_manage": _CANONICAL_OBJECT_OUTPUT_SCHEMA,
    "hospital_bag_cart_mutate": _CANONICAL_OBJECT_OUTPUT_SCHEMA,
    "ibclc_consult_card_create": _CANONICAL_OBJECT_OUTPUT_SCHEMA,
    "support_ticket_draft_create": _CANONICAL_OBJECT_OUTPUT_SCHEMA,
    "pump_models_read": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "status",
            "currency",
            "count",
            "products",
            "source_urls",
        ],
        "properties": {
            "schema_version": {
                "type": "string",
                "enum": ["pump-models.result.v1"],
            },
            "status": {
                "type": "string",
                "enum": ["models_ready"],
            },
            "currency": {
                "type": "string",
                "enum": ["USD"],
            },
            "count": {
                "type": "integer",
                "minimum": 0,
            },
            "products": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "sku_id",
                        "model",
                        "name",
                        "official_price",
                        "sale_price",
                        "tier",
                        "use_cases",
                        "preference_tags",
                        "best_for",
                        "features",
                        "suction",
                        "battery",
                        "weight",
                        "noise",
                        "app_supported",
                        "single_unit_available",
                        "image_url",
                        "source_url",
                    ],
                    "properties": {
                        "sku_id": {"type": "string"},
                        "model": {"type": "string"},
                        "name": {"type": "string"},
                        "official_price": {"type": "number", "minimum": 0},
                        "sale_price": {
                            "type": ["number", "null"],
                            "minimum": 0,
                        },
                        "tier": {"type": "string"},
                        "use_cases": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "preference_tags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "best_for": {"type": "string"},
                        "features": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "suction": {"type": "string"},
                        "battery": {"type": "string"},
                        "weight": {"type": ["string", "null"]},
                        "noise": {"type": "string"},
                        "app_supported": {"type": "boolean"},
                        "single_unit_available": {"type": "boolean"},
                        "image_url": {"type": "string"},
                        "source_url": {"type": "string"},
                    },
                },
            },
            "source_urls": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    },
}


def output_schema_for_tool(tool_name: str) -> JsonSchema:
    schema = _TOOL_OUTPUT_SCHEMAS.get(tool_name)
    if schema is None:
        raise KeyError(f"Tool output schema is not registered: {tool_name}")
    return deepcopy(schema)
