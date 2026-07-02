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
