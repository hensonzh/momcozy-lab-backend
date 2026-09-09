import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from scripts.export_openapi import build_openapi_schema


SMOKE_FLOWS = Path(__file__).resolve().parents[1] / "docs" / "flutter-smoke-flows.json"
TEMPLATE_VARIABLE_RE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
PUBLIC_STEPS = {
    ("POST", "/v1/auth/signup"),
    ("POST", "/v1/auth/login"),
    ("POST", "/v1/auth/invite-login"),
    ("POST", "/v1/auth/refresh"),
}
SENSITIVE_QUERY_NAMES = {"token", "access_token", "refresh_token", "service_key", "api_key"}


def test_flutter_smoke_flows_cover_public_product_contracts() -> None:
    payload = json.loads(SMOKE_FLOWS.read_text())
    flow_names = {flow["name"] for flow in payload["flows"]}

    assert flow_names == {"auth_session", "core_records_schedule_files", "voice_contract"}


def test_flutter_smoke_flows_do_not_put_tokens_in_urls() -> None:
    payload = json.loads(SMOKE_FLOWS.read_text())

    assert payload["rules"]["no_tokens_in_urls"] is True
    for flow in payload["flows"]:
        for step in flow["steps"]:
            query_names = {name for name, _value in parse_qsl(urlsplit(step["path"]).query, keep_blank_values=True)}
            assert SENSITIVE_QUERY_NAMES.isdisjoint(query_names)


def test_flutter_smoke_flows_use_idempotency_for_retryable_writes() -> None:
    payload = json.loads(SMOKE_FLOWS.read_text())
    retryable_operations = _openapi_operations_with_header("Idempotency-Key")

    for flow in payload["flows"]:
        for step in flow["steps"]:
            operation = (step["method"], _openapi_path(step["path"]))
            if operation in retryable_operations:
                assert "${idempotency_header}" in step.get("headers", [])


def test_flutter_smoke_flow_steps_match_openapi_paths_and_methods() -> None:
    payload = json.loads(SMOKE_FLOWS.read_text())
    schema_paths = build_openapi_schema()["paths"]

    for flow in payload["flows"]:
        for step in flow["steps"]:
            openapi_path = _openapi_path(step["path"])
            assert openapi_path in schema_paths
            assert step["method"].lower() in schema_paths[openapi_path]


def test_flutter_smoke_flows_use_only_user_auth_boundary() -> None:
    payload = json.loads(SMOKE_FLOWS.read_text())

    for flow in payload["flows"]:
        for step in flow["steps"]:
            operation = (step["method"], _openapi_path(step["path"]))
            headers = set(step.get("headers", []))
            assert "${service_header}" not in headers
            if operation in PUBLIC_STEPS:
                assert "${auth_header}" not in headers
            else:
                assert "${auth_header}" in headers


def _openapi_path(path: str) -> str:
    return TEMPLATE_VARIABLE_RE.sub(lambda match: "{" + match.group(1) + "}", urlsplit(path).path)


def _openapi_operations_with_header(header_name: str) -> set[tuple[str, str]]:
    operations: set[tuple[str, str]] = set()
    for path, path_item in build_openapi_schema()["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            headers = {parameter["name"] for parameter in operation.get("parameters", []) if parameter.get("in") == "header"}
            if header_name in headers:
                operations.add((method.upper(), path))
    return operations
