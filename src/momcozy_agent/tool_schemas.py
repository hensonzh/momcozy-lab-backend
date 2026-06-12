from __future__ import annotations

from typing import Any

from .types import FunctionToolDefinition, ToolName

# JSON_OBJECT_STRING is intentionally a string-typed field, used only for
# free-form structured payloads where defining a complete nested schema is
# impractical (for example arbitrary record events, profile patches, or
# rendered service card JSON). Application-side executors decode the string
# back into a dict before use.
JSON_OBJECT_STRING = {
    "type": "string",
    "description": '以字符串编码的自由 JSON 对象。不需要字段时使用 "{}"。',
}
JSON_ARRAY_STRING = {
    "type": "string",
    "description": '以字符串编码的自由 JSON 数组。不需要字段时使用 "[]"。',
}
ISO_DATE = {"type": "string", "description": "ISO-8601 日期，例如 2026-05-07。"}


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize a JSON schema for OpenAI strict-mode function tools."""

    types = _schema_types(schema)

    if "object" in types:
        schema.setdefault("additionalProperties", False)
        schema["required"] = list(schema.get("properties", {}).keys())
        for property_schema in schema.get("properties", {}).values():
            if isinstance(property_schema, dict):
                _strict_schema(property_schema)

    if "array" in types:
        items = schema.get("items")
        if isinstance(items, dict):
            _strict_schema(items)

    return schema


def _schema_types(schema: dict[str, Any]) -> set[str]:
    type_value = schema.get("type")
    if isinstance(type_value, str):
        return {type_value}
    if isinstance(type_value, list):
        return {item for item in type_value if isinstance(item, str)}
    return set()


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Mark a property schema as nullable for strict-mode function tools."""

    cloned = dict(schema)
    enum_values = cloned.get("enum")
    if isinstance(enum_values, list) and None not in enum_values:
        cloned["enum"] = [*enum_values, None]
    type_value = cloned.get("type")
    if isinstance(type_value, str):
        if type_value != "null":
            cloned["type"] = [type_value, "null"]
    elif isinstance(type_value, list):
        if "null" not in type_value:
            cloned["type"] = [*type_value, "null"]
    else:
        cloned["type"] = ["null"]
    return cloned


def _function_tool(
    name: ToolName,
    description: str,
    properties: dict[str, Any],
) -> FunctionToolDefinition:
    parameters = _strict_schema(
        {
            "type": "object",
            "properties": properties,
        }
    )

    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": parameters,
    }


FORM_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "应用表单层使用的稳定字段 ID。"},
        "label": {"type": "string", "description": "展示给用户看的字段标签。"},
        "type": {
            "type": "string",
            "description": "字段输入类型，例如 text、textarea、select、multi_select、checkbox_group、radio、checkbox、date 或 number。",
        },
        "required": {"type": "boolean"},
        "help_text": _nullable({"type": "string"}),
        "placeholder": _nullable({"type": "string"}),
        "default_value": _nullable({"type": "string"}),
        "options": _nullable(
            {
                "type": "array",
                "items": {"type": "string"},
                "description": "select、multi_select、checkbox_group、radio 或 checkbox 字段允许的字符串选项。",
            }
        ),
    },
}


