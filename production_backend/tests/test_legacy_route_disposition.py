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
