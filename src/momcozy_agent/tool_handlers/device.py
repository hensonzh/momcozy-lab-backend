from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from ..types import RuntimeInputs

PROJECT_ROOT = Path(__file__).resolve().parents[3]
AIR1_MANUAL_PATH = PROJECT_ROOT / "skills" / "device-guidance" / "references" / "air1" / "manual.md"
AIR1_FAQ_PATH = PROJECT_ROOT / "skills" / "device-guidance" / "references" / "air1" / "faq.md"
SKILLS_ROOT = PROJECT_ROOT / "skills"
LEGACY_AIR1_FAQ_IMAGE_ROOT = SKILLS_ROOT / "device-guidance" / "assets" / "air1" / "faq-images"
MAX_RESULT_CHARS = 2600
AIR1_REFERENCE_KEY = "device-guidance/Air1/references/air1/manual.md"
AIR1_IMAGE_TEXT_BY_URL: dict[str, str] = {
    "/skill-assets/device-guidance/air1/images/air1_guide_parts_components.png": (
        "Air1 核心部件编号清单："
        "编号1=24mm Flange Pump x2；"
        "编号2=Wireless Charging Case x1；"
        "编号3=Flange Cover x2；"
        "编号4=USB Type-C Cable x1；"
        "编号5=Magnetic Charging Cable x1；"
        "编号6=17mm Flange Insert x2；"
        "编号7=19mm Flange Insert x2；"
        "编号8=21mm Flange Insert x2；"
        "编号9=Spare Valve x2；"
        "编号10=Flange Size Ruler x1；"
        "编号11=Quick Start Guide x1；"
        "编号12=User Manual x1。"
    ),
    "/skill-assets/device-guidance/air1/images/air1_guide_controls_button_indicator.png": (
        "Air1 主机按钮与指示灯编号清单："
        "编号1=Mode Selection / 模式选择键；"
        "编号2=Decrease Suction Level / 降低吸力键；"
        "编号3=Increase Suction Level / 增加吸力键；"
        "编号4=On / Off; Pause / Continue / 开关机、暂停、继续键；"
        "编号5=Indicator Light / 指示灯。"
        "指示灯：白灯常亮=电量满；红灯常亮=低电量；绿灯闪烁=充电中；绿灯常亮=已充满。"
    ),
}
AIR1_INCLUDED_FLANGE_INSERTS_MM = {17, 19, 21}
AIR1_FLANGE_SIZE_RANGES: tuple[dict[str, Any], ...] = (
    {"min_mm": 11.0, "max_mm": 13.0, "range_label": "11-13mm", "recommended_mm": 15, "accessory_type": "flange_insert"},
    {"min_mm": 13.0, "max_mm": 15.0, "range_label": "13-15mm", "recommended_mm": 17, "accessory_type": "flange_insert"},
    {"min_mm": 15.0, "max_mm": 17.0, "range_label": "15-17mm", "recommended_mm": 19, "accessory_type": "flange_insert"},
    {"min_mm": 17.0, "max_mm": 20.0, "range_label": "17-20mm", "recommended_mm": 21, "accessory_type": "flange_insert"},
    {"min_mm": 20.0, "max_mm": 23.0, "range_label": "20-23mm", "recommended_mm": 24, "accessory_type": "base_flange"},
    {"min_mm": 23.0, "max_mm": 26.0, "range_label": "23-26mm", "recommended_mm": 27, "accessory_type": "flange_insert"},
    {"min_mm": 26.0, "max_mm": 29.0, "range_label": "26-29mm", "recommended_mm": 30, "accessory_type": "flange_insert"},
)
AIR1_QUICK_START_RESOURCES: tuple[dict[str, str], ...] = (
    {
        "kind": "pdf",
        "title": "Air1 快速上手指南",
        "description": "官方 Quick Start 指导卡片",
        "url": "/skill-assets/device-guidance/air1/quick-start/momcozy-air1-quick-start-guidance.pdf",
        "voice_policy": "describe_on_request",
        "spoken_label": "这里有 Air1 官方快速上手指南，需要时可以打开对照。",
    },
    {
        "kind": "video",
        "title": "Air1 中文操作视频",
        "description": "官方中文操作视频",
        "url": "/skill-assets/device-guidance/air1/videos/air1-operation-zh.mp4",
        "voice_policy": "describe_on_request",
        "spoken_label": "这里有 Air1 官方中文操作视频，需要时可以打开查看。",
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
    measured_nipple_mm = _measured_nipple_mm(
        args.get("measured_nipple_mm"),
        query=query,
        user_message=str(inputs.get("user_message") or ""),
        topic=topic,
    )

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
            "flange_recommendation": None,
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
            "flange_recommendation": None,
            "product_highlights": [],
            "quick_start_resources": [],
            "message": "当前型号的本地说明书未找到。",
        }

    flange_recommendation = _air1_flange_recommendation(measured_nipple_mm)
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
        "flange_recommendation": flange_recommendation,
        "product_highlights": _air1_product_highlights(),
        "quick_start_resources": _air1_quick_start_resources(),
        "usage_guidance": (
            "如果 status 是 manual_already_loaded 或 manual_already_loaded_with_faq，说明当前型号 manual 已在本会话上下文中，不要要求重新加载，直接复用已有 manual。"
            "manual 是当前型号的完整本地官方说明书整理稿，应作为设备步骤的主要事实依据。"
            "faq_results 是按用户问题检索到的相关 FAQ；如果为空，说明没有命中明确 FAQ，但 manual 仍可作为依据。"
            "relevant_images 是按当前 query/topic 预选的步骤图片；每个图片项都有 markdown_image。讲到对应新视觉步骤的第一轮时，必须复制最相关图片项的 markdown_image 展示图片，不要把 url 当可见正文。"
            "进入 Air1 开箱分步指导后，每个新视觉步骤首次展示当前步骤图；同一视觉步骤的后续轮次不要重复展示同一张图，应让用户对照上图继续。"
            "如果当前步骤没有可用 relevant_images 或 manual 图片，先用对应 topic 再调用 device_manual_search 获取步骤图，再继续指导。"
            "product_highlights 是确认 Air1 后可先给用户看的产品亮点，只能使用其中事实，不要扩写成资料未覆盖的卖点。"
            "quick_start_resources 是 Air1 开箱/首次使用资源；如果需要展示资源，只使用每个资源的 markdown_link 字段，禁止直接展示 url 或 /skill-assets/... 原始路径。"
            "如果 flange_recommendation 不为空，必须直接告诉用户推荐的法兰/硅胶塞尺寸，并说明是否随机附带；不要再让用户自己对照图片。"
            "在给出 quick_start_resources 的同一轮，不要直接开始 manual 第一步；只有用户确认需要一步步指导后，才进入首次使用推荐路径。"
            "开箱路径中 guide.parts 至少包含“取出平铺”和“清点核对”两个回合；用户说“好了”通常只代表平铺完成，"
            "不要从 guide.parts 直接跳到 guide.controls，必须先让用户核对可见物品、独立配件和整机状态是否齐全完整。"
            "面向用户的步骤要简短；分步指导以 manual 的 guide.* 模块为一轮主步骤，模块内 bullet 是同一步的子动作，通常合并在同一轮给出并等待完成确认。"
            "除非 manual 明确要求多轮、用户卡住或存在安全风险，不要把每个 bullet 都拆成一轮。进入新视觉步骤时配当前步骤图。"
            "不要补造资料中没有的 Air1 专属说明。"
        ),
    }


