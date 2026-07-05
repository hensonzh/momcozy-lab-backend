from __future__ import annotations

from typing import Any

from ..types import RuntimeInputs


def generate_handoff_summary(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    return {
        "tool_name": "handoff_summary_generate",
        "summary": _handoff_summary(args),
        "status": "generated",
    }


def _handoff_summary(args: dict[str, Any]) -> str:
    issue_type = args.get("issue_type", "unspecified issue")
    facts = args.get("facts", {})
    if isinstance(facts, dict) and facts:
        fact_text = "; ".join(f"{key}: {value}" for key, value in facts.items())
        return f"{issue_type}: {fact_text}"
    return str(issue_type)

