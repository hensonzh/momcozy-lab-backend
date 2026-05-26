from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .types import SkillDefinition, SkillId

SERVICE_SKILL_IDS: tuple[SkillId, ...] = (
    "birth-prep",
    "milk-management",
    "emotion-support",
    "device-guidance",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "skills"
READABLE_SKILL_FILE_KINDS = ("references", "assets")
READABLE_SKILL_FILE_SUFFIXES = {".md", ".txt", ".json", ".csv"}
MAX_SKILL_FILE_BYTES = 100_000

ScriptHandler = Callable[[dict[str, Any]], dict[str, Any]]
APPROVED_SCRIPT_HANDLERS: dict[tuple[SkillId, str], ScriptHandler] = {}

_FRONTMATTER_DELIMITER = "---"
_MANIFEST_CACHE: dict[SkillId, SkillDefinition] = {}


def get_skill(skill_id: SkillId) -> SkillDefinition:
    if skill_id not in SERVICE_SKILL_IDS:
        raise ValueError(f"Unknown skill id: {skill_id!r}")

    cached = _MANIFEST_CACHE.get(skill_id)
    if cached is not None:
        return cached

    manifest = _read_skill_manifest(skill_id)
    _MANIFEST_CACHE[skill_id] = manifest
    return manifest


def list_skill_manifests() -> list[dict[str, Any]]:
    return [dict(get_skill(skill_id)) for skill_id in SERVICE_SKILL_IDS]


def load_skill(skill_id: SkillId) -> dict[str, Any]:
    if skill_id not in SERVICE_SKILL_IDS:
        raise ValueError(f"Skill {skill_id!r} is not loadable through the public skill runtime.")

    skill_dir = SKILLS_ROOT / skill_id
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"Missing SKILL.md for {skill_id}.")

    manifest = get_skill(skill_id)
    return {
        "id": skill_id,
        "name": manifest["name"],
        "description": manifest["description"],
        "skill_md": skill_file.read_text(encoding="utf-8"),
        "references": _list_child_files(skill_dir / "references"),
        "scripts": _list_child_files(skill_dir / "scripts"),
        "assets": _list_child_files(skill_dir / "assets"),
    }


def search_skill_assets(skill_id: SkillId, query: str) -> dict[str, Any]:
    loaded = load_skill(skill_id)
    normalized = query.lower()
    matches = []
    for kind in ("references", "scripts", "assets"):
        for path in loaded[kind]:
            if normalized in path.lower():
                matches.append({"kind": kind, "path": path})
    return {"skill_id": skill_id, "query": query, "matches": matches}


def read_skill_file(skill_id: SkillId, kind: str, path: str) -> dict[str, Any]:
    if kind not in READABLE_SKILL_FILE_KINDS:
        raise ValueError("Only reference and asset files can be read through read_skill_file.")

    loaded = load_skill(skill_id)
    if path not in loaded[kind]:
        raise ValueError(f"File {path!r} is not available in {kind} for {skill_id}.")

    file_path = (SKILLS_ROOT / skill_id / path).resolve()
    skill_dir = (SKILLS_ROOT / skill_id).resolve()
    if skill_dir not in file_path.parents:
        raise ValueError("Resolved skill file path is outside the skill directory.")

    if file_path.suffix.lower() not in READABLE_SKILL_FILE_SUFFIXES:
        raise ValueError(f"Unsupported skill file type: {file_path.suffix or '<none>'}.")

    if file_path.stat().st_size > MAX_SKILL_FILE_BYTES:
        raise ValueError(f"Skill file is too large to load: {path}.")

    return {
        "skill_id": skill_id,
        "kind": kind,
        "path": path,
        "content": file_path.read_text(encoding="utf-8"),
    }


def register_approved_skill_script(skill_id: SkillId, script_name: str, handler: ScriptHandler) -> None:
    loaded = load_skill(skill_id)
    if script_name not in loaded["scripts"]:
        raise ValueError(f"Script {script_name!r} is not available for {skill_id}.")
    APPROVED_SCRIPT_HANDLERS[(skill_id, script_name)] = handler


