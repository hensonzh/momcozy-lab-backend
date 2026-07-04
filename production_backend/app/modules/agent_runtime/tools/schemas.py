from __future__ import annotations

from copy import deepcopy
from typing import Any

from ....core.errors import ApiError


JsonSchema = dict[str, Any]


_TOOL_INPUT_SCHEMAS: dict[str, JsonSchema] = {
    "ProfileContextQuery": {
        "title": "ProfileContextQuery",
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
    "BusinessContextQuery": {
        "title": "BusinessContextQuery",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "Maximum items per business context section.",
            }
        },
    },
    "MilkSummaryQuery": {
        "title": "MilkSummaryQuery",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "default": 7,
                "description": "Number of recent trend days to summarize.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "Maximum recent feeding and pumping records to include.",
            },
        },
    },
    "FeedingRecordProposalCreate": {
        "title": "FeedingRecordProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["feed_time", "feed_type"],
        "properties": {
            "infant_id": {"type": "string", "maxLength": 80},
            "feed_time": {"type": "string", "minLength": 1, "maxLength": 80},
            "feed_type": {"type": "string", "minLength": 1, "maxLength": 32},
            "feed_action": {"type": "string", "maxLength": 32},
            "volume_ml": {"type": "number", "minimum": 0},
            "duration_seconds": {"type": "integer", "minimum": 0},
            "title": {"type": "string", "maxLength": 255},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "PumpingRecordProposalCreate": {
        "title": "PumpingRecordProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["pump_start_time"],
        "properties": {
            "pump_start_time": {"type": "string", "minLength": 1, "maxLength": 80},
            "pump_end_time": {"type": "string", "maxLength": 80},
            "milk_volume_ml": {"type": "number", "minimum": 0},
            "pump_type": {"type": "string", "maxLength": 32},
            "duration_seconds": {"type": "integer", "minimum": 0},
            "source": {"type": "string", "maxLength": 32},
            "title": {"type": "string", "maxLength": 255},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "HospitalBagCartUpdateProposalCreate": {
        "title": "HospitalBagCartUpdateProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["cart_update"],
        "properties": {
            "cart_update": {
                "type": "object",
                "description": "Minimal cart delta to preview and apply after user confirmation.",
                "additionalProperties": True,
            },
            "summary": {
                "type": "string",
                "description": "Concise user-visible description of the cart change.",
                "maxLength": 500,
            },
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
    "SupportTicketProposalCreate": {
        "title": "SupportTicketProposalCreate",
        "type": "object",
        "additionalProperties": False,
        "required": ["issue_summary"],
        "properties": {
            "issue_type": {
                "type": "string",
                "description": "Short category for the issue, such as pump, order, app, or other.",
                "maxLength": 80,
            },
            "issue_summary": {
                "type": "string",
                "description": "Concise user-visible summary of the issue to preview before confirmation.",
                "minLength": 1,
                "maxLength": 500,
            },
            "product_model": {"type": "string", "maxLength": 120},
            "order_number": {"type": "string", "maxLength": 120},
            "purchase_channel": {"type": "string", "maxLength": 120},
            "user_contact": {
                "type": "string",
                "description": "Optional contact detail; shown only as a boolean in the preview payload.",
                "maxLength": 255,
            },
            "urgency": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "locale": {"type": "string", "maxLength": 35},
            "timezone": {"type": "string", "maxLength": 80},
            "payload": {
                "type": "object",
                "additionalProperties": True,
            },
            "idempotency_key": {"type": "string", "maxLength": 255},
        },
    },
}


def tool_input_schema(schema_ref: str) -> JsonSchema:
    schema = _TOOL_INPUT_SCHEMAS.get(schema_ref)
    if schema is None:
        raise ApiError(code="tool_schema_not_found", message="Tool input schema is not registered.", status=500)
    return deepcopy(schema)


def validate_tool_input(*, schema_ref: str, value: dict[str, Any]) -> None:
    schema = tool_input_schema(schema_ref)
    _validate_value(schema=schema, value=value, path="$")


def _validate_value(*, schema: JsonSchema, value: Any, path: str) -> None:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            _raise_invalid(path=path, reason="must be an object")
        _validate_object(schema=schema, value=value, path=path)
        return
    if schema_type == "string":
        _validate_string(schema=schema, value=value, path=path)
        return
    if schema_type == "integer":
        _validate_integer(schema=schema, value=value, path=path)
        return
    if schema_type == "number":
        _validate_number(schema=schema, value=value, path=path)


def _validate_object(*, schema: JsonSchema, value: dict[str, Any], path: str) -> None:
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    missing = [key for key in required if key not in value]
    if missing:
        _raise_invalid(path=path, reason=f"missing required field: {missing[0]}")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            _raise_invalid(path=f"{path}.{extra[0]}", reason="field is not allowed")
    for key, item in value.items():
        child_schema = properties.get(key)
        if isinstance(child_schema, dict):
            _validate_value(schema=child_schema, value=item, path=f"{path}.{key}")


def _validate_string(*, schema: JsonSchema, value: Any, path: str) -> None:
    if not isinstance(value, str):
        _raise_invalid(path=path, reason="must be a string")
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        _raise_invalid(path=path, reason=f"must be at least {min_length} characters")
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        _raise_invalid(path=path, reason=f"must be at most {max_length} characters")
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        _raise_invalid(path=path, reason="has an unsupported value")


def _validate_integer(*, schema: JsonSchema, value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _raise_invalid(path=path, reason="must be an integer")
    minimum = schema.get("minimum")
    if isinstance(minimum, int) and value < minimum:
        _raise_invalid(path=path, reason=f"must be greater than or equal to {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, int) and value > maximum:
        _raise_invalid(path=path, reason=f"must be less than or equal to {maximum}")


def _validate_number(*, schema: JsonSchema, value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _raise_invalid(path=path, reason="must be a number")
    minimum = schema.get("minimum")
    if isinstance(minimum, int | float) and value < minimum:
        _raise_invalid(path=path, reason=f"must be greater than or equal to {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, int | float) and value > maximum:
        _raise_invalid(path=path, reason=f"must be less than or equal to {maximum}")


def _raise_invalid(*, path: str, reason: str) -> None:
    raise ApiError(
        code="tool_input_invalid",
        message="Tool input does not match the registered contract.",
        status=422,
        details={"path": path, "reason": reason},
    )
