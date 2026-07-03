import json
from pathlib import Path

from production_backend.scripts.export_openapi import build_openapi_schema


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "production_backend" / "docs" / "backend-refactor-inventory.generated.md"
DISPOSITION = ROOT / "production_backend" / "docs" / "legacy-route-disposition.json"


def test_legacy_route_disposition_covers_inventory_paths() -> None:
    inventory_paths = _inventory_paths()
    routes = _disposition_routes()

    assert inventory_paths <= set(routes)
    assert set(routes) <= inventory_paths


def test_legacy_route_disposition_targets_match_openapi() -> None:
    openapi_paths = set(build_openapi_schema()["paths"])

    for route in _disposition_routes().values():
        assert route["status"] in {"mapped", "retired", "pending"}
        assert route["target_domain"]
        assert route["notes"]

        if route["status"] == "mapped":
            assert route["target_paths"]
            assert set(route["target_paths"]) <= openapi_paths
        if route["status"] == "retired":
            assert not route["target_paths"]
            assert route["legacy_path"] not in openapi_paths


def test_pump_fsm_retirement_and_workstate_contract_are_explicit() -> None:
    routes = _disposition_routes()

    assert routes["/v1/pump/workstate"]["status"] == "mapped"
    assert routes["/v1/pump/workstate"]["target_paths"] == ["/v1/devices/pump-workstate"]
    for retired_path in [
        "/v1/pump/workstate/pending-replies",
        "/v1/pump/process",
        "/v1/pump/process/data",
        "/v1/pump/session-summary",
    ]:
        route = routes[retired_path]
        assert route["status"] == "retired"
        assert route["target_paths"] == []
        assert "retired" in route["notes"].lower()

    pump_contract = (ROOT / "production_backend" / "docs" / "pr-slices" / "pump-device-contract.md").read_text()
    assert "Status: accepted production contract" in pump_contract
    assert "latest projections for workstate, threshold, and health" in pump_contract
    assert "old pump process/FSM and pending-reply behavior is retired" in pump_contract


def _inventory_paths() -> set[str]:
    paths: set[str] = set()
    for line in INVENTORY.read_text().splitlines():
        if not line.startswith("|") or "`/" not in line:
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 2:
            continue
        path = columns[1].strip("`")
        if path.startswith("/"):
            paths.add(path)
    return paths


def _disposition_routes() -> dict[str, dict]:
    payload = json.loads(DISPOSITION.read_text())
    assert payload["schema_version"] == "legacy_route_disposition.v1"
    routes = payload["routes"]
    paths = [route["legacy_path"] for route in routes]

    assert len(paths) == len(set(paths))
    return {route["legacy_path"]: route for route in routes}
