#!/usr/bin/env python3
"""Fail a Product Backend release before it silently breaks deployed clients.

This checks the *live* public OpenAPI against the committed candidate, not two
snapshots from the same source tree. Removing legacy routes requires an explicit
migration/retirement decision and a separate compatibility plan.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _resolve(document: dict[str, Any], schema: Any) -> dict[str, Any]:
    seen: set[str] = set()
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"]
        if ref in seen or not ref.startswith("#/components/schemas/"):
            return {}
        seen.add(ref)
        components = document.get("components", {})
        schemas = components.get("schemas", {}) if isinstance(components, dict) else {}
        schema = schemas.get(ref.rsplit("/", 1)[-1]) if isinstance(schemas, dict) else None
    return schema if isinstance(schema, dict) else {}


def _body(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    payload = operation.get("requestBody", {})
    content = payload.get("content", {}) if isinstance(payload, dict) else {}
    json_type = content.get("application/json", {}) if isinstance(content, dict) else {}
    return _resolve(document, json_type.get("schema") if isinstance(json_type, dict) else {})


def _success_response(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return {}
    for status, response in sorted(responses.items()):
        if not status.startswith("2") or not isinstance(response, dict):
            continue
        content = response.get("content", {})
        json_type = content.get("application/json", {}) if isinstance(content, dict) else {}
        if isinstance(json_type, dict) and "schema" in json_type:
            return _resolve(document, json_type["schema"])
    return {}


def _parameters(operation: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    params = operation.get("parameters", [])
    return {
        (p["in"], p["name"]): p
        for p in params if isinstance(p, dict) and p.get("in") in {"query", "header", "path"} and isinstance(p.get("name"), str)
    } if isinstance(params, list) else {}


def incompatibilities(current: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """Return conservative, actionable breaking changes from live to candidate."""
    errors: list[str] = []
    old_paths = current.get("paths", {})
    new_paths = candidate.get("paths", {})
    if not isinstance(old_paths, dict) or not isinstance(new_paths, dict):
        return ["Both OpenAPI documents must have a paths object"]
    for path, methods in sorted(old_paths.items()):
        if not isinstance(methods, dict):
            continue
        new_methods = new_paths.get(path, {})
        for method, old_op in sorted(methods.items()):
            if method not in HTTP_METHODS or not isinstance(old_op, dict):
                continue
            label = f"{method.upper()} {path}"
            new_op = new_methods.get(method) if isinstance(new_methods, dict) else None
            if not isinstance(new_op, dict):
                errors.append(f"Removed live operation: {label}")
                continue

            old_body, new_body = _body(current, old_op), _body(candidate, new_op)
            old_props = old_body.get("properties", {})
            new_props = new_body.get("properties", {})
            if isinstance(old_props, dict) and isinstance(new_props, dict):
                old_required = set(old_body.get("required", []))
                new_required = set(new_body.get("required", []))
                for field in sorted(new_required - old_required):
                    errors.append(f"{label}: new required field {field}")
                if new_body.get("additionalProperties") is False:
                    for field in sorted(old_props.keys() - new_props.keys()):
                        errors.append(f"{label}: removed accepted field {field}")
            old_response = _success_response(current, old_op)
            new_response = _success_response(candidate, new_op)
            if old_response and not new_response:
                errors.append(f"{label}: removed JSON success response")
            elif old_response:
                old_fields = old_response.get("properties", {})
                new_fields = new_response.get("properties", {})
                if isinstance(old_fields, dict) and isinstance(new_fields, dict):
                    for field in sorted(old_fields.keys() - new_fields.keys()):
                        errors.append(f"{label}: removed response field {field}")
                    old_required = set(old_response.get("required", []))
                    new_required = set(new_response.get("required", []))
                    for field in sorted(old_required - new_required):
                        if field in new_fields:
                            errors.append(f"{label}: response field {field} is no longer guaranteed")
            old_params, new_params = _parameters(old_op), _parameters(new_op)
            for key, parameter in sorted(new_params.items()):
                if parameter.get("required") and not old_params.get(key, {}).get("required"):
                    errors.append(f"{label}: newly required {key[0]} parameter {key[1]}")
            for key in sorted(old_params.keys() - new_params.keys()):
                if key[0] == "query":
                    errors.append(f"{label}: removed accepted query parameter {key[1]}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    current = json.loads(args.current.read_text())
    candidate = json.loads(args.candidate.read_text())
    errors = incompatibilities(current, candidate)
    if errors:
        for error in errors:
            print(f"OpenAPI compatibility: {error}")
        print(f"BLOCKED: {len(errors)} breaking changes; coordinate supported clients and migration before deployment.")
        return 1
    print("OpenAPI compatibility: no removed operations or newly mandatory inputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