def create_support_ticket_draft(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    if not _support_ticket_creation_confirmed(args, inputs):
        message = _support_ticket_confirmation_message()
        return {
            "tool_name": "support_ticket_draft_create",
            "status": "needs_support_ticket_confirmation",
            "data": {
                "requires_confirmation": True,
                "confirmation_question": message,
            },
            "assistant_followup": {"message": message},
        }

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
        "assistant_followup": {"message": _support_ticket_followup_message(ticket)},
    }


def _support_ticket_creation_confirmed(args: dict[str, Any], inputs: RuntimeInputs) -> bool:
    if args.get("user_confirmed") is not True:
        return False
    message = _normalize_compact_text(inputs.get("user_message"))
    if not message:
        return False
    negative_terms = (
        "不需要",
        "不用",
        "先不用",
        "暂时不用",
        "不要",
        "别创建",
        "先别",
        "不用创建",
        "不要创建",
    )
    if any(term in message for term in negative_terms):
        return False
    confirmation_terms = (
        "需要",
        "可以",
        "好",
        "好的",
        "确认",
        "同意",
        "创建",
        "帮我创建",
        "帮我建",
        "建售后",
        "建工单",
        "创建售后",
        "提交工单",
        "提交售后",
        "售后工单",
        "联系客服",
        "现在帮我",
        "现在创建",
    )
    return any(term in message for term in confirmation_terms)


def _support_ticket_confirmation_message() -> str:
    return "非常抱歉没有解决你的问题，我可以帮你创建一个售后工单，我们客服团队会在 24 小时之内联系到你。你看，需要我现在帮你创建吗？"


