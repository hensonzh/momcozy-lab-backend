from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUTPUT = Path("docs/openapi.generated.json")


def build_openapi_schema(*, app_env: str = "test") -> dict[str, Any]:
    from app.core.settings import Settings
    from app.factory import create_app

    app = create_app(Settings(app_env=app_env))
    return app.openapi()


def write_openapi_schema(*, output: Path, app_env: str = "test") -> None:
    schema = build_openapi_schema(app_env=app_env)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the production backend OpenAPI schema.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--app-env", default="test")
    args = parser.parse_args()

    write_openapi_schema(output=args.output, app_env=args.app_env)


if __name__ == "__main__":
    main()
