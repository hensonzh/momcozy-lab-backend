from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .service_skills import ServiceSkillId


SERVICE_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
SERVICE_SKILL_FILE_NAME = "SKILL.md"
REQUIRED_METADATA_KEYS = frozenset({"name", "description"})
REQUIRED_SECTION_TITLES = ("服务范围", "回复风格", "服务流程", "交付物", "工具策略", "边界")
SERVICE_SKILL_ORDER = (
    ServiceSkillId.BIRTH_PREP.value,
    ServiceSkillId.MILK_MANAGEMENT.value,
    ServiceSkillId.DEVICE_GUIDANCE.value,
)


@dataclass(frozen=True)
class AgentServiceSkill:
    name: str
    description: str
    id: str
    version: str
    service_skill_id: str
    role: str
    scope: tuple[str, ...]
    style_rules: tuple[str, ...]
    service_flows: tuple[str, ...]
    deliverables: tuple[str, ...]
    tool_rules: tuple[str, ...]
    boundaries: tuple[str, ...]
    body: str
    source_path: Path

    def prompt_block(self) -> str:
        return self.body

    def state_summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "service_skill_id": self.service_skill_id,
            "scope": list(self.scope),
            "deliverables": list(self.deliverables),
        }


class AgentServiceSkillRegistry:
    def __init__(self, skills: tuple[AgentServiceSkill, ...], default_skill_id: str) -> None:
        self._skills = {skill.service_skill_id: skill for skill in skills}
        self._ordered_skills = skills
        self._default_skill_id = default_skill_id
        if len(self._skills) != len(skills):
            raise ValueError("duplicate agent service skill service_skill_id")
        if default_skill_id not in self._skills:
            raise ValueError("default agent service skill is not registered")

    def get(self, skill_id: str) -> AgentServiceSkill:
        return self._skills[skill_id]

    def list(self) -> tuple[AgentServiceSkill, ...]:
        return self._ordered_skills

    def default(self) -> AgentServiceSkill:
        return self.get(self._default_skill_id)


def default_service_skill_registry() -> AgentServiceSkillRegistry:
    skills = _load_default_service_skills()
    return AgentServiceSkillRegistry(skills=skills, default_skill_id=ServiceSkillId.BIRTH_PREP.value)


def _load_default_service_skills() -> tuple[AgentServiceSkill, ...]:
    skill_paths = tuple(sorted(SERVICE_SKILLS_ROOT.glob(f"*/{SERVICE_SKILL_FILE_NAME}")))
    if not skill_paths:
        raise ValueError(f"no agent service skills found under {SERVICE_SKILLS_ROOT}")
    skills = tuple(load_service_skill(path) for path in skill_paths)
    by_service_skill_id = {skill.service_skill_id: skill for skill in skills}
    expected_ids = set(SERVICE_SKILL_ORDER)
    actual_ids = set(by_service_skill_id)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise ValueError(f"agent service skill set mismatch; missing={missing}, extra={extra}")
    return tuple(by_service_skill_id[skill_id] for skill_id in SERVICE_SKILL_ORDER)


def load_service_skill(path: Path) -> AgentServiceSkill:
    metadata, body = _split_frontmatter(path.read_text(encoding="utf-8"), path=path)
    _validate_metadata(metadata=metadata, path=path)
    service_skill_id = metadata.get("service_skill_id") or metadata.get("name") or path.parent.name
    role = _parse_role(body=body, path=path, fallback=metadata["description"])
    sections = {title: _parse_section_items(body=body, title=title, path=path) for title in REQUIRED_SECTION_TITLES}
    return AgentServiceSkill(
        name=metadata["name"],
        description=metadata["description"],
        id=metadata.get("id") or service_skill_id,
        version=metadata.get("version") or "legacy",
        service_skill_id=service_skill_id,
        role=role,
        scope=sections["服务范围"],
        style_rules=sections["回复风格"],
        service_flows=sections["服务流程"],
        deliverables=sections["交付物"],
        tool_rules=sections["工具策略"],
        boundaries=sections["边界"],
        body=body,
        source_path=path,
    )


def _split_frontmatter(raw: str, *, path: Path) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        raise ValueError(f"{path} must start with YAML-style frontmatter")
    marker_index = raw.find("\n---", 4)
    if marker_index == -1:
        raise ValueError(f"{path} frontmatter is not closed")
    header = raw[4:marker_index].strip()
    body = raw[marker_index + 4 :].strip()
    metadata: dict[str, str] = {}
    for line in header.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        key, separator, value = normalized.partition(":")
        if not separator:
            raise ValueError(f"{path} frontmatter line is missing ':'")
        metadata[key.strip()] = value.strip().strip('"')
    if not body:
        raise ValueError(f"{path} body is empty")
    return metadata, body


def _validate_metadata(*, metadata: dict[str, str], path: Path) -> None:
    missing = sorted(key for key in REQUIRED_METADATA_KEYS if not metadata.get(key))
    if missing:
        raise ValueError(f"{path} is missing required frontmatter keys: {missing}")
    service_skill_id = metadata.get("service_skill_id") or metadata.get("name") or path.parent.name
    try:
        ServiceSkillId(service_skill_id)
    except ValueError as exc:
        raise ValueError(f"{path} has unsupported service_skill_id: {service_skill_id}") from exc


def _parse_role(*, body: str, path: Path, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("角色定位："):
            role = line.removeprefix("角色定位：").strip()
            if role:
                return role
    return fallback


def _parse_section_items(*, body: str, title: str, path: Path) -> tuple[str, ...]:
    lines = body.splitlines()
    header = f"## {title}"
    items: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == header:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return tuple(items)
