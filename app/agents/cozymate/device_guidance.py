from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from app.core.errors import ApiError


_DEVICE_GUIDANCE_ROOT = Path(__file__).resolve().parent / "skills" / "device-guidance" / "references" / "air1"
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
_GUIDE_HEADING_PATTERN = re.compile(r"^###\s+(guide\.[A-Za-z0-9_-]+)\s+(.+?)\s*$")
_TOPIC_DEFAULT_STEPS = {
    "unboxing": "guide.parts",
    "setup": "guide.parts",
    "assembly": "guide.assembly",
    "cleaning": "guide.cleaning",
    "disinfection": "guide.cleaning",
    "charging": "guide.charging",
    "bluetooth": "guide.bluetooth",
    "flange": "guide.flange",
}
AIR1_UNBOXING_STEPS = (
    "guide.parts",
    "guide.controls",
    "guide.charging",
    "guide.disassembly",
    "guide.cleaning",
    "guide.flange",
    "guide.assembly",
    "guide.wearing_start",
    "guide.bluetooth",
    "guide.finish_storage",
)


@dataclass(frozen=True)
class _GuideSection:
    step_id: str
    title: str
    content: str
    completion_condition: str
    image_labels: tuple[str, ...]
    image_urls: tuple[str, ...]


class DeviceGuidanceReferenceService:
    def __init__(self, *, root: Path = _DEVICE_GUIDANCE_ROOT) -> None:
        self.root = root.resolve()

    def read(
        self,
        *,
        model: str,
        topic: str = "",
        step: str = "",
    ) -> dict[str, Any]:
        normalized_model = _normalize_model(model)
        if normalized_model != "air1":
            raise ApiError(code="unsupported_device_model", message="Only Air1/BP334 guidance is currently available.", status=422)
        normalized_topic = str(topic or "").strip().lower()
        if normalized_topic and normalized_topic not in _TOPIC_DEFAULT_STEPS:
            raise ApiError(code="device_guidance_topic_not_found", message="Device guidance topic was not found.", status=404)
        normalized_step = str(step or "").strip()
        if not normalized_step:
            normalized_step = _TOPIC_DEFAULT_STEPS.get(normalized_topic, "")

        result: dict[str, Any] = {
            "device_model": "Air1",
            "document_version": self.document_version,
            "guide_outline": list(self.guide_sections),
        }
        if normalized_step:
            section = self.guide_sections.get(normalized_step)
            if section is None:
                raise ApiError(code="device_guidance_step_not_found", message="Device guidance step was not found.", status=404)
            result["current_step"] = {
                "id": section.step_id,
                "title": section.title,
                "content": section.content,
                "completion_condition": section.completion_condition,
                "image_labels": list(section.image_labels),
            }

        return result

    @cached_property
    def guide_sections(self) -> dict[str, _GuideSection]:
        raw = self._read_text("manual.md")
        headings: list[tuple[int, str, str]] = []
        lines = raw.splitlines()
        for index, line in enumerate(lines):
            match = _GUIDE_HEADING_PATTERN.match(line)
            if match:
                headings.append((index, match.group(1), match.group(2).strip()))
        sections: dict[str, _GuideSection] = {}
        for position, (start, step_id, title) in enumerate(headings):
            end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            body_lines = lines[start + 1 : end]
            image_references = _MARKDOWN_IMAGE_PATTERN.findall("\n".join(body_lines))
            image_labels = tuple(label.strip() for label, _url in image_references if label.strip())
            image_urls = tuple(url.strip() for _label, url in image_references if url.strip())
            clean_lines = [line for line in body_lines if not _MARKDOWN_IMAGE_PATTERN.search(line)]
            clean_lines = [line for line in clean_lines if line.strip() != "图片："]
            content = "\n".join(clean_lines).strip()
            sections[step_id] = _GuideSection(
                step_id=step_id,
                title=title,
                content=content[:8000],
                completion_condition=_completion_condition(clean_lines),
                image_labels=image_labels,
                image_urls=image_urls,
            )
        return sections

    def image_urls_for_step(self, step: str) -> tuple[str, ...]:
        section = self.guide_sections.get(str(step or "").strip())
        return section.image_urls if section is not None else ()

    @cached_property
    def document_version(self) -> str:
        digest = hashlib.sha256()
        digest.update(self._read_text("manual.md").encode("utf-8"))
        return f"air1-{digest.hexdigest()[:12]}"

    def _read_text(self, file_name: str) -> str:
        path = (self.root / file_name).resolve()
        if path.parent != self.root or not path.is_file():
            raise ApiError(code="device_guidance_reference_missing", message="Device guidance reference is unavailable.", status=500)
        return path.read_text(encoding="utf-8")


def _normalize_model(model: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", str(model or "").lower())
    return "air1" if normalized in {"air1", "airone", "bp334"} else normalized


def _completion_condition(lines: list[str]) -> str:
    in_progression = False
    conditions: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "对话推进：":
            in_progression = True
            continue
        if in_progression and stripped.endswith("：") and not stripped.startswith("-"):
            break
        if in_progression and stripped.startswith("-"):
            conditions.append(stripped.removeprefix("-").strip())
    if conditions:
        return " ".join(conditions)[-1600:]
    return "用户确认已完成当前步骤中的全部动作和检查点。"
