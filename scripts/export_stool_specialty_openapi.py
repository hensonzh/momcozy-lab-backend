"""Deterministic structured network contract for the independent service."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUTPUT = Path("docs/contracts/stool-specialty.openapi.v1.json")


def build_schema() -> dict[str, Any]:
    from services.stool_specialty.app import create_app
    from services.stool_specialty.settings import SpecialtySettings

    # OpenAPI construction does not enable clinical analysis.
    settings = SpecialtySettings(
        app_env="test",
        market="US", service_shared_secret="schema-only-secret-with-32-bytes-minimum",
    )
    return create_app(settings).openapi()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