def run_approved_skill_script(skill_id: SkillId, script_name: str, args: dict[str, Any] | str | None = None) -> dict[str, Any]:
    loaded = load_skill(skill_id)
    if script_name not in loaded["scripts"]:
        raise ValueError(f"Script {script_name!r} is not available for {skill_id}.")

    normalized_args = _parse_script_args(args)
    handler = APPROVED_SCRIPT_HANDLERS.get((skill_id, script_name))
    if handler is None:
        return {
            "skill_id": skill_id,
            "script_name": script_name,
            "args": normalized_args,
            "status": "not_approved",
            "reason": "The script is bundled with the skill, but no application-side allowlist handler is registered.",
        }

    result = handler(normalized_args)
    return {
        "skill_id": skill_id,
        "script_name": script_name,
        "args": normalized_args,
        "status": "executed",
        "result": result,
    }


def execute_skill_runtime_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "list_skills":
        return {"skills": list_skill_manifests()}
    if name == "load_skill":
        return load_skill(arguments["skill_id"])
    if name == "search_skill_assets":
        return search_skill_assets(arguments["skill_id"], arguments["query"])
    if name == "read_skill_file":
        return read_skill_file(arguments["skill_id"], arguments["kind"], arguments["path"])
    if name == "run_approved_skill_script":
        return run_approved_skill_script(arguments["skill_id"], arguments["script_name"], arguments.get("args"))
    raise ValueError(f"Unknown skill runtime tool: {name}")


def reload_skill_manifests() -> None:
    """Drop the in-memory frontmatter cache so the next read re-parses SKILL.md files."""
    _MANIFEST_CACHE.clear()


def _read_skill_manifest(skill_id: SkillId) -> SkillDefinition:
    skill_file = SKILLS_ROOT / skill_id / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"Missing SKILL.md for {skill_id}.")

    front = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))

    description = front.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"SKILL.md for {skill_id} is missing a non-empty 'description'.")

    name_value = front.get("name")
    name = name_value if isinstance(name_value, str) and name_value.strip() else skill_id

    manifest: SkillDefinition = {
        "id": skill_id,
        "name": name,
        "description": description.strip(),
    }
    return manifest


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse the YAML-like frontmatter block at the top of a SKILL.md file.

    The supported subset is intentionally small: a leading and trailing ``---``
    delimiter, ``key: value`` pairs for scalar strings, and ``key:`` followed by
    indented ``- item`` lines for string lists. This is enough for skill manifest
    metadata without pulling in a full YAML dependency.
    """

    lines = content.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        raise ValueError("SKILL.md must start with a '---' frontmatter delimiter.")

    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_DELIMITER:
            end_index = index
            break
    if end_index is None:
        raise ValueError("SKILL.md frontmatter is not closed by '---'.")

    result: dict[str, Any] = {}
    current_list: list[str] | None = None

    for raw_line in lines[1:end_index]:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        if raw_line.startswith((" ", "\t")):
            stripped = raw_line.strip()
            if not stripped.startswith("- "):
                raise ValueError(f"Unsupported indented frontmatter line: {raw_line!r}")
            if current_list is None:
                raise ValueError(f"List item without a preceding key: {raw_line!r}")
            current_list.append(_strip_scalar(stripped[2:]))
            continue

        if ":" not in raw_line:
            raise ValueError(f"Invalid frontmatter line: {raw_line!r}")

        key, _, value = raw_line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Frontmatter key is empty: {raw_line!r}")

        if not value:
            current_list = []
            result[key] = current_list
        else:
            result[key] = _strip_scalar(value)
            current_list = None

    return result


def _strip_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _list_child_files(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(str(file.relative_to(path.parent)) for file in path.rglob("*") if file.is_file())


def _parse_script_args(args: dict[str, Any] | str | None) -> dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    parsed = json.loads(args)
    if not isinstance(parsed, dict):
        raise ValueError("Script args must be a JSON object.")
    return parsed
