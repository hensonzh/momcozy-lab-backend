from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from app.core.errors import ApiError


JsonSchema = dict[str, Any]


@dataclass(frozen=True)
class _ValidationContext:
    root_schema: JsonSchema
    error_code: str
    message: str
    status: int


def validate_tool_input(*, schema: JsonSchema, value: dict[str, Any]) -> None:
    _validate_value(
        context=_ValidationContext(
            root_schema=schema,
            error_code="tool_input_invalid",
            message="Tool input does not match the registered contract.",
            status=422,
        ),
        schema=schema,
        value=value,
        path="$",
    )


def validate_tool_output(*, schema: JsonSchema | None, value: dict[str, Any]) -> None:
    if schema is None:
        return
    _validate_value(
        context=_ValidationContext(
            root_schema=schema,
            error_code="tool_output_invalid",
            message="Tool output does not match the registered contract.",
            status=500,
        ),
        schema=schema,
        value=value,
        path="$",
    )


def _validate_value(
    *,
    context: _ValidationContext,
    schema: JsonSchema,
    value: Any,
    path: str,
) -> None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        resolved = _resolve_local_reference(context=context, reference=reference, path=path)
        _validate_value(context=context, schema=resolved, value=value, path=path)
        return

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        failures: list[ApiError] = []
        for option in any_of:
            if not isinstance(option, dict):
                continue
            try:
                _validate_value(context=context, schema=option, value=value, path=path)
            except ApiError as exc:
                failures.append(exc)
            else:
                return
        if failures:
            raise failures[0]
        _raise_invalid(context=context, path=path, reason="does not match any allowed schema")

    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        _raise_invalid(context=context, path=path, reason="has an unsupported value")

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        _validate_value(
            context=context,
            schema={**schema, "anyOf": [{"type": item} for item in schema_type], "type": None},
            value=value,
            path=path,
        )
        return
    if schema_type == "object":
        if not isinstance(value, dict):
            _raise_invalid(context=context, path=path, reason="must be an object")
        _validate_object(context=context, schema=schema, value=value, path=path)
        return
    if schema_type == "string":
        _validate_string(context=context, schema=schema, value=value, path=path)
        return
    if schema_type == "integer":
        _validate_integer(context=context, schema=schema, value=value, path=path)
        return
    if schema_type == "number":
        _validate_number(context=context, schema=schema, value=value, path=path)
        return
    if schema_type == "array":
        _validate_array(context=context, schema=schema, value=value, path=path)
        return
    if schema_type == "boolean":
        _validate_boolean(context=context, value=value, path=path)
        return
    if schema_type == "null" and value is not None:
        _raise_invalid(context=context, path=path, reason="must be null")


def _validate_object(
    *,
    context: _ValidationContext,
    schema: JsonSchema,
    value: dict[str, Any],
    path: str,
) -> None:
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required") or []
    if not isinstance(required, list):
        required = []
    missing = [key for key in required if key not in value]
    if missing:
        _raise_invalid(context=context, path=path, reason=f"missing required field: {missing[0]}")
    min_properties = schema.get("minProperties")
    if isinstance(min_properties, int) and len(value) < min_properties:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must include at least {min_properties} fields",
        )
    max_properties = schema.get("maxProperties")
    if isinstance(max_properties, int) and len(value) > max_properties:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must include at most {max_properties} fields",
        )
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            _raise_invalid(
                context=context,
                path=f"{path}.{extra[0]}",
                reason="field is not allowed",
            )
    for key, item in value.items():
        child_schema = properties.get(key)
        if isinstance(child_schema, dict):
            _validate_value(
                context=context,
                schema=child_schema,
                value=item,
                path=f"{path}.{key}",
            )


def _validate_string(
    *,
    context: _ValidationContext,
    schema: JsonSchema,
    value: Any,
    path: str,
) -> None:
    if not isinstance(value, str):
        _raise_invalid(context=context, path=path, reason="must be a string")
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must be at least {min_length} characters",
        )
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must be at most {max_length} characters",
        )
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and re.search(pattern, value) is None:
        _raise_invalid(context=context, path=path, reason="has an invalid format")
    _validate_string_format(
        context=context,
        schema_format=schema.get("format"),
        value=value,
        path=path,
    )


def _validate_integer(
    *,
    context: _ValidationContext,
    schema: JsonSchema,
    value: Any,
    path: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _raise_invalid(context=context, path=path, reason="must be an integer")
    minimum = schema.get("minimum")
    if isinstance(minimum, int | float) and value < minimum:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must be greater than or equal to {minimum}",
        )
    maximum = schema.get("maximum")
    if isinstance(maximum, int | float) and value > maximum:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must be less than or equal to {maximum}",
        )


def _validate_number(
    *,
    context: _ValidationContext,
    schema: JsonSchema,
    value: Any,
    path: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _raise_invalid(context=context, path=path, reason="must be a number")
    minimum = schema.get("minimum")
    if isinstance(minimum, int | float) and value < minimum:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must be greater than or equal to {minimum}",
        )
    maximum = schema.get("maximum")
    if isinstance(maximum, int | float) and value > maximum:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must be less than or equal to {maximum}",
        )


def _validate_array(
    *,
    context: _ValidationContext,
    schema: JsonSchema,
    value: Any,
    path: str,
) -> None:
    if not isinstance(value, list):
        _raise_invalid(context=context, path=path, reason="must be an array")
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must include at least {min_items} items",
        )
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        _raise_invalid(
            context=context,
            path=path,
            reason=f"must include at most {max_items} items",
        )
    item_schema = schema.get("items")
    if isinstance(item_schema, dict) and item_schema:
        for index, item in enumerate(value):
            _validate_value(
                context=context,
                schema=item_schema,
                value=item,
                path=f"{path}[{index}]",
            )


def _validate_boolean(*, context: _ValidationContext, value: Any, path: str) -> None:
    if not isinstance(value, bool):
        _raise_invalid(context=context, path=path, reason="must be a boolean")


def _resolve_local_reference(
    *,
    context: _ValidationContext,
    reference: str,
    path: str,
) -> JsonSchema:
    if not reference.startswith("#/"):
        _raise_invalid(context=context, path=path, reason="uses an unsupported schema reference")
    current: Any = context.root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            _raise_invalid(context=context, path=path, reason="uses an unknown schema reference")
        current = current[part]
    if not isinstance(current, dict):
        _raise_invalid(context=context, path=path, reason="uses an invalid schema reference")
    return cast(JsonSchema, current)


def _validate_string_format(
    *,
    context: _ValidationContext,
    schema_format: Any,
    value: str,
    path: str,
) -> None:
    try:
        if schema_format == "date":
            date.fromisoformat(value)
        elif schema_format == "date-time":
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                raise ValueError
        elif schema_format == "uuid":
            UUID(value)
    except ValueError:
        _raise_invalid(context=context, path=path, reason=f"must be a valid {schema_format}")


def _raise_invalid(*, context: _ValidationContext, path: str, reason: str) -> None:
    raise ApiError(
        code=context.error_code,
        message=context.message,
        status=context.status,
        details={"path": path, "reason": reason},
    )