def _normalize_compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _support_ticket_followup_message(ticket: dict[str, str]) -> str:
    issue_type = str(ticket.get("issue_type") or "").strip()
    if issue_type == "missing_parts":
        opening = "收到设备却发现配件不完整，确实很影响体验，也会耽误正常使用。"
    elif issue_type in {"malfunction", "defect"}:
        opening = "设备还是没法正常使用，确实很让人着急，尤其是已经按步骤排查过还没有变化的时候。"
    elif issue_type == "safety_concern":
        opening = "这个情况会让人不放心，先把安全放在第一位是对的。"
    elif issue_type == "return_or_refund":
        opening = "退换货这类事情本来就很耗心力，我先帮你把关键信息整理清楚。"
    elif issue_type == "order_or_shipping":
        opening = "订单或物流问题拖着不清楚，确实容易让人焦虑。"
    else:
        opening = "这件事确实会影响使用体验，也容易让人着急。"
    return (
        f"{opening}\n\n"
        "我已经帮你把售后信息整理好了，你可以看一下有没有需要补充或修改的地方。"
    )


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


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _measured_nipple_mm(value: Any, *, query: str, user_message: str, topic: str) -> float | None:
    explicit = _float(value)
    if explicit is not None:
        return explicit

    text = f"{query}\n{user_message}".strip()
    unit_match = re.search(r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*(?:mm|毫米|㎜)\b", text, flags=re.IGNORECASE)
    if unit_match:
        return _float(unit_match.group(1))

    if topic.lower() != "flange":
        return None
    compact = re.sub(r"\s+", "", text)
    plain_match = re.fullmatch(r"(?:测到|量到|大概|约|差不多)?(\d{1,2}(?:\.\d+)?)(?:左右)?", compact)
    if plain_match:
        return _float(plain_match.group(1))
    return None


def _air1_flange_recommendation(measured_nipple_mm: float | None) -> dict[str, Any] | None:
    if measured_nipple_mm is None:
        return None

    rounded = round(measured_nipple_mm, 1)
    rounded_measurement = int(rounded) if float(rounded).is_integer() else rounded
    matched = _air1_flange_size_range(measured_nipple_mm)
    if matched is None:
        return {
            "measured_nipple_mm": rounded_measurement,
            "status": "out_of_official_chart_range",
            "message": "这个测量值不在 Air1 官方法兰尺寸对照表覆盖范围内，建议重新测量一次，或联系 Momcozy 客服确认合适配件。",
        }

    recommended_mm = int(matched["recommended_mm"])
    accessory_type = str(matched["accessory_type"])
    included = accessory_type == "base_flange" or recommended_mm in AIR1_INCLUDED_FLANGE_INSERTS_MM
    if accessory_type == "base_flange":
        accessory_label = "24mm 基础法兰"
        purchase_note = "24mm 直接使用基础法兰，不需要额外法兰硅胶塞。"
    else:
        accessory_label = f"{recommended_mm}mm 法兰硅胶塞"
        purchase_note = (
            f"Air1 随机附带 {recommended_mm}mm 法兰硅胶塞。"
            if included
            else f"{recommended_mm}mm 法兰硅胶塞通常需要单独购买。"
        )

    return {
        "measured_nipple_mm": rounded_measurement,
        "status": "recommended",
        "matched_range": matched["range_label"],
        "recommended_flange_mm": recommended_mm,
        "recommended_insert_mm": recommended_mm if accessory_type == "flange_insert" else None,
        "accessory_type": accessory_type,
        "accessory_label": accessory_label,
        "included_with_air1": included,
        "purchase_note": purchase_note,
        "message": (
            f"{_format_mm(rounded_measurement)} 落在 {matched['range_label']} 区间，"
            f"建议使用 {accessory_label}。{purchase_note}"
        ),
    }


def _air1_flange_size_range(measured_nipple_mm: float) -> dict[str, Any] | None:
    for item in AIR1_FLANGE_SIZE_RANGES:
        min_mm = float(item["min_mm"])
        max_mm = float(item["max_mm"])
        if min_mm <= measured_nipple_mm < max_mm:
            return item
    final = AIR1_FLANGE_SIZE_RANGES[-1]
    if measured_nipple_mm == float(final["max_mm"]):
        return final
    return None


def _format_mm(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}mm"
    return f"{value:g}mm"


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
        (("穿戴", "开机", "启动设备", "开始使用"), ["guide.wearing_start"]),
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
            images.append(_image_resource(alt, url))
    return images


def _image_resource(alt: str, url: str) -> dict[str, str]:
    image = {
        "alt": alt,
        "url": url,
        "voice_policy": "announce",
        "priority": "instructional",
        "spoken_label": _image_spoken_label(),
    }
    if alt and url:
        image["markdown_image"] = f"![{alt}]({url})"
    image_text = AIR1_IMAGE_TEXT_BY_URL.get(url)
    if image_text:
        image["image_text"] = image_text
    return image


def _image_spoken_label() -> str:
    return "我放了一张当前步骤的对照图，你可以边看图边完成这一步。"


def _static_image_exists(url: str) -> bool:
    return _static_asset_exists(url)


def _static_asset_exists(url: str) -> bool:
    if url.startswith("/images/Air_img/"):
        image_path = (LEGACY_AIR1_FAQ_IMAGE_ROOT / url.removeprefix("/images/Air_img/")).resolve()
        root = LEGACY_AIR1_FAQ_IMAGE_ROOT.resolve()
        return root in image_path.parents and image_path.exists()
    if url.startswith("/skill-assets/"):
        relative_path = url.removeprefix("/skill-assets/")
        skill_id, _, asset_name = relative_path.partition("/")
        if not skill_id or not asset_name:
            return False
        image_path = (SKILLS_ROOT / skill_id / "assets" / asset_name).resolve()
        skill_assets_root = (SKILLS_ROOT / skill_id / "assets").resolve()
        return skill_assets_root in image_path.parents and image_path.exists()
    return False
