from __future__ import annotations

from typing import Any

from ..skills import (
    list_skill_manifests,
    load_skill as load_skill_definition,
    read_skill_file as read_skill_file_definition,
    run_approved_skill_script as run_approved_skill_script_definition,
    search_skill_assets as search_skill_assets_definition,
)
from ..types import RuntimeInputs


def list_skills(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    return {"skills": list_skill_manifests()}


def load_skill(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    return load_skill_definition(args["skill_id"])


def search_skill_assets(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    return search_skill_assets_definition(args["skill_id"], args["query"])


def read_skill_file(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    return read_skill_file_definition(args["skill_id"], args["kind"], args["path"])


def run_approved_skill_script(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    return run_approved_skill_script_definition(args["skill_id"], args["script_name"], args.get("args"))
