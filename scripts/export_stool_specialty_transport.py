"""Export the structured independent specialty transport DTO snapshot."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.stool_analyses.transport_contract import SpecialtyAnalysisInput, SpecialtyTransportEvent  # noqa: E402

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "docs/contracts/stool-specialty-transport.v1.json"


def build_catalog() -> dict[str, Any]:
    return {
        "status": "structured_stool_result_v1",
        "contracts": {
            "analysis_input": SpecialtyAnalysisInput.model_json_schema(),
            "analysis_event": SpecialtyTransportEvent.model_json_schema(),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_catalog(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
