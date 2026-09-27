"""Deployment must not silently remove live API operations or narrow clients."""
from copy import deepcopy

from scripts.check_deployed_openapi_compatibility import incompatibilities
from scripts.export_openapi import build_openapi_schema


def test_candidate_matches_current_deployment() -> None:
    schema = build_openapi_schema()
    assert incompatibilities(schema, schema) == []


def test_rejects_removed_live_route_and_method() -> None:
    current = build_openapi_schema()
    candidate = deepcopy(current)
    candidate["paths"].pop("/v1/records/feeding")
    candidate["paths"]["/v1/auth/me"].pop("get")

    errors = incompatibilities(current, candidate)
    assert "Removed live operation: GET /v1/records/feeding" in errors
    assert "Removed live operation: POST /v1/records/feeding" in errors
    assert "Removed live operation: GET /v1/auth/me" in errors


def test_rejects_required_new_input_and_removed_strict_input() -> None:
    current = build_openapi_schema()
    candidate = deepcopy(current)
    # Old clients may not send newly mandatory confirmation fields.
    current["components"]["schemas"]["EmailChallengeRequest"]["required"].remove("confirm_password")
    candidate["components"]["schemas"]["EmailRegisterRequest"]["properties"].pop("password")

    errors = incompatibilities(current, candidate)
    assert any("POST /v1/auth/verify-email" in e and "new required field confirm_password" in e for e in errors)
    assert any("POST /v1/auth/register" in e and "removed accepted field password" in e for e in errors)


def test_additive_operations_and_optional_fields_are_allowed() -> None:
    current = build_openapi_schema()
    candidate = deepcopy(current)
    candidate["paths"]["/v1/optional-feature"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    candidate["components"]["schemas"]["EmailRegisterRequest"]["properties"]["client_version"] = {"type": "string"}
    assert incompatibilities(current, candidate) == []


def test_rejects_removed_required_response_field() -> None:
    current = build_openapi_schema()
    candidate = deepcopy(current)
    response = candidate["components"]["schemas"]["SchedulePageRead"]
    response["properties"].pop("personal")
    response["required"].remove("personal")

    errors = incompatibilities(current, candidate)
    assert any("GET /v1/schedule" in e and "removed response field personal" in e for e in errors)
