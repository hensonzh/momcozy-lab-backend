from __future__ import annotations

from dataclasses import dataclass

from .prompts import SERVICE_SKILL_PROMPT_ORDER, SERVICE_SKILL_PROMPTS
from .routing.schemas import ServiceSkillId


REQUIRED_METADATA_KEYS = frozenset({"name", "description", "id", "version", "service_skill_id"})
REQUIRED_SECTION_TITLES = ("服务范围", "回复风格", "服务流程", "交付物", "工具策略", "边界")
SERVICE_SKILL_ORDER = SERVICE_SKILL_PROMPT_ORDER


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
    source: str

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
    return AgentServiceSkillRegistry(skills=skills, default_skill_id=ServiceSkillId.GENERAL.value)


def _load_default_service_skills() -> tuple[AgentServiceSkill, ...]:
    if not SERVICE_SKILL_PROMPTS:
        raise ValueError("no agent service skill prompts found in prompts.catalog.SERVICE_SKILL_PROMPTS")
    skills = tuple(load_service_skill_prompt(skill_id=skill_id, raw=SERVICE_SKILL_PROMPTS[skill_id]) for skill_id in SERVICE_SKILL_ORDER)
    by_service_skill_id = {skill.service_skill_id: skill for skill in skills}
    expected_ids = set(SERVICE_SKILL_ORDER)
    actual_ids = set(by_service_skill_id)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise ValueError(f"agent service skill set mismatch; missing={missing}, extra={extra}")
    return tuple(by_service_skill_id[skill_id] for skill_id in SERVICE_SKILL_ORDER)


def load_service_skill_prompt(*, skill_id: str, raw: str) -> AgentServiceSkill:
    source = f"prompts.catalog.SERVICE_SKILL_PROMPTS[{skill_id}]"
    metadata, body = _split_frontmatter(raw, source=source)
    _validate_metadata(metadata=metadata, expected_skill_id=skill_id, source=source)
    role = _parse_role(body=body, source=source)
    sections = {title: _parse_section_items(body=body, title=title, source=source) for title in REQUIRED_SECTION_TITLES}
    return AgentServiceSkill(
        name=metadata["name"],
        description=metadata["description"],
        id=metadata["id"],
        version=metadata["version"],
        service_skill_id=metadata["service_skill_id"],
        role=role,
        scope=sections["服务范围"],
        style_rules=sections["回复风格"],
        service_flows=sections["服务流程"],
        deliverables=sections["交付物"],
        tool_rules=sections["工具策略"],
        boundaries=sections["边界"],
        body=body,
        source=source,
    )


def _split_frontmatter(raw: str, *, source: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        raise ValueError(f"{source} must start with YAML-style frontmatter")
    marker_index = raw.find("\n---", 4)
    if marker_index == -1:
        raise ValueError(f"{source} frontmatter is not closed")
    header = raw[4:marker_index].strip()
    body = raw[marker_index + 4 :].strip()
    metadata: dict[str, str] = {}
    for line in header.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        key, separator, value = normalized.partition(":")
        if not separator:
            raise ValueError(f"{source} frontmatter line is missing ':'")
        metadata[key.strip()] = value.strip().strip('"')
    if not body:
        raise ValueError(f"{source} body is empty")
    return metadata, body


def _validate_metadata(*, metadata: dict[str, str], expected_skill_id: str, source: str) -> None:
    missing = sorted(key for key in REQUIRED_METADATA_KEYS if not metadata.get(key))
    if missing:
        raise ValueError(f"{source} is missing required frontmatter keys: {missing}")
    try:
        ServiceSkillId(metadata["service_skill_id"])
    except ValueError as exc:
        raise ValueError(f"{source} has unsupported service_skill_id: {metadata['service_skill_id']}") from exc
    if metadata["service_skill_id"] != expected_skill_id:
        raise ValueError(f"{source} service_skill_id must be {expected_skill_id}")


def _parse_role(*, body: str, source: str) -> str:
    for line in body.splitlines():
        if line.startswith("角色定位："):
            role = line.removeprefix("角色定位：").strip()
            if role:
                return role
    raise ValueError(f"{source} must include a non-empty 角色定位 line")


def _parse_section_items(*, body: str, title: str, source: str) -> tuple[str, ...]:
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
    if not items:
        raise ValueError(f"{source} must include at least one bullet under {header}")
    return tuple(items)
