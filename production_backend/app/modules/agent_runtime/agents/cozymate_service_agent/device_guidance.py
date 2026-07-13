from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from production_backend.app.core.errors import ApiError


_DEVICE_GUIDANCE_ROOT = Path(__file__).resolve().parent / "skills" / "device-guidance" / "references" / "air1"
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_GUIDE_HEADING_PATTERN = re.compile(r"^###\s+(guide\.[A-Za-z0-9_-]+)\s+(.+?)\s*$")
_FAQ_HEADING_PATTERN = re.compile(r"^##\s+\d+\.\s+(.+?)\s*$")
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
_TOPIC_FAQ_QUERIES = {
    "suction": "吸力减小 低吸力 没有母乳流出",
    "troubleshooting": "吸奶器不工作 没有母乳流出 吸力减小",
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
DEVICE_ELECTRICAL_HAZARD_RESPONSE = (
    "先立即停止使用。若设备正在充电，并且可以安全接近，请先从墙上插座拔下电源适配器，再断开设备；"
    "不要继续通电、充电或使用，也不要自行拆机或更换电池。把设备放到远离易燃物的位置，等它完全冷却。"
    "这个情况需要升级产品支持，我可以帮你创建售后工单，客服团队会在 24 小时内联系你。需要我现在帮你创建吗？"
)
_DEVICE_ELECTRICAL_HAZARD_TERMS = (
    "冒烟",
    "烧焦味",
    "焦糊味",
    "烧焦气味",
    "电火花",
    "漏电",
    "电池鼓包",
    "电池膨胀",
    "充电线破损",
    "充电线开裂",
    "充电线断裂",
    "适配器破损",
    "适配器开裂",
    "异常发热",
    "烫手",
)
_DEVICE_HAZARD_CLAUSE_BOUNDARY = re.compile(r"[。！？!?；;，,]|但是|不过|\b(?:but|however|yet)\b", re.IGNORECASE)
_DEVICE_HAZARD_NEGATION = re.compile(r"(?:没有|没|无|未见|并未|不)(?:出现|闻到|发现|感觉|是)?\s*$")


def device_electrical_hazard_response(text: str) -> str:
    normalized = re.sub(r"\s+", "", str(text or "").strip().lower())
    for term in _DEVICE_ELECTRICAL_HAZARD_TERMS:
        for match in re.finditer(re.escape(term), normalized):
            prefix = normalized[: match.start()]
            boundaries = list(_DEVICE_HAZARD_CLAUSE_BOUNDARY.finditer(prefix))
            clause_prefix = prefix[boundaries[-1].end() :] if boundaries else prefix
            if not _DEVICE_HAZARD_NEGATION.search(clause_prefix[-24:]):
                return DEVICE_ELECTRICAL_HAZARD_RESPONSE
    return ""


@dataclass(frozen=True)
class _GuideSection:
    step_id: str
    title: str
    content: str
    completion_condition: str
    image_labels: tuple[str, ...]


class DeviceGuidanceReferenceService:
    def __init__(self, *, root: Path = _DEVICE_GUIDANCE_ROOT) -> None:
        self.root = root.resolve()

    def read(
        self,
        *,
        model: str,
        topic: str = "",
        step: str = "",
        query: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        normalized_model = _normalize_model(model)
        if normalized_model != "air1":
            raise ApiError(code="unsupported_device_model", message="Only Air1/BP334 guidance is currently available.", status=422)
        normalized_topic = str(topic or "").strip().lower()
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

        faq_query = query or _TOPIC_FAQ_QUERIES.get(normalized_topic, "")
        if normalized_topic == "faq" or faq_query and not normalized_step:
            result["faq_matches"] = self._faq_matches(query=faq_query, limit=limit)
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
            image_labels = tuple(label.strip() for label in _MARKDOWN_IMAGE_PATTERN.findall("\n".join(body_lines)) if label.strip())
            clean_lines = [line for line in body_lines if not _MARKDOWN_IMAGE_PATTERN.search(line)]
            clean_lines = [line for line in clean_lines if line.strip() != "图片："]
            content = "\n".join(clean_lines).strip()
            sections[step_id] = _GuideSection(
                step_id=step_id,
                title=title,
                content=content[:8000],
                completion_condition=_completion_condition(clean_lines),
                image_labels=image_labels,
            )
        return sections

    @cached_property
    def faq_entries(self) -> tuple[dict[str, str], ...]:
        lines = self._read_text("faq.md").splitlines()
        headings: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
            match = _FAQ_HEADING_PATTERN.match(line)
            if match:
                headings.append((index, match.group(1).strip()))
        entries: list[dict[str, str]] = []
        for position, (start, question) in enumerate(headings):
            end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            answer = "\n".join(lines[start + 1 : end]).strip()
            if answer:
                entries.append({"question": question[:300], "answer": answer[:1600]})
        return tuple(entries)

    @cached_property
    def document_version(self) -> str:
        digest = hashlib.sha256()
        digest.update(self._read_text("manual.md").encode("utf-8"))
        digest.update(self._read_text("faq.md").encode("utf-8"))
        return f"air1-{digest.hexdigest()[:12]}"

    def _faq_matches(self, *, query: str, limit: int) -> list[dict[str, str]]:
        bounded_limit = max(1, min(int(limit), 10))
        query_terms = _search_terms(query)
        if not query_terms:
            return [dict(item) for item in self.faq_entries[:bounded_limit]]
        ranked: list[tuple[int, int, dict[str, str]]] = []
        for index, item in enumerate(self.faq_entries):
            item_terms = _search_terms(f"{item['question']} {item['answer']}")
            score = len(query_terms & item_terms)
            if score:
                ranked.append((score, -index, item))
        ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return [dict(item) for _score, _index, item in ranked[:bounded_limit]]

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


def _search_terms(value: str) -> set[str]:
    normalized = re.sub(r"\s+", "", str(value or "").lower())
    latin_terms = set(re.findall(r"[a-z0-9]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    chinese_terms = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    return latin_terms | chinese_terms
