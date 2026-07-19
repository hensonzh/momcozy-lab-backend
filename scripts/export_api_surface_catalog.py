from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.surface import (  # noqa: E402
    API_CLIENT_FIELD,
    API_NOTES_FIELD,
    API_OWNER_FIELD,
    API_STABILITY_FIELD,
    API_SURFACE_FIELD,
)
from scripts.export_openapi import build_openapi_schema  # noqa: E402


DEFAULT_OUTPUT = Path("docs/api-surface-catalog.md")
SURFACE_ORDER = [
    "public_app_api",
    "runtime_stream_api",
    "internal_service_api",
    "admin_ops_api",
    "infra_probe_api",
    "deprecated_api",
]
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


def render_api_surface_catalog(schema: dict[str, Any]) -> str:
    grouped: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
    for path, path_item in sorted(schema.get("paths", {}).items()):
        if not isinstance(path_item, dict):
            continue
        for method, operation in sorted(path_item.items()):
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            surface = str(operation.get(API_SURFACE_FIELD, "unclassified"))
            grouped[surface].append((method.upper(), path, operation))

    lines = [
        "# MomCozy API Surface Catalog",
        "",
        "This catalog is generated from `docs/openapi.generated.json`.",
        "The source of truth is each FastAPI route's OpenAPI extension metadata.",
        "",
        "| Surface | Meaning |",
        "|---|---|",
        "| `public_app_api` | Stable API called by the Flutter app. |",
        "| `runtime_stream_api` | App-facing streaming or realtime transport contract. |",
        "| `internal_service_api` | Backend service or worker API; not a direct app contract. |",
        "| `admin_ops_api` | Operations, replay, eval, or support tooling API. |",
        "| `infra_probe_api` | Health, readiness, metrics, or infrastructure probe API. |",
        "| `deprecated_api` | Still present but scheduled for removal. |",
        "",
    ]

    for surface in [*SURFACE_ORDER, *sorted(set(grouped) - set(SURFACE_ORDER))]:
        operations = grouped.get(surface, [])
        if not operations:
            continue
        lines.extend(
            [
                f"## {surface}",
                "",
                "| Method | Path | Owner | Clients | Stability | Summary |",
                "|---|---|---|---|---|---|",
            ]
        )
        for method, path, operation in operations:
            clients = operation.get(API_CLIENT_FIELD, [])
            client_text = ", ".join(str(client) for client in clients) if isinstance(clients, list) else str(clients)
            summary = str(operation.get("summary") or "")
            notes = str(operation.get(API_NOTES_FIELD) or "")
            if notes:
                summary = f"{summary} {notes}".strip()
            lines.append(
                "| "
                + " | ".join(
                    [
                        method,
                        f"`{path}`",
                        str(operation.get(API_OWNER_FIELD, "")),
                        client_text,
                        str(operation.get(API_STABILITY_FIELD, "")),
                        summary,
                    ]
                )
                + " |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_api_surface_catalog(*, output: Path, app_env: str = "test", openapi_input: Path | None = None) -> None:
    if openapi_input is None:
        schema = build_openapi_schema(app_env=app_env)
    else:
        schema = json.loads(openapi_input.read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_api_surface_catalog(schema))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a human-readable API surface catalog from OpenAPI metadata.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--app-env", default="test")
    parser.add_argument("--openapi-input", type=Path, default=None)
    args = parser.parse_args()

    write_api_surface_catalog(output=args.output, app_env=args.app_env, openapi_input=args.openapi_input)


if __name__ == "__main__":
    main()
