from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from ..types import RuntimeInputs

PROJECT_ROOT = Path(__file__).resolve().parents[3]
AIR1_MANUAL_PATH = PROJECT_ROOT / "skills" / "device-guidance" / "references" / "air1" / "manual.md"
AIR1_FAQ_PATH = PROJECT_ROOT / "skills" / "device-guidance" / "references" / "air1" / "faq.md"
WEB_ROOT = PROJECT_ROOT / "web"
SKILLS_ROOT = PROJECT_ROOT / "skills"
MAX_RESULT_CHARS = 2600
AIR1_REFERENCE_KEY = "device-guidance/Air1/references/air1/manual.md"
AIR1_QUICK_START_RESOURCES: tuple[dict[str, str], ...] = (
    {
        "kind": "pdf",
        "title": "Air1 快速上手指南",
        "description": "官方 Quick Start 指导卡片",
        "url": "/skill-assets/device-guidance/air1/quick-start/momcozy-air1-quick-start-guidance.pdf",
    },
    {
        "kind": "video",
        "title": "Air1 中文操作视频",
        "description": "官方中文操作视频",
        "url": "/skill-assets/device-guidance/air1/videos/air1-operation-zh.mp4",
    },
)

def search_device_manual(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    model = _text(args.get("model"), "unknown")
    query = _text(args.get("query"), inputs.get("user_message", ""))
    topic = _text(args.get("topic"))
    max_results = _int(args.get("max_results"), 4)
    if max_results < 1:
        max_results = 1
    if max_results > 6:
        max_results = 6

    model_key = model.lower()
    if model_key not in {"air1", "air 1", "momcozy air1", "momcozy air 1"}:
        return {
            "tool_name": "device_manual_search",
            "status": "unsupported_model",
            "model": model,
            "query": query,
            "topic": topic,
            "manual": None,
            "faq_results": [],
            "results": [],
            "product_highlights": [],
            "quick_start_resources": [],
            "message": "当前只提供 Momcozy Air 1 的本地说明书和 FAQ 内容。",
        }

    manual = _manual_document(AIR1_MANUAL_PATH)
    if manual is None:
        return {
            "tool_name": "device_manual_search",
            "status": "manual_unavailable",
            "model": "Air1",
            "query": query,
            "topic": topic,
            "manual": None,
            "faq_results": [],
            "product_highlights": [],
            "quick_start_resources": [],
            "message": "当前型号的本地说明书未找到。",
        }

    faq_chunks = _faq_chunks(AIR1_FAQ_PATH)
    terms = _query_terms(f"{query} {topic}")
    scored = sorted(
        (
            (_score_chunk(chunk, terms), chunk)
            for chunk in faq_chunks
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    faq_results = []
    top_score = scored[0][0] if scored else 0
    for score, chunk in scored:
        if score <= 0:
            break
        faq_results.append(_format_chunk(chunk))
        if len(faq_results) >= max_results:
            break

    manual_already_loaded = _reference_loaded(inputs, AIR1_REFERENCE_KEY)
    if manual_already_loaded:
        status = "manual_already_loaded_with_faq" if faq_results else "manual_already_loaded"
    else:
        status = "manual_loaded_with_faq" if faq_results else "manual_loaded"

    return {
        "tool_name": "device_manual_search",
        "status": status,
        "model": "Air1",
        "query": query,
        "topic": topic,
        "match_score": top_score,
        "manual": None if manual_already_loaded else manual,
        "loaded_reference": AIR1_REFERENCE_KEY if manual_already_loaded else None,
        "faq_results": faq_results,
        "relevant_images": _relevant_images(manual.get("module_images", {}), query=query, topic=topic),
        "product_highlights": _air1_product_highlights(),
        "quick_start_resources": _air1_quick_start_resources(),
        "usage_guidance": (
            "如果 status 是 manual_already_loaded 或 manual_already_loaded_with_faq，说明当前型号 manual 已在本会话上下文中，不要要求重新加载，直接复用已有 manual。"
            "manual 是当前型号的完整本地官方说明书整理稿，应作为设备步骤的主要事实依据。"
            "faq_results 是按用户问题检索到的相关 FAQ；如果为空，说明没有命中明确 FAQ，但 manual 仍可作为依据。"
            "relevant_images 是按当前 query/topic 预选的步骤图片；讲到对应步骤时，请用 Markdown 图片语法展示最相关图片。"
            "product_highlights 是确认 Air1 后可先给用户看的产品亮点，只能使用其中事实，不要扩写成资料未覆盖的卖点。"
            "quick_start_resources 是 Air1 开箱/首次使用资源；如果需要展示资源，只使用每个资源的 markdown_link 字段，禁止直接展示 url 或 /skill-assets/... 原始路径。"
            "在给出 quick_start_resources 的同一轮，不要直接开始 manual 第一步；只有用户确认需要一步步指导后，才进入首次使用推荐路径。"
            "开箱路径中 guide.parts 至少包含“取出平铺”和“清点核对”两个回合；用户说“好了”通常只代表平铺完成，"
            "不要从 guide.parts 直接跳到 guide.controls，必须先让用户核对可见物品、独立配件和整机状态是否齐全完整。"
            "面向用户的步骤要简短；引导式安装或清洁时，一次只给一步并等待用户确认。"
            "不要补造资料中没有的 Air1 专属说明。"
        ),
    }


def create_support_ticket_draft(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    ticket = {
        "draft_id": f"draft_{uuid.uuid4().hex[:10]}",
        "issue_type": _text(args.get("issue_type"), "other"),
        "issue_summary": _text(args.get("issue_summary"), inputs.get("user_message", "")),
        "product_model": _text(args.get("product_model")),
        "order_number": _text(args.get("order_number")),
        "purchase_channel": _text(args.get("purchase_channel")),
        "user_contact": _text(args.get("user_contact")),
        "troubleshooting_done": _string_list(args.get("troubleshooting_done")),
        "urgency": _text(args.get("urgency"), "normal"),
        "user_emotion": _text(args.get("user_emotion")),
        "attachments_note": _text(args.get("attachments_note")),
        "preferred_language": _text(inputs.get("locale"), "en-US"),
    }
    return {
        "tool_name": "support_ticket_draft_create",
        "status": "ticket_draft_created",
        "ticket": ticket,
        "submit_label": "确认并提交",
    }


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _manual_document(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    cleaned = _drop_missing_image_lines(text.strip())
    return {
        "source": "references/air1/manual.md",
        "title": _first_meaningful_line(cleaned) or "Air1 说明书",
        "content": cleaned,
        "images": _available_images(text),
        "module_images": _module_images(text),
    }


def _air1_product_highlights() -> list[str]:
    return [
        "无线可穿戴吸奶器，可放入内衣中使用。",
        "支持充电盒给主机充电，也支持充电线直充主机。",
        "可通过主机按钮完成开关机、暂停、模式选择和吸力调节。",
        "连接 App 后可选择 Auto、Manual 或 Customize 模式，并调节 15 级吸力。",
    ]


def _air1_quick_start_resources() -> list[dict[str, str]]:
    resources = []
    for resource in AIR1_QUICK_START_RESOURCES:
        if not _static_asset_exists(resource["url"]):
            continue
        item = dict(resource)
        item["markdown_link"] = f"[{item['title']}]({item['url']})"
        resources.append(item)
    return resources


def _reference_loaded(inputs: RuntimeInputs, reference_key: str) -> bool:
    loaded_references = inputs.get("_loaded_references")
    if not isinstance(loaded_references, list):
        return False
    return any(reference_key in str(reference) for reference in loaded_references)


def _module_images(content: str) -> dict[str, list[dict[str, str]]]:
    module_images: dict[str, list[dict[str, str]]] = {}
    current_module = ""
    for line in content.splitlines():
        heading_match = re.match(r"^###\s+(guide\.[a-z_]+)\b", line)
        if heading_match:
            current_module = heading_match.group(1)
            module_images.setdefault(current_module, [])
            continue
        if not current_module:
            continue
        for image in _available_images(line):
            module_images.setdefault(current_module, []).append(image)
    return {module: images for module, images in module_images.items() if images}


def _relevant_images(module_images: dict[str, list[dict[str, str]]], *, query: str, topic: str) -> list[dict[str, str]]:
    normalized = f"{query} {topic}".lower()
    module_order = []
    keyword_modules = [
        (("首次", "第一次", "开箱", "盒内", "配件", "清点"), ["guide.parts"]),
        (("第二步", "第2步", "按钮", "指示灯", "电量灯", "开关"), ["guide.controls"]),
        (("充电", "电量", "电池", "充电舱"), ["guide.charging"]),
        (("拆", "拆卸"), ["guide.disassembly"]),
        (("清洁", "清洗", "消毒"), ["guide.cleaning"]),
        (("法兰", "乳头", "尺寸"), ["guide.flange"]),
        (("组装", "安装", "漏气", "没吸力"), ["guide.assembly"]),
        (("蓝牙", "配网", "连接"), ["guide.bluetooth"]),
        (("app", "控制", "同步"), ["guide.app_control"]),
        (("穿戴", "开机", "吸奶", "吸乳"), ["guide.wearing_start"]),
        (("储奶", "倒奶", "结束"), ["guide.finish_storage"]),
    ]
    for keywords, modules in keyword_modules:
        if any(keyword in normalized for keyword in keywords):
            module_order.extend(modules)
    topic_modules = {
        "unboxing": ["guide.parts", "guide.controls", "guide.charging"],
        "setup": ["guide.parts", "guide.controls", "guide.charging", "guide.assembly", "guide.wearing_start"],
        "overview": ["guide.parts", "guide.controls"],
        "parts": ["guide.parts"],
        "charging": ["guide.charging"],
        "cleaning": ["guide.cleaning", "guide.disassembly"],
        "disinfection": ["guide.cleaning"],
        "assembly": ["guide.assembly", "guide.disassembly"],
        "flange": ["guide.flange"],
        "suction": ["guide.assembly", "guide.flange", "guide.wearing_start"],
        "bluetooth": ["guide.bluetooth", "guide.app_control"],
        "daily_use": ["guide.wearing_start", "guide.app_control", "guide.finish_storage"],
        "milk_storage": ["guide.finish_storage"],
        "troubleshooting": ["guide.assembly", "guide.flange", "guide.charging", "guide.bluetooth"],
    }
    module_order.extend(topic_modules.get(topic.lower(), []))

    seen_modules = set()
    images = []
    for module in module_order:
        if module in seen_modules:
            continue
        seen_modules.add(module)
        for image in module_images.get(module, []):
            images.append({"module": module, **image})
    return images[:4]


def _faq_chunks(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=##\s+)", text)
    chunks = []
    for block in blocks:
        cleaned = block.strip()
        if not cleaned.startswith("## "):
            continue
        title, _, body = cleaned.partition("\n")
        chunks.append({"source": "references/air1/faq.md", "title": title.removeprefix("## ").strip(), "content": body.strip()})
    return chunks


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"^[\s*#-]+", "", line).strip()
        if line:
            return line
    return ""


def _query_terms(query: str) -> list[str]:
    normalized = query.lower()
    terms = re.findall(r"[a-z0-9]+", normalized)
    known_terms = [
        "吸力",
        "没吸力",
        "首次使用",
        "第一次",
        "开箱",
        "清洗",
        "清洁",
        "消毒",
        "充电",
        "电量",
        "充电舱",
        "整机",
        "蓝牙",
        "连接",
        "配网",
        "法兰",
        "乳头",
        "配件",
        "部件",
        "隔膜",
        "鸭嘴阀",
        "阀门",
        "控制",
        "面板",
        "按钮",
        "开关",
        "模式",
        "档位",
        "安装",
        "组装",
        "拆卸",
        "疼",
        "痛",
        "疼痛",
        "漏气",
        "漏奶",
        "储奶",
        "母乳",
        "app",
        "故障",
        "保修",
        "作用",
        "原理",
        "用途",
        "功能",
        "是什么",
        "为什么",
        "能不能",
        "正常吗",
        "区别",
        "真空",
        "系统",
    ]
    terms.extend(term for term in known_terms if term in normalized)
    synonyms = {
        "吸力": ["suction", "真空", "漏气", "档位"],
        "没吸力": ["吸力", "漏气", "组装", "法兰"],
        "首次使用": ["开箱", "整机", "充电舱", "按钮", "充电", "清洁", "组装"],
        "第一次": ["首次使用", "开箱", "充电", "清洁", "组装"],
        "开箱": ["首次使用", "整机", "充电舱", "配件", "部件"],
        "unboxing": ["首次使用", "开箱", "整机", "充电舱", "配件", "部件"],
        "setup": ["首次使用", "开箱", "组装", "清洁", "法兰", "穿戴"],
        "overview": ["简介", "产品简介", "基础认知"],
        "daily_use": ["穿戴", "开机", "日常", "使用", "吸乳"],
        "清洗": ["清洁", "消毒", "水洗"],
        "cleaning": ["清洁", "清洗", "消毒", "水洗"],
        "消毒": ["清洁", "煮沸", "微波"],
        "disinfection": ["消毒", "清洁", "煮沸", "微波"],
        "充电": ["电量", "电池", "适配器", "充电舱"],
        "charging": ["充电", "电量", "电池", "适配器", "充电舱"],
        "充电舱": ["充电", "电量", "主机"],
        "整机": ["组装", "拆卸", "配件", "部件"],
        "蓝牙": ["配网", "app", "连接"],
        "bluetooth": ["蓝牙", "配网", "app", "连接"],
        "法兰": ["乳头", "尺寸", "insert", "flange"],
        "flange": ["法兰", "乳头", "尺寸", "转换件"],
        "suction": ["吸力", "真空", "漏气", "档位"],
        "配件": ["部件", "组件", "parts"],
        "parts": ["配件", "部件", "组件"],
        "隔膜": ["diaphragm", "真空", "系统", "母乳", "通道", "隔离"],
        "diaphragm": ["隔膜", "真空", "系统", "母乳", "通道", "隔离"],
        "鸭嘴阀": ["阀门", "配件", "集乳"],
        "阀门": ["鸭嘴阀", "配件", "集乳"],
        "控制": ["面板", "按钮", "开关", "模式"],
        "面板": ["控制", "按钮", "开关", "模式"],
        "按钮": ["控制", "面板", "开关", "模式"],
        "开关": ["按钮", "控制", "暂停"],
        "模式": ["刺激", "吸乳", "混合"],
        "档位": ["吸力", "级别"],
        "安装": ["组装", "拆卸"],
        "组装": ["安装", "拆卸", "漏气"],
        "assembly": ["组装", "安装", "拆卸", "漏气"],
        "troubleshooting": ["故障", "排查", "没吸力", "漏气", "错误"],
        "milk_storage": ["储奶", "母乳", "倒奶"],
        "faq": ["常见问题", "问题", "FAQ"],
        "作用": ["原理", "用途", "功能", "是什么"],
        "原理": ["作用", "用途", "功能", "真空"],
        "用途": ["作用", "功能", "是什么"],
        "功能": ["作用", "用途", "是什么"],
        "是什么": ["作用", "用途", "功能"],
        "为什么": ["原因", "原理", "作用"],
        "能不能": ["可以", "是否", "支持"],
        "正常吗": ["正常", "是否", "问题"],
        "区别": ["不同", "差异", "对比"],
        "疼": ["疼痛", "不适", "法兰", "吸力"],
        "痛": ["疼痛", "不适", "法兰", "吸力"],
    }
    expanded = list(terms)
    for term in list(terms):
        expanded.extend(synonyms.get(term, []))
    return [term for term in expanded if term]


def _score_chunk(chunk: dict[str, Any], terms: list[str]) -> int:
    title = str(chunk.get("title", "")).lower()
    content = str(chunk.get("content", "")).lower()
    haystack = f"{title}\n{content}"
    score = 0
    for term in terms:
        normalized_term = term.lower()
        score += haystack.count(normalized_term)
        if normalized_term in title:
            score += 4
    return score


def _format_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    content = _trim_content(_drop_missing_image_lines(str(chunk["content"])))
    images = _available_images(str(chunk["content"]))
    return {
        "source": chunk["source"],
        "title": chunk["title"],
        "content": content,
        "images": images,
    }


def _trim_content(content: str) -> str:
    if len(content) <= MAX_RESULT_CHARS:
        return content
    return f"{content[:MAX_RESULT_CHARS].rstrip()}\n..."


def _drop_missing_image_lines(content: str) -> str:
    lines = []
    for line in content.splitlines():
        image_match = re.search(r"!\[[^\]]*\]\(([^)]+)\)", line)
        if image_match and not _static_image_exists(image_match.group(1)):
            continue
        lines.append(line)
    return "\n".join(lines)


def _available_images(content: str) -> list[dict[str, str]]:
    images = []
    for alt, url in re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", content):
        if _static_image_exists(url):
            images.append({"alt": alt, "url": url})
    return images


def _static_image_exists(url: str) -> bool:
    return _static_asset_exists(url)


def _static_asset_exists(url: str) -> bool:
    if url.startswith("/images/"):
        image_path = (WEB_ROOT / url.lstrip("/")).resolve()
        web_root = WEB_ROOT.resolve()
        return web_root in image_path.parents and image_path.exists()
    if url.startswith("/skill-assets/"):
        relative_path = url.removeprefix("/skill-assets/")
        skill_id, _, asset_name = relative_path.partition("/")
        if not skill_id or not asset_name:
            return False
        image_path = (SKILLS_ROOT / skill_id / "assets" / asset_name).resolve()
        skill_assets_root = (SKILLS_ROOT / skill_id / "assets").resolve()
        return skill_assets_root in image_path.parents and image_path.exists()
    return False