FUNCTION_TOOLS: dict[ToolName, FunctionToolDefinition] = {
    "list_skills": _function_tool(
        "list_skills",
        "列出可用的结构化 skill，包括名称、描述、触发条件和能力范围。无副作用。只在判断是否需要结构化 skill 流程时使用；问候或普通问答不要使用。",
        {},
    ),
    "load_skill": _function_tool(
        "load_skill",
        "加载某个结构化 skill 的完整 SKILL.md，以及可用 references、scripts 和 assets 列表。无副作用。当用户需要该流程时使用，例如卡片、个性化计划、转接准备、基于记录的分析或设备专项支持；问候或普通科普回答不要使用。",
        {"skill_id": {"type": "string", "enum": ["birth-prep", "milk-management", "health-consultation", "emotion-support", "device-guidance"]}},
    ),
    "search_skill_assets": _function_tool(
        "search_skill_assets",
        "在已加载 skill 的可用 reference、script 和 asset 文件名中搜索。无副作用。不会暴露任意文件系统访问。",
        {"skill_id": {"type": "string"}, "query": {"type": "string"}},
    ),
    "read_skill_file": _function_tool(
        "read_skill_file",
        "读取已加载 skill 目录下被允许的文本 reference 或 asset 文件。无副作用。只能读取 load_skill 或 search_skill_assets 返回的路径；脚本应使用 run_approved_skill_script。",
        {
            "skill_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["references", "assets"]},
            "path": {"type": "string"},
        },
    ),
    "run_approved_skill_script": _function_tool(
        "run_approved_skill_script",
        "请求执行已加载 skill 中被批准的脚本。必须由应用侧审批并执行；模型永远不能获得 shell 访问。",
        {
            "skill_id": {"type": "string"},
            "script_name": {"type": "string"},
            "args": _nullable(JSON_OBJECT_STRING),
        },
    ),
    "ui_form_create": _function_tool(
        "ui_form_create",
        "创建前端可渲染的通用表单规格，用于在生成结构化内容前收集或确认用户信息。无后端副作用。分娩沟通单应优先使用 birth_plan_form_create；待产包应优先使用 hospital_bag_form_create；只有兼容旧流程时才手写字段。",
        {
            "form_id": {"type": "string", "description": "稳定表单 ID，例如 birth_plan_card_intake 或 hospital_bag_intake。"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "submit_label": {"type": "string"},
            "fields": {
                "type": "array",
                "items": FORM_FIELD_SCHEMA,
                "description": "由前端渲染的表单字段。不适用 help_text、placeholder、default_value 或 options 时使用 null。",
            },
        },
    ),
    "ui_quick_replies_create": _function_tool(
        "ui_quick_replies_create",
        "为当前最终回复创建 3 个前端快捷输入提示。无后端副作用；每轮最终回复都应调用一次。不要用于替代正文回答，不要在正文里复述这些快捷输入。每次调用必须提供且只提供 3 个短提示；点击后只会作为普通用户消息发送，不能绕过保存、提交、替换、转接等确认流程。",
        {
            "replies": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "按钮上展示的短文案，建议 6-18 个字。"},
                        "send_text": {"type": "string", "description": "用户点击后发送给智能体的完整文本，通常与 text 相同。"},
                    },
                },
                "description": "恰好 3 个快捷输入提示。",
            },
        },
    ),
    "birth_plan_form_create": _function_tool(
        "birth_plan_form_create",
        "创建分娩沟通单信息采集表单。LLM 只传已知字段 default_values；表单字段、顺序、分类、选项和排他选项过滤由工具稳定生成。无后端副作用。",
        {
            "default_values": JSON_OBJECT_STRING,
        },
    ),
    "labor_communication_card_create": _function_tool(
        "labor_communication_card_create",
        "根据应用侧注入的 birth_plan_card_intake 表单提交数据生成前端可渲染的分娩沟通单。LLM 不需要生成 card_json，也不能用参数伪造表单提交；字段映射、强诉求降级、分区整理、医院问题和安全声明由工具稳定生成。无后端副作用。confirmed_form_data 参数传 \"{}\" 即可。",
        {
            "confirmed_form_data": JSON_OBJECT_STRING,
        },
    ),
    "birth_journey_plan_card_create": _function_tool(
        "birth_journey_plan_card_create",
        "根据已知的孕周/预产期、胎次/胎数、分娩方式、医院、支持人和喂养意向，生成前端可渲染并保存为 active care plan 的生产全过程计划。LLM 不需要生成 card_json；日期换算、阶段拆分、个性化调整和安全声明由工具稳定生成。已有 active 生产全过程计划时工具会返回 existing_plan_found，不要重复生成。",
        {
            "plan_context": JSON_OBJECT_STRING,
            "scope": {"type": "string", "enum": ["full", "prenatal_only", "short_range"]},
        },
    ),
    "birth_journey_plan_delete": _function_tool(
        "birth_journey_plan_delete",
        "删除当前用户已保存的 active 生产全过程计划。“生产计划”“已有生产计划”“生产过程计划”在 birth-prep 场景下都指这份生产全过程计划。具有后端副作用；只有用户明确要求并确认删除生产全过程计划后才调用。不要用于删除奶量计划、待产包清单、分娩沟通单或普通聊天记录。confirmed 必须为 true，否则工具不会删除。",
        {
            "confirmed": {"type": "boolean", "description": "用户是否已经明确确认删除生产全过程计划。只有 true 才执行删除。"},
        },
    ),
    "pregnancy_diary_manage": _function_tool(
        "pregnancy_diary_manage",
        "读写当前用户的孕期日记。通过 action 区分读取、写入、更新和删除：list 读取最近记录；get_today 读取指定日期或今天；create 新建一条日记；update 合并更新已有日记；delete 删除已有日记。用于用户要求记录孕期生活、回顾最近孕期状态、整理日记里的产检问题或删除日记。不要用于生产全过程计划、奶量记录、宝宝成长记录或普通医学诊断。删除必须 confirmed=true；更新只传要改的字段，未传字段会保留。",
        {
            "action": {"type": "string", "enum": ["list", "get_today", "create", "update", "delete"]},
            "entry_id": _nullable({"type": "integer", "description": "更新或删除指定日记时使用；未知时传 null。"}),
            "entry_date": _nullable({"type": "string", "description": "ISO-8601 日期，例如 2026-06-10。读取今天或按日期更新/删除时使用；未知时传 null。"}),
            "start_date": _nullable({"type": "string", "description": "action=list 的开始日期；不限定时传 null。"}),
            "end_date": _nullable({"type": "string", "description": "action=list 的结束日期；不限定时传 null。"}),
            "limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "action=list 的读取条数；通常传 7。"},
            "gestational_week": _nullable({"type": "string"}),
            "mood": _nullable({"type": "string"}),
            "energy_level": _nullable({"type": "string"}),
            "sleep_summary": _nullable({"type": "string"}),
            "fetal_movement": _nullable({"type": "string"}),
            "symptom_tags": _nullable({"type": "array", "items": {"type": "string"}, "description": "身体感受标签；不更新时传 null。"}),
            "appointment_note": _nullable({"type": "string", "description": "想问医生或下次产检要确认的问题。"}),
            "nutrition_note": _nullable({"type": "string"}),
            "content": _nullable({"type": "string", "description": "孕期生活或身体感受正文。"}),
            "confirmed": {"type": "boolean", "description": "删除前是否已经获得用户明确确认。非删除 action 传 false。"},
        },
    ),
    "hospital_bag_form_create": _function_tool(
        "hospital_bag_form_create",
        "创建待产包信息采集表单。用户确认开始待产包整理后可直接调用；LLM 只在 default_values 里传当前对话、request_context、已提交表单或 profile_get 中可靠的已知字段。表单字段、顺序、分类、选项和样式约束由工具稳定生成。无后端副作用。",
        {
            "default_values": JSON_OBJECT_STRING,
        },
    ),
    "hospital_bag_card_create": _function_tool(
        "hospital_bag_card_create",
        "根据应用侧注入的 hospital_bag_intake 表单提交数据生成前端可渲染的待产包清单。LLM 不需要生成 card_json，也不能用参数伪造表单提交；分包、物品、数量、医院确认项、购物车 followup 和兼容字段由工具稳定生成。无后端副作用。confirmed_form_data 参数传 \"{}\" 即可。调用本工具后的最终回复只保留工具返回的 assistant_followup.message，不要再复述已确认字段、设计思路、住院天数或医院确认逻辑。",
        {
            "confirmed_form_data": JSON_OBJECT_STRING,
            "generation_mode": {"type": "string", "enum": ["standard", "quick", "immediate"]},
        },
    ),
    "hospital_bag_cart_update": _function_tool(
        "hospital_bag_cart_update",
        "根据用户自然语言修改当前待产包购物车。只在 request_context 已有 current_hospital_bag_cart，或当前对话明确处于待产包购物车页面/购物车调整流程时使用；不要用于首次生成待产包清单、独立吸奶器型号选型、设备故障排查或真实下单。用于用户说预算上限、太贵、便宜一点、删掉/加回某个商品、医院会提供、家里已有、调整数量、恢复默认购物车、把已推荐的 Momcozy 吸奶器型号同步到购物车等。用户给出明确金额时必须使用 action=optimize_budget 并设置 target_budget，例如“1000元以内”传 1000。工具只返回前端可应用的购物车更新，不真正下单。预算优化默认尽量保留吸奶器；只有用户明确要求删除吸奶器，或 allow_remove_pump=true 时才可移除。若用户要删除具体物品，应从 request_context 里的 item_id 中选择；不确定具体物品时传空数组并用 assistant_message 简短询问。若要同步吸奶器型号，先用 hospital_bag_pump_recommend 选型，再用 action=replace_pump_model 并传 product_sku_id。",
        {
            "action": {
                "type": "string",
                "enum": [
                    "replace_pump_model",
                    "add_pump_model",
                    "optimize_budget",
                    "apply_budget_plan",
                    "remove_items",
                    "restore_items",
                    "replace_items",
                    "mark_provided",
                    "mark_owned",
                    "update_quantity",
                    "reset_cart",
                    "clarify",
                ],
            },
            "item_ids": {"type": "array", "items": {"type": "string"}, "description": "要操作的当前购物车 item_id；预算优化或不需要指定物品时传空数组。"},
            "product_sku_id": _nullable({"type": "string", "description": "action=replace_pump_model 或 add_pump_model 时使用；可取 pump-s9-pro、pump-s12-pro-quick、pump-m5-smart、pump-m6、pump-v1-pro、pump-v2-pro、pump-m9、pump-w1、pump-air-1。其他 action 传 null。"}),
            "quantity_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "qty": {"type": "integer", "minimum": 0},
                    },
                },
                "description": "action=update_quantity 时使用；qty=0 表示移除。",
            },
            "target_budget": _nullable({"type": "number", "description": "用户明确预算上限，例如 1000；没有明确预算时传 null。"}),
            "budget_mode": {"type": "string", "enum": ["under", "around", "cheaper", "minimal", "none"], "description": "预算意图；没有预算相关意图时传 none。"},
            "preference": {"type": "string", "enum": ["balanced", "cheapest", "comfort", "breastfeeding", "minimal"], "description": "用户偏好；不确定时传 balanced。"},
            "preserve_item_ids": {"type": "array", "items": {"type": "string"}, "description": "用户明确想保留的 item_id；不确定传空数组。"},
            "allow_remove_pump": {"type": "boolean", "description": "只有用户明确同意吸奶器后买/删除吸奶器，或预算低到必须移除且用户确认时才传 true。默认 false。"},
            "assistant_message": {"type": "string", "description": "给用户的简短说明。可为空，由工具生成默认文案。"},
        },
    ),
    "hospital_bag_pump_recommend": _function_tool(
        "hospital_bag_pump_recommend",
        "购买前选型工具：根据用户预算、使用场景和偏好，从 Momcozy 官方吸奶器型号目录里推荐 1 款主推型号和 1-2 款备选。适用于用户问哪款吸奶器适合自己、型号差异、预算内怎么选、某型号多少钱或“Air1 呢”等点名型号追问，也适用于待产包场景中先确定吸奶器型号。不要用于已购设备故障、说明书/FAQ、配件问题或奶量是否正常；这些分别使用 device_support 或 milk_management。无购物车副作用；如果用户要同步购物车，拿返回的 cart_sync_suggestion 再调用 hospital_bag_cart_update。价格口径使用 Momcozy 官方对外价格，保留官方 USD 标价和活动价字段。比较预算必须按 official_price_usd/sale_price_usd/price_label/sale_price_label 数值判断；Air 1 是高价轻薄款，不能描述为降低预算、省钱或更便宜选择。",
        {
            "requested_model": _nullable({"type": "string", "description": "用户点名询问或追问的型号，例如 Air 1、Air1、M9、S12 Pro Quick；没有点名型号传 null。"}),
            "use_case": {
                "type": "string",
                "enum": ["unknown", "hospital_backup", "daily_home", "work_pumping", "portable", "comfort", "performance", "high_output", "budget"],
                "description": "用户主要使用场景；不确定传 unknown。",
            },
            "preference": {
                "type": "string",
                "enum": ["balanced", "budget", "comfort", "portable", "performance", "app", "simple", "premium"],
                "description": "用户最在意的选择偏好；不确定传 balanced。",
            },
            "feeding_intention": {
                "type": "string",
                "enum": ["unknown", "breastfeeding", "mixed", "formula"],
                "description": "用户喂养意向；只在已知时填写。",
            },
            "target_budget_usd": _nullable({"type": "number", "description": "用户明确用美元表达的吸奶器预算上限；没有明确美元预算时传 null。"}),
            "must_have_app": _nullable({"type": "boolean", "description": "用户明确要求 App 控制传 true，明确不要 App 传 false，不确定传 null。"}),
            "need_single_unit": _nullable({"type": "boolean", "description": "用户明确只想买单边/单台传 true，明确要套装传 false，不确定传 null。"}),
            "assistant_message": {"type": "string", "description": "可选的简短说明；通常传空字符串，让工具生成默认推荐文案。"},
        },
    ),
    "ibclc_consult_card_create": _function_tool(
        "ibclc_consult_card_create",
        "创建前端可渲染的 IBCLC 在线咨询卡片，无后端副作用。仅在用户明确要求或确认需要 IBCLC/哺乳顾问/真人或人工哺乳咨询/在线咨询时使用。若是智能体自主判断问题已经进入含乳、排乳、泵奶节奏、反复堵奶、宝宝摄入细节或反复尝试无效，应先承接用户处境，完成至少一轮必要问诊，主动说明更适合让 IBCLC 顾问继续看，并询问是否现在打开咨询入口；用户同意后再调用本工具。不要因为用户首次提到疼痛、堵奶、奶量担忧或宝宝摄入风险就直接触发。前端会渲染顾问姓名、资质、经验、简介和在线咨询入口。",
        {
            "consultant_name": _nullable({"type": "string", "description": "前端名片展示的 IBCLC 顾问姓名；不确定时传 null，由工具使用默认 demo 顾问。"}),
            "consultant_bio": _nullable({"type": "string", "description": "前端名片展示的顾问简介；不确定时传 null，由工具使用默认简介。"}),
            "chat_url": _nullable({"type": "string", "description": "在线咨询按钮跳转的 H5 URL；不确定时传 null，由工具使用默认页面。"}),
        },
    ),
    "profile_get": _function_tool(
        "profile_get",
        "读取客户端当前会话传入的用户、宝宝和当前状态摘要。无副作用。不需要模型提供用户 ID。",
        {},
    ),
    "handoff_summary_generate": _function_tool(
        "handoff_summary_generate",
        "生成给人工或专业支持接手用的简洁转接摘要。只在已经决定转接、用户明确需要人工/专业支持，或某个服务流程要求转接时使用；不要用于普通回答总结、设备售后工单、待产包购物车或吸奶器选型。无副作用，不会对外提交。",
        {
            "issue_type": {"type": "string", "description": "转接类型，例如 emotion_risk、ibclc、clinical_question、care_support 或 other。"},
            "facts": {**JSON_OBJECT_STRING, "description": "仅包含用户已提供或工具读取到的关键事实、已尝试步骤和待接手问题；不要包含推测诊断。"},
        },
    ),
    "device_manual_search": _function_tool(
        "device_manual_search",
        "补充已购/正在使用的 Momcozy 吸奶器设备资料。无副作用。用于获取当前型号说明书、检索 FAQ 问答、查找步骤图片，并在 Air1 开箱场景返回产品亮点、Quick Start PDF 和操作视频资源。不要用于购买前型号推荐或价格比较；选型使用 hospital_bag_pump_recommend。不要用于判断奶量是否正常或制定喂养计划；奶量数据问题使用 milk_management。展示 Quick Start/PDF/视频资源时使用工具结果中的 markdown_link；展示步骤图片时使用 relevant_images 或 manual 图片项中的 markdown_image，禁止把 /skill-assets/... 原始路径直接输出成可见正文。进入 Air1 开箱分步指导后，每个新视觉步骤首次展示当前步骤图；同一视觉步骤后续轮次不要重复同图，可让用户对照上图。分步指导以 manual 的 guide.* 模块为一轮主步骤，模块内 bullet 是同一步的子动作；除非 manual 明确要求多轮、用户卡住或存在安全风险，不要把每个 bullet 都拆成一轮。用户问“图中/上图/编号/标号”时，只参考当前刚展示给用户的图片，不要用历史图片中相同编号猜。用户问部件是什么、作用原理、为什么、能不能、多少、区别、是否正常等日常设备知识时，应使用 topic=faq 检索 FAQ。用户在法兰步骤给出 14mm、14 毫米等乳头根部测量值时，必须设置 topic=flange 并把数值填入 measured_nipple_mm，随后使用工具返回的 flange_recommendation 直接推荐法兰/硅胶塞尺寸。已获得同型号 manual 后，连续步骤应复用已有内容；只有新的 FAQ 问题、缺少步骤图片或需要根据测量值计算法兰推荐时才再次调用。",
        {
            "model": {"type": "string", "enum": ["Air1", "unknown"], "description": "已确认的设备型号。当前只支持 Air1；未知型号必须传 unknown。"},
            "query": {"type": "string", "description": "用户的设备问题，或需要检索的具体指导主题。"},
            "topic": {
                "type": "string",
                "enum": ["overview", "unboxing", "setup", "daily_use", "cleaning", "disinfection", "assembly", "flange", "suction", "charging", "bluetooth", "milk_storage", "troubleshooting", "parts", "faq", "other"],
            },
            "measured_nipple_mm": _nullable({"type": "number", "description": "法兰步骤里用户给出的乳头根部直径，单位 mm，例如用户说 14mm 就传 14；没有测量值时传 null。"}),
            "max_results": {"type": "number", "description": "希望返回的 FAQ 片段数量，通常为 2 到 4。首次加载会按型号返回完整说明书；已加载时可能只返回轻量状态、FAQ 和相关图片。"},
        },
    ),
    "support_ticket_draft_create": _function_tool(
        "support_ticket_draft_create",
        "为未解决的 Momcozy 吸奶器或设备售后问题整理前端可确认的客服工单信息。创建前必须先向用户确认是否需要现在创建售后工单；只有用户已经明确同意时，user_confirmed 才能为 true。适用于用户明确请求客服/退货/保修，反馈缺件或疑似缺陷，设备安全问题需要升级支持，或经过 device_manual_search 排查后仍未解决且用户愿意升级时使用。不要用于普通操作指导、购买前选型、奶量建议或专业照护转接摘要。",
        {
            "issue_type": {
                "type": "string",
                "enum": ["malfunction", "missing_parts", "defect", "warranty", "return_or_refund", "order_or_shipping", "usage_help", "safety_concern", "other"],
            },
            "issue_summary": {"type": "string", "description": "面向客服和用户的简洁问题摘要，用于预填工单。"},
            "product_model": _nullable({"type": "string"}),
            "order_number": _nullable({"type": "string"}),
            "purchase_channel": _nullable({"type": "string"}),
            "user_contact": _nullable({"type": "string"}),
            "troubleshooting_done": _nullable({"type": "array", "items": {"type": "string"}}),
            "urgency": {"type": "string", "enum": ["normal", "high", "safety"]},
            "user_emotion": _nullable({"type": "string", "description": "简短描述观察到的用户情绪，例如沮丧、焦虑或生气。"}),
            "attachments_note": _nullable({"type": "string", "description": "说明已提供或仍需要的相关图片/视频附件。"}),
            "user_confirmed": {"type": "boolean", "description": "用户是否已经明确同意现在创建售后工单。只有用户说需要、可以、确认、帮我创建、现在创建等明确同意表达时才为 true。"},
        },
    ),
    "milk_snapshot_get": _function_tool(
        "milk_snapshot_get",
        "GET 只读工具：读取当前用户奶量管理的轻量快照，包括用户/宝宝资料、最新计划元数据等。用于进入奶量管理流程时补足基础上下文，或在生成建议前确认是否已有计划。不要用它替代 milk_status_query 的状态页事实、milk_records_query 的历史明细、milk_assessment_evaluate 的奶量评估或 milk_plan_query 的完整计划读取。只返回结构化事实；最终解释由模型完成。",
        {},
    ),
    "milk_status_query": _function_tool(
        "milk_status_query",
        "GET 只读工具：读取类似 MaiMomcozy 状态页的奶量聚合信息，包括妈妈宝宝资料、今日产奶/喂养、30 日趋势、宝宝生长记录和当天计划任务。适合用户问“现在状态怎么样”“今天数据”“状态页信息”或需要展示近期趋势事实。不要用它替代 milk_assessment_evaluate 来回答奶量是否够、是否正常、趋势风险或适合什么计划；不要用它替代 milk_records_query 查可修改的原始记录。",
        {
            "section": {
                "type": "string",
                "enum": ["all", "overview", "today", "trend", "growth", "tasks"],
                "description": "要读取的状态区块。常规状态问题用 all；只看今日用 today；只看趋势用 trend。",
            },
            "target_date": _nullable(ISO_DATE),
            "trend_days": {"type": "integer", "description": "趋势天数，1-30；常规传 30。"},
            "growth_history_limit": {"type": "integer", "description": "最多返回多少条宝宝生长记录，建议 5-10。"},
            "include_tasks": {"type": "boolean", "description": "是否同时读取 target_date 当天计划任务。"},
        },
    ),
    "milk_records_query": _function_tool(
        "milk_records_query",
        "GET 只读工具：读取任意时间段内的吸奶、亲喂、母乳瓶喂和奶粉瓶喂记录，返回结构化原始记录和聚合摘要。只适合用户明确要“查记录、列出每天多少、看原始记录、修改/删除某条记录”。本工具返回的是实际发生记录，不是计划；不要把记录时间称为“原计划/当前计划”。如果用户说“分析最近吸奶情况、奶量够不够、是否正常、趋势好不好、要不要追奶/稳奶/减奶”，不要只用本工具，必须改用或补用 milk_assessment_evaluate，因为本工具不返回参考奶量区间。不要在同一轮用相同 start_at/end_at/record_scope 重复调用；需要记录 ID 做修改/删除时才再次查询原始记录。亲喂奶量如出现估算会明确标记为 estimated。",
        {
            "start_at": {"type": "string", "description": "起始日期或日期时间，例如 2026-05-01 或 2026-05-01 08:00。"},
            "end_at": {"type": "string", "description": "结束日期或日期时间；日期会按整天处理并作为 exclusive end 的下一日 00:00。"},
            "record_scope": {
                "type": "string",
                "enum": ["all", "milk_output", "feeding", "pumping", "nursing", "breastmilk_bottle", "formula_bottle"],
                "description": "读取范围：母乳产出用 milk_output；全部记录用 all。",
            },
            "include_raw_records": {"type": "boolean", "description": "是否返回原始记录列表；只要摘要时传 false。"},
            "summary_granularity": {"type": "string", "enum": ["none", "daily"], "description": "是否返回每日聚合。"},
            "limit": {"type": "integer", "description": "每类原始记录最多返回数量，建议 50-200。"},
        },
    ),
    "milk_record_mutate": _function_tool(
        "milk_record_mutate",
        "CREATE/UPDATE/DELETE 写入工具：新增、修改或删除真实发生过的吸奶/喂养记录。record_kind 支持 pumping、nursing、breastmilk_bottle、formula_bottle。有副作用；只有用户明确确认后才调用。不要用于完成/跳过计划任务；计划任务完成状态使用 milk_task_complete。不要用计划值代替用户提供的实际奶量或时长。",
        {
            "operation": {"type": "string", "enum": ["create", "update", "delete"]},
            "record_kind": {"type": "string", "enum": ["pumping", "nursing", "breastmilk_bottle", "formula_bottle"]},
            "record_id": _nullable({"type": "integer", "description": "update/delete 必填；create 传 null。"}),
            "occurred_at": _nullable({"type": "string", "description": "create 时的记录发生时间，例如 2026-05-14 09:30。"}),
            "amount_ml": _nullable({"type": "number", "description": "create 时的吸奶或瓶喂奶量 ml；亲喂不确定时传 null。"}),
            "duration_minutes": _nullable({"type": "integer", "description": "create 时的吸奶或亲喂持续分钟数；不确定时传 null。"}),
            "infant_id": _nullable({"type": "integer", "description": "喂养记录对应宝宝 ID；不确定时传 null。"}),
            "title": _nullable({"type": "string", "description": "可选标题或备注；无则传 null。"}),
            "patch": JSON_OBJECT_STRING,
            "idempotency_key": {"type": "string"},
        },
    ),
    "milk_plan_query": _function_tool(
        "milk_plan_query",
        "GET 只读工具：读取已经保存的奶量计划。用户提到“原计划、已有计划、当前追奶/稳奶/减奶计划、按计划调整”时使用。不要用它生成新计划；新计划草稿使用 milk_plan_preview。plan_id 不为 null 时读取单个计划；plan_id 为 null 时按 plan_type/limit 列出计划。具体每天几点执行通常还要配合 milk_calendar_query 读取 calendar。",
        {
            "plan_id": _nullable({"type": "integer"}),
            "plan_type": _nullable({"type": "string", "enum": ["increase_milk", "maintain_milk", "decrease_milk"]}),
            "limit": {"type": "integer"},
        },
    ),
    "milk_plan_mutate": _function_tool(
        "milk_plan_mutate",
        "CREATE/UPDATE/DELETE 写入工具：保存、更新或删除用户已确认的奶量计划。通常在 milk_plan_preview 生成草稿并获得用户确认后调用。保存计划会从明天开始展开写入 calendar，不覆盖今天；如明天起已有未来计划任务，必须先让用户确认追加还是替换，再传 calendar_write_strategy。有副作用；只有用户明确确认后才调用。不要用于单次日程调整或任务完成。",
        {
            "operation": {"type": "string", "enum": ["create", "update", "delete"]},
            "plan_id": _nullable({"type": "integer"}),
            "confirmed_plan": JSON_OBJECT_STRING,
            "patch": JSON_OBJECT_STRING,
            "reexpand_calendar": {"type": "boolean"},
            "delete_calendar_items": {"type": "boolean"},
            "calendar_write_strategy": _nullable(
                {
                    "type": "string",
                    "enum": ["append", "replace_future_plan_tasks"],
                    "description": "create 保存计划时的日程写入方式。append=追加到明天起现有日程；replace_future_plan_tasks=替换明天起未来未完成的旧计划任务。无已有未来计划任务可传 null。",
                }
            ),
            "idempotency_key": {"type": "string"},
        },
    ),
    "milk_calendar_query": _function_tool(
        "milk_calendar_query",
        "GET 只读工具：读取奶量 calendar/计划执行情况。calendar 是“今天安排、未来几天安排、原计划展开后的具体时间”的来源；不要用实际吸奶记录替代 calendar。query_mode=range 读取任意范围；today_overview/today_summary 读取某日概览或日结。",
        {
            "query_mode": {"type": "string", "enum": ["range", "today_overview", "today_summary"]},
            "target_date": _nullable(ISO_DATE),
            "start_at": _nullable({"type": "string", "description": "range 查询起始日期或日期时间。"}),
            "end_at": _nullable({"type": "string", "description": "range 查询结束日期或日期时间。"}),
            "plan_id": _nullable({"type": "integer"}),
            "item_type": _nullable({"type": "string", "enum": ["吸奶", "亲喂", "自定义"]}),
            "include_items": {"type": "boolean", "description": "range 查询时是否返回条目列表；只要统计时传 false。"},
            "limit": {"type": "integer", "description": "range 查询最多返回条目数量，建议 50-200。"},
        },
    ),
    "milk_calendar_change_preview": _function_tool(
        "milk_calendar_change_preview",
        "PREVIEW 候选变更工具：预览新增单次 calendar 事项导致的变更，不写数据库。适用于用户通过文字或图片提出新增一次会议、外出、临时吸奶/亲喂或其他自定义事项，并需要检查冲突。不要用于生成追奶/稳奶/减奶计划草稿；计划草稿使用 milk_plan_preview。返回冲突、候选调整和 proposal；写入前必须获得用户确认。",
        {
            "target_date": ISO_DATE,
            "event_start_time": {"type": "string", "description": "开始时间，例如 09:00 或完整 ISO datetime。"},
            "event_end_time": _nullable({"type": "string", "description": "结束时间，例如 09:30；如提供 duration_minutes 可传 null。"}),
            "duration_minutes": _nullable({"type": "integer"}),
            "content": {"type": "string"},
            "item_type": _nullable({"type": "string", "enum": ["吸奶", "亲喂", "自定义"], "description": "吃饭、外出、会议、接孩子等使用 自定义。"}),
            "plan_id": _nullable({"type": "integer"}),
        },
    ),
    "milk_calendar_reschedule_preview": _function_tool(
        "milk_calendar_reschedule_preview",
        "PREVIEW 日程重排工具：根据用户通过文字或图片提供的会议、通勤、外出等不可用时间段，读取目标日期当前 calendar，并预览如何重排当天吸奶/亲喂计划以避开这些时间段。不写数据库；写入前必须让用户确认。",
        {
            "target_date": ISO_DATE,
            "busy_windows": {
                **JSON_ARRAY_STRING,
                "description": 'JSON 数组字符串，每项包含 start_time、end_time 或 duration_minutes、content。例如 [{"start_time":"09:00","end_time":"10:30","content":"会议"}]。图片里的日程先由模型识别成这个结构；不确定日期或时间时不要调用。',
            },
            "adjustable_item_types": {
                **JSON_ARRAY_STRING,
                "description": 'JSON 数组字符串，允许被重排的 calendar 类型。默认传 ["吸奶","亲喂"]；只想动吸奶计划时传 ["吸奶"]。',
            },
            "plan_id": _nullable({"type": "integer"}),
            "default_duration_minutes": {"type": "integer", "description": "没有 end_time 的吸奶/亲喂任务默认持续时间，建议 20-30。"},
            "min_gap_minutes": {"type": "integer", "description": "重排后吸奶/亲喂任务之间的最小间隔，建议 90。"},
            "include_busy_events": {"type": "boolean", "description": "是否把会议/外出等不可用时间作为自定义事项一起写入 calendar；通常传 true。"},
        },
    ),
    "milk_calendar_mutate": _function_tool(
        "milk_calendar_mutate",
        "APPLY/UPDATE/DELETE 写入工具：应用 milk_calendar_change_preview 或 milk_calendar_reschedule_preview 返回的 proposal，或批量/单条修改、删除 calendar 条目。有副作用；只有用户明确确认后才调用。不要用于保存完整奶量计划；使用 milk_plan_mutate。任务完成/跳过优先使用 milk_task_complete。",
        {
            "operation": {"type": "string", "enum": ["apply_adjustment", "apply_reschedule", "range_shift", "range_delete", "patch_items", "update_item", "delete_item"]},
            "target_date": _nullable(ISO_DATE),
            "proposal": JSON_OBJECT_STRING,
            "start_at": _nullable({"type": "string"}),
            "end_at": _nullable({"type": "string"}),
            "patch": JSON_OBJECT_STRING,
            "plan_id": _nullable({"type": "integer"}),
            "item_type": _nullable({"type": "string", "enum": ["吸奶", "亲喂", "自定义"]}),
            "item_id": _nullable({"type": "integer"}),
            "idempotency_key": {"type": "string"},
        },
    ),
    "milk_task_complete": _function_tool(
        "milk_task_complete",
        "COMPLETE/CANCEL/SKIP 写入工具：确认后更新已有计划任务或 calendar 条目的完成状态，可按用户提供的真实奶量/时长同步创建或删除关联吸奶/喂养记录。用于“这个完成了”“取消完成”“跳过这次”。不要用于新增、修改或删除独立历史记录；那类记录编辑使用 milk_record_mutate。有副作用；只有用户明确确认后才调用。",
        {
            "operation": {"type": "string", "enum": ["complete", "cancel_complete", "skip"]},
            "target_date": _nullable(ISO_DATE),
            "task_id": _nullable({"type": "integer", "description": "MaiMomcozy 计划任务 ID；若传 item_id 可为 null。"}),
            "item_id": _nullable({"type": "integer", "description": "calendar item_id；若已通过 milk_calendar_query 定位，优先传 item_id。"}),
            "record_kind": _nullable(
                {
                    "type": "string",
                    "enum": ["pumping", "nursing", "breastmilk_bottle", "formula_bottle", "none"],
                    "description": "要同步的真实记录类型。不确定时传 null 由工具按任务类型推断；只更新完成状态用 none。缺少真实奶量/时长时不要强行同步记录。",
                }
            ),
            "amount_ml": _nullable({"type": "number", "description": "用户明确提供的实际吸奶量或瓶喂奶量 ml；未知传 null，不要用计划值代替。"}),
            "duration_minutes": _nullable({"type": "integer", "description": "用户明确提供的实际吸奶或亲喂时长；未知传 null，不要用计划时长代替。"}),
            "occurred_at": _nullable({"type": "string", "description": "实际完成时间，例如 2026-05-14 09:30 或 09:30；未知传 null 使用任务时间。"}),
            "title": _nullable({"type": "string", "description": "同步记录标题/备注；未知传 null 使用任务内容。"}),
            "delete_linked_record": {"type": "boolean", "description": "取消完成或跳过时是否删除该任务同步创建的记录；通常传 true。"},
            "idempotency_key": {"type": "string"},
        },
    ),
    "milk_assessment_evaluate": _function_tool(
        "milk_assessment_evaluate",
        "EVALUATE 只读工具：基于固定参考数据和记录聚合，返回近期奶量状态、缺失数据、每日参考奶量区间、含亲喂估算奶量和规则命中。所有奶量分析请求都要先确认宝宝摄入/精神状态和妈妈乳房/全身状态；缺少这些关键信息或存在风险时不会生成分析卡，而是返回 needs_clinical_context 或 gate 结果。只查历史明细或单纯列每天多少时才用 milk_records_query。不是诊断，也不生成最终用户话术。",
        {
            "as_of_time": _nullable({"type": "string", "description": "可选 ISO-8601 评估时间；不确定时传 null。"}),
            "window_days": {"type": "integer", "description": "回看天数。分析最近吸奶情况、奶量趋势或全面评估通常用 7；用户已明确追奶/稳奶/减奶计划方向时可用 1。"},
            "include_today": {"type": "boolean", "description": "是否包含当前日未完整记录。通常评估完整日时传 false。"},
            "maternal_symptoms": {
                **JSON_OBJECT_STRING,
                "description": "字符串编码 JSON。可包含 fever、chills、breast_redness、lump_or_hard_area、worsening_pain、nipple_damage、recurrent_plug、pain_level。没有信息传 {}。",
            },
            "infant_signals": {
                **JSON_OBJECT_STRING,
                "description": "字符串编码 JSON。可包含 wet_diapers_24h、stool_24h、baby_state、poor_feeding、poor_latch、lethargy、recent_weight、growth_concern。没有信息传 {}。",
            },
        },
    ),
    "milk_clinical_assessment_evaluate": _function_tool(
        "milk_clinical_assessment_evaluate",
        "EVALUATE 只读工具：在奶量评估之上综合宝宝摄入/生长、妈妈乳房症状和记录完整度，返回数据可信度、风险等级、计划准入 plan_gate 和下一步动作。用于复杂奶量判断、是否适合追奶/稳奶/减奶、是否需要 IBCLC/医生优先介入。不是诊断，不写数据库，不生成最终用户话术。",
        {
            "as_of_time": _nullable({"type": "string", "description": "可选 ISO-8601 评估时间；不确定时传 null。"}),
            "window_days": {"type": "integer", "description": "回看天数。通常用 7；用户已明确计划方向且只看最近完整日时可用 1。"},
            "include_today": {"type": "boolean", "description": "是否包含当前日未完整记录。通常评估完整日时传 false。"},
            "requested_plan_type": _nullable({"type": "string", "enum": ["increase_milk", "maintain_milk", "decrease_milk"]}),
            "maternal_symptoms": {
                **JSON_OBJECT_STRING,
                "description": "字符串编码 JSON。可包含 fever、chills、breast_redness、lump_or_hard_area、worsening_pain、nipple_damage、recurrent_plug、pain_level。未知字段传 {}。",
            },
            "infant_signals": {
                **JSON_OBJECT_STRING,
                "description": "字符串编码 JSON。可包含 wet_diapers_24h、stool_24h、baby_state、poor_feeding、poor_latch、lethargy、recent_weight、growth_concern。未知字段传 {}。",
            },
        },
    ),
    "infant_growth_evaluate": _function_tool(
        "infant_growth_evaluate",
        "EVALUATE 只读工具：基于宝宝档案、生长记录和固定参考数据返回生长趋势规则结果。不是诊断；仅在用户提到身高、体重、增长或摄入是否足够时使用。",
        {
            "infant_id": _nullable({"type": "integer", "description": "可选宝宝 ID；不确定时传 null。"}),
            "as_of_time": _nullable({"type": "string", "description": "可选 ISO-8601 评估时间；不确定时传 null。"}),
        },
    ),
    "infant_growth_mutate": _function_tool(
        "infant_growth_mutate",
        "CREATE/UPDATE 写入工具：新增、修改或更新宝宝身高、体重、头围记录。只在用户明确提供测量值并确认保存/更新时调用；不要根据照片、描述或模型估算写入。有副作用；只有用户明确确认后才调用。",
        {
            "operation": {"type": "string", "enum": ["create", "update", "upsert_today"]},
            "growth_id": _nullable({"type": "integer", "description": "update 必填；create/upsert_today 传 null。"}),
            "infant_id": _nullable({"type": "integer", "description": "宝宝 ID；不确定传 null 使用当前用户第一个宝宝。"}),
            "height_cm": _nullable({"type": "number", "description": "身高/身长 cm；不修改传 null。"}),
            "weight_kg": _nullable({"type": "number", "description": "体重 kg；不修改传 null。"}),
            "head_cm": _nullable({"type": "number", "description": "头围 cm；不修改传 null。"}),
            "target_date": _nullable({**ISO_DATE, "description": "记录日期；不确定传 null，由运行时当前日期补齐。"}),
            "history_limit": {"type": "integer", "description": "写入后返回的历史记录条数，建议 5-10。"},
            "idempotency_key": {"type": "string"},
        },
    ),
    "milk_plan_preview": _function_tool(
        "milk_plan_preview",
        "PREVIEW 候选方案工具：按确定性规则生成追奶、稳奶或减奶计划草稿，不写数据库，并返回保存前校验结果。用于用户明确想要生成/调整奶量计划，或评估后需要给出计划草稿时；不要用于读取已有计划、单次 calendar 调整或设备使用指导。只有返回 plan_preview_ready 且 data.validation.valid=true 时才能展示确认保存；如用户给出目标，可同时返回目标校验结果。",
        {
            "plan_type": _nullable({"type": "string", "enum": ["increase_milk", "maintain_milk", "decrease_milk"]}),
            "plan_days": _nullable({"type": "integer"}),
            "custom_target_daily_ml": _nullable({"type": "number"}),
            "target_daily_ml": _nullable({"type": "number"}),
            "delta_ml": _nullable({"type": "number", "description": "未提供 target_daily_ml 时使用的每日增加或减少量。"}),
            "source_plan_id": _nullable({"type": "integer", "description": "基于已有计划重新生成时传计划 ID；普通新计划传 null。"}),
            "as_of_time": _nullable({"type": "string"}),
            "options": _nullable(
                {
                    **JSON_OBJECT_STRING,
                    "description": "字符串编码 JSON。可包含 prepared_assessment、prepared_growth_assessment、maternal_symptoms、infant_signals、observed_persistent_abnormal 或 medical_confirmation_confirmed。",
                }
            ),
        },
    ),
}
