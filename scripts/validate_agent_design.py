from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from momcozy_agent import ContextState, build_agent_request, execute_skill_runtime_tool, execute_tool
from momcozy_agent.server import _runtime_inputs_from_ag_ui


def main() -> None:
    initial_request = build_agent_request(
        {
            "user_message": "我需要 IBCLC 帮忙，我的吸奶量下降了。",
            "locale": "zh-CN",
            "timezone": "America/Los_Angeles",
            "message_sent_at": "2026-05-04T09:00:00-07:00",
        }
    )
    initial_tool_names = {tool["name"] for tool in initial_request["tools"] if tool["type"] == "function"}
    initial_tool_types = {tool["type"] for tool in initial_request["tools"]}
    initial_deferred_tool_names = _deferred_tool_names(initial_request)
    assert "metadata" not in initial_request, "request metadata should no longer carry loaded_skill_ids"
    assert [item.get("role", item.get("type")) for item in initial_request["input"]] == ["user"]
    assert len(initial_request["input"][0]["content"]) == 2
    assert initial_request["input"][0]["content"][0]["text"].startswith("request_context:")
    assert "## 可用 Skill" in initial_request["instructions"]
    assert "skill_manifests:" in initial_request["instructions"]
    assert "CoMate 全局规则" in initial_request["instructions"]
    assert "先接住人，再处理事" in initial_request["instructions"]
    assert "### 避免的表达" in initial_request["instructions"]
    assert "### 优秀回复示例" in initial_request["instructions"]
    assert "不要用“如果你有以下任何一种情况”开头列风险清单" in initial_request["instructions"]
    assert "不要像表单一样一次索要很多字段" in initial_request["instructions"]
    assert "恭喜你呀，真的到这个时候了" in initial_request["instructions"]
    assert "你先告诉我型号，或者发一张现在装到哪一步的照片" in initial_request["instructions"]
    assert "是否加载 skill，只根据用户当前意图、对话连续性，以及 skill manifest 中的 description 判断" in initial_request["instructions"]
    assert "是否调用工具，只根据当前可见工具的名称、description、schema 和用户目标判断" in initial_request["instructions"]
    assert "不能诊断疾病、开药、承诺治疗结果" in initial_request["instructions"]
    assert "普通陪伴和轻问答不需要为了显得完整而调用工具" in initial_request["instructions"]
    assert "static_agent_context:" not in initial_request["instructions"]
    assert "skill_selection: decide_from_skill_manifests" not in initial_request["instructions"]
    assert "tool_selection: decide_from_tool_descriptions" not in initial_request["instructions"]
    assert "默认不要提供列表式结构化选项菜单" not in initial_request["instructions"]
    assert "护士式信息剂量" not in initial_request["instructions"]
    assert "服务路径连续性协议" not in initial_request["instructions"]
    assert "skill_manifests:" not in initial_request["input"][0]["content"][0]["text"]
    assert initial_request["input"][0]["content"][1]["text"].startswith("user_message:")
    assert "tool_search" in initial_tool_types
    assert "load_skill" in initial_tool_names
    assert "ui_form_create" in initial_tool_names
    assert "ui_card_create" in initial_tool_names
    assert "ibclc_consult_card_create" in initial_tool_names
    assert "hospital_bag_cart_update" not in initial_tool_names
    assert "hospital_bag_cart_update" in initial_deferred_tool_names
    assert "hospital_bag_pump_recommend" not in initial_tool_names
    assert "hospital_bag_pump_recommend" in initial_deferred_tool_names
    assert "memory_search" not in initial_tool_names
    assert "handoff_summary_generate" not in initial_tool_names
    assert "handoff_summary_generate" in initial_deferred_tool_names
    assert "support_ticket_draft_create" not in initial_tool_names
    assert "support_ticket_draft_create" in initial_deferred_tool_names
    assert "device_manual_search" not in initial_tool_names
    assert "device_manual_search" in initial_deferred_tool_names
    assert "milk_snapshot_get" not in initial_tool_names
    assert "milk_snapshot_get" in initial_deferred_tool_names
    assert "milk_context_get" not in initial_deferred_tool_names
    assert "milk_plan_preview" not in initial_tool_names
    assert "milk_plan_preview" in initial_deferred_tool_names
    assert "milk_calendar_mutate" not in initial_tool_names
    assert "milk_calendar_mutate" in initial_deferred_tool_names
    assert "milk_calendar_adjustment_apply" not in initial_deferred_tool_names
    expected_milk_deferred_tools = {
        "milk_snapshot_get",
        "milk_assessment_evaluate",
        "infant_growth_evaluate",
        "milk_records_query",
        "milk_record_mutate",
        "milk_plan_query",
        "milk_plan_preview",
        "milk_plan_mutate",
        "milk_calendar_query",
        "milk_calendar_change_preview",
        "milk_calendar_mutate",
    }
    assert expected_milk_deferred_tools <= initial_deferred_tool_names
    for removed_tool_name in (
        "ibclc_book",
        "reminder_create",
        "memory_write",
        "knowledge_search",
        "support_ticket_create",
        "support_ticket_submit",
        "risk_evaluate",
        "feeding_log_query",
        "pumping_log_query",
        "trend_calculate",
        "milk_context_get",
        "milk_plan_apply",
        "milk_plan_list",
        "milk_plan_get",
        "milk_plan_delete",
        "milk_plan_update",
        "milk_plan_regenerate_preview",
        "milk_plan_target_validate",
        "milk_plan_validate",
        "milk_records_range_get",
        "milk_record_create",
        "milk_record_update",
        "milk_record_delete",
        "milk_today_overview_get",
        "milk_today_summary_get",
        "milk_today_tasks_shift",
        "milk_today_tasks_confirm",
        "milk_calendar_day_get",
        "milk_calendar_range_get",
        "milk_calendar_adjustment_preview",
        "milk_calendar_adjustment_apply",
        "milk_calendar_range_update",
        "milk_calendar_item_update",
        "milk_calendar_item_delete",
    ):
        assert removed_tool_name not in initial_tool_names
        assert removed_tool_name not in initial_deferred_tool_names

    image_data_url = "data:image/png;base64,iVBORw0KGgo="
    image_payload = {
        "threadId": "thread_image_test",
        "userId": "user_demo",
        "state": {
            "user_id": "user_demo",
            "locale": "zh-CN",
            "timezone": "America/Los_Angeles",
            "message_sent_at": "2026-05-04T09:00:00-07:00",
        },
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "帮我看看这个吸奶器配件怎么装"},
                    {"type": "image", "image_url": image_data_url, "mime_type": "image/png", "name": "pump.png", "size": 24},
                ],
            }
        ],
    }
    image_inputs = _runtime_inputs_from_ag_ui(image_payload)
    assert image_inputs["user_id"] == "user_demo"
    assert image_inputs["user_profile"]["user_id"] == "user_demo"
    assert image_inputs["user_message"] == "帮我看看这个吸奶器配件怎么装"
    assert image_inputs["images"][0]["image_url"] == image_data_url
    image_request = build_agent_request(image_inputs)
    image_content = image_request["input"][0]["content"]
    assert image_content[0]["type"] == "input_text"
    assert image_content[1]["type"] == "input_text"
    assert image_content[2] == {"type": "input_image", "image_url": image_data_url, "detail": "auto"}

    loaded = execute_skill_runtime_tool("load_skill", {"skill_id": "milk-management"})
    assert "# 奶量管理" in loaded["skill_md"]
    assert "tool_names" not in loaded, "load_skill must not advertise per-skill tool_names"
    assert loaded["id"] == "milk-management"
    assert loaded["name"] == "milk-management"
    assert isinstance(loaded["description"], str) and loaded["description"]

    birth_prep_skill = execute_skill_runtime_tool("load_skill", {"skill_id": "birth-prep"})
    assert "临产表达的回应节奏" in birth_prep_skill["skill_md"]
    assert "生产全过程计划" in birth_prep_skill["skill_md"]
    assert "references/birth-journey-plan.md" in birth_prep_skill["skill_md"]
    assert "意图优先级链" in birth_prep_skill["skill_md"]
    assert "物品词优先于阶段词" in birth_prep_skill["skill_md"]
    assert "上一轮明确承接能力优先" in birth_prep_skill["skill_md"]
    assert "宽泛注意/准备问题默认轻问答" in birth_prep_skill["skill_md"]
    assert "产前普通问答的信息剂量" in birth_prep_skill["skill_md"]
    assert "最多问 1-2 个问题" in birth_prep_skill["skill_md"]
    assert "生产计划歧义" in birth_prep_skill["skill_md"]
    assert "第一反应不是清单" in birth_prep_skill["skill_md"]
    assert "最终回复里先轻柔祝贺" in birth_prep_skill["skill_md"]
    assert "最终回复先表达祝贺和情感共鸣" in birth_prep_skill["skill_md"]
    assert "先轻柔祝贺" in birth_prep_skill["skill_md"]
    assert "skill 流程连续性" in birth_prep_skill["skill_md"]
    assert "待产包入口确认" in birth_prep_skill["skill_md"]
    assert "默认先不调用 `ui_form_create`" in birth_prep_skill["skill_md"]
    assert "已有信息会先填好" in birth_prep_skill["skill_md"]
    assert "字段 `default_value`" in birth_prep_skill["skill_md"]
    assert "再调用 `hospital_bag_form_create`" in birth_prep_skill["skill_md"]
    journey_plan_reference = execute_skill_runtime_tool(
        "read_skill_file",
        {"skill_id": "birth-prep", "kind": "references", "path": "references/birth-journey-plan.md"},
    )["content"]
    assert "生产全过程计划是一份阶段路线图" in journey_plan_reference
    assert "待产包、入院包或住院包物品清单" in journey_plan_reference
    assert "给医生、护士或助产士看的分娩沟通卡" in journey_plan_reference
    assert "默认不用表单" in journey_plan_reference
    assert "回到主 `SKILL.md` 的歧义澄清规则" in journey_plan_reference
    assert "通用安全边界以主 `SKILL.md` 为准" in journey_plan_reference
    assert "不要为了这个能力调用 `ui_form_create` 或 `ui_card_create`" in birth_prep_skill["skill_md"]
    assert "孕晚期准备期" in journey_plan_reference
    assert "临产预备期" in journey_plan_reference
    assert "临产识别期" in journey_plan_reference
    assert "住院分娩期" in journey_plan_reference
    assert "产后启动期" in journey_plan_reference
    assert "默认用移动端友好的阶段卡片呈现路线图" in journey_plan_reference
    assert "|  |  |" in journey_plan_reference
    assert "第二列固定按这三段写：`阶段目标`、`注意事项`、`准备动作`" in journey_plan_reference
    assert "每个阶段只集中处理一组准备事项" in journey_plan_reference
    assert "不要在结尾同时抛出待产包、分娩沟通卡、提醒、医院问题清单等多个并列选项" in journey_plan_reference
    birth_plan_reference = execute_skill_runtime_tool(
        "read_skill_file",
        {"skill_id": "birth-prep", "kind": "references", "path": "references/birth-plan-card.md"},
    )["content"]
    assert "分娩沟通卡不是完整医疗计划" in birth_plan_reference
    assert "表单字段、字段顺序、选项" in birth_plan_reference
    assert "把可靠信息写入 `birth_plan_form_create.default_values`" in birth_plan_reference
    assert "调用 `birth_plan_form_create`" in birth_plan_reference
    assert "support_people" in birth_plan_reference
    assert "不要再调用 `ui_form_create` 手写分娩沟通卡字段" in birth_plan_reference
    assert "直接调用 `birth_plan_card_create`" in birth_plan_reference
    assert "不要让 LLM 自己生成分娩沟通卡 `card_json`" in birth_plan_reference
    assert "工具返回 `assistant_followup`" in birth_plan_reference
    hospital_bag_reference = execute_skill_runtime_tool(
        "read_skill_file",
        {"skill_id": "birth-prep", "kind": "references", "path": "references/hospital-bag-service.md"},
    )["content"]
    assert "待产包服务不是让用户自己从长清单里筛东西" in hospital_bag_reference
    assert "表单字段、字段顺序、卡片 schema" in hospital_bag_reference
    assert "先自然邀约，不直接创建表单" in hospital_bag_reference
    assert "把可靠信息写入 `hospital_bag_form_create.default_values`" in hospital_bag_reference
    assert "调用 `hospital_bag_form_create`" in hospital_bag_reference
    assert "不要再调用 `ui_form_create` 手写待产包字段" in hospital_bag_reference
    assert "直接调用 `hospital_bag_card_create`" in hospital_bag_reference
    assert "不要让 LLM 自己生成待产包 `card_json`" in hospital_bag_reference
    assert "工具返回 `assistant_followup`" in hospital_bag_reference

    prefilled_hospital_bag_form = execute_tool(
        "ui_form_create",
        {
            "form_id": "hospital_bag_intake",
            "title": "信息采集",
            "description": "先确认几件关键背景，帮你少带错、少漏带。",
            "submit_label": "生成我的卡片",
            "fields": [
                {
                    "id": "due_date_or_week",
                    "label": "基本信息｜预产期或当前孕周",
                    "type": "text",
                    "required": True,
                    "default_value": "37 周",
                    "help_text": "用于判断准备阶段、清单密度和优先级。",
                },
                {
                    "id": "birth_path",
                    "label": "生产信息｜计划分娩方式",
                    "type": "select",
                    "required": True,
                    "options": ["顺产", "剖宫产", "未确定"],
                    "default_value": "顺产",
                    "help_text": "如果还没确定，可以选择“未确定”。",
                },
                {
                    "id": "hospital_rules_or_notes",
                    "label": "医院信息｜已知医院规则或注意事项",
                    "type": "textarea",
                    "required": False,
                },
                {
                    "id": "existing_checklist_or_photo_note",
                    "label": "医院信息｜已有医院清单或产检材料说明",
                    "type": "textarea",
                    "required": False,
                },
            ],
        },
        {
            "user_message": "好",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-05-04T20:00:00+08:00",
        },
    )
    prefilled_fields = {field["id"]: field for field in prefilled_hospital_bag_form["form"]["fields"]}
    assert prefilled_hospital_bag_form["form"]["description"] == ""
    assert prefilled_fields["due_date_or_week"]["default_value"] == "37 周"
    assert prefilled_fields["birth_path"]["default_value"] == "顺产"
    assert all("help_text" not in field for field in prefilled_fields.values())
    assert "hospital_rules_or_notes" not in prefilled_fields
    assert "existing_checklist_or_photo_note" not in prefilled_fields

    pump_skill = execute_skill_runtime_tool("load_skill", {"skill_id": "device-guidance"})
    assert "references/air1/faq.md" in pump_skill["references"]
    assert "references/air1/manual.md" in pump_skill["references"]
    assert "设备资料检索工具" in pump_skill["skill_md"]

    air1_manual = execute_tool(
        "device_manual_search",
        {"model": "Air1", "query": "Air1 怎么清洗和消毒？", "topic": "cleaning", "max_results": 3},
        {"user_message": "Air1 怎么清洗？", "locale": "zh-CN", "timezone": "America/Los_Angeles", "message_sent_at": "2026-05-04T09:00:00-07:00"},
    )
    assert air1_manual["status"] in {"manual_loaded", "manual_loaded_with_faq"}
    assert air1_manual["manual"]
    assert "清洁" in air1_manual["manual"]["content"]
    assert "消毒" in air1_manual["manual"]["content"]

    unsupported_device = execute_tool(
        "device_manual_search",
        {"model": "unknown", "query": "Air1 第一次开箱怎么用？", "topic": "unboxing", "max_results": 2},
        {"user_message": "Air1 第一次开箱怎么用？", "locale": "zh-CN", "timezone": "America/Los_Angeles", "message_sent_at": "2026-05-04T09:00:00-07:00"},
    )
    assert unsupported_device["status"] == "unsupported_model"
    assert unsupported_device["results"] == []

    low_confidence_device = execute_tool(
        "device_manual_search",
        {"model": "Air1", "query": "完全无关的问题", "topic": "other", "max_results": 2},
        {"user_message": "完全无关的问题", "locale": "zh-CN", "timezone": "America/Los_Angeles", "message_sent_at": "2026-05-04T09:00:00-07:00"},
    )
    assert low_confidence_device["status"] == "manual_loaded"
    assert low_confidence_device["match_score"] == 0
    assert low_confidence_device["manual"], "manual should still load even when FAQ retrieval has no match"
    assert low_confidence_device["faq_results"] == []

    hospital_bag_card = execute_tool(
        "ui_card_create",
        {
            "card_type": "hospital_bag_card",
            "schema_version": "1.0",
            "card_json": {
                "title": "待产包",
                "packing_groups": [
                    {"group_id": "documents", "title": "证件资料包", "items": []},
                    {"group_id": "baby", "title": "宝宝出院包", "items": []},
                ],
            },
        },
        {"user_message": "confirmed_form_data:\n{}", "locale": "zh-CN", "timezone": "America/Los_Angeles", "message_sent_at": "2026-05-04T09:00:00-07:00"},
    )
    card_json = hospital_bag_card["card"]["card_json"]
    postpartum_groups = [group for group in card_json["packing_groups"] if group.get("group_id") == "postpartum_home_first_week"]
    assert postpartum_groups, "hospital bag card should include a postpartum home group"
    pump_item_index = next(
        index
        for index, item in enumerate(postpartum_groups[0]["items"])
        if "吸奶" in item["label"]
    )
    assert pump_item_index < 4, "吸奶器应在紧凑版卡片中保持可见"
    assert "assistant_followup" in hospital_bag_card
    assert "/hospital-bag-cart" in hospital_bag_card["assistant_followup"]["message"]
    assert "一键打包下单页" in hospital_bag_card["assistant_followup"]["message"]

    birth_plan_card = execute_tool(
        "ui_card_create",
        {
            "card_type": "birth_plan_card",
            "schema_version": "1.0",
            "card_json": {
                "title": "分娩沟通卡",
                "owner": {"due_date_or_week": "37 周", "support_person": ["伴侣"]},
                "birth_preferences": {"birth_path": "顺产", "environment": "待确认"},
                "communication_preferences": {
                    "decision_style": "先解释",
                    "consent_preference": "必须征求同意",
                    "language_or_explanation_preference": "语言简单清楚",
                },
                "questions_for_hospital": ["陪同规则？", "入院流程？", "宝宝护理流程？", "住院天数？"],
                "medical_notes": {"conditions_or_constraints": "不应保留模型推断出的医疗备注"},
                "missing_fields": ["待确认"],
            },
        },
        {
            "user_message": (
                "confirmed_form_data:\n"
                '{"due_date_or_week":"37 周","birth_path":"顺产","top_priorities":"希望每一步先解释；希望伴侣参与重要决定",'
                '"communication_preferences":["干预前先解释","先征求同意"],"support_person":"伴侣",'
                '"medical_notes":"青霉素过敏"}'
            ),
            "locale": "zh-CN",
            "timezone": "America/Los_Angeles",
            "message_sent_at": "2026-05-04T09:00:00-07:00",
        },
    )
    birth_card_json = birth_plan_card["card"]["card_json"]
    assert birth_card_json["top_priorities"] == ["希望每一步先解释", "希望伴侣参与重要决定"]
    assert birth_card_json["overview"]["birth_path"] == "顺产"
    assert len(birth_card_json["questions_for_hospital"]) >= 4
    assert birth_card_json["medical_notes"] == ["青霉素过敏"]
    assert "missing_fields" not in birth_card_json
    assert "assistant_followup" in birth_plan_card
    assert "提前和医院确认" in birth_plan_card["assistant_followup"]["message"]

    support_ticket_draft = execute_tool(
        "support_ticket_draft_create",
        {
            "issue_type": "设备故障",
            "issue_summary": "吸奶器反复无法启动，用户已经重装配件仍未解决。",
            "product_model": "M5",
            "order_number": None,
            "purchase_channel": None,
            "user_contact": None,
            "troubleshooting_done": ["重启设备", "重新安装配件"],
            "urgency": "high",
            "user_emotion": "挫败",
            "attachments_note": "用户已上传设备图片",
        },
        {"user_message": "我真的搞不定这个吸奶器了", "locale": "zh-CN", "timezone": "America/Los_Angeles", "message_sent_at": "2026-05-04T09:00:00-07:00"},
    )
    assert support_ticket_draft["status"] == "ticket_draft_created"
    assert support_ticket_draft["ticket"]["issue_type"] == "设备故障"
    assert support_ticket_draft["ticket"]["troubleshooting_done"] == ["重启设备", "重新安装配件"]

    ibclc_card = execute_tool(
        "ibclc_consult_card_create",
        {
            "consultant_name": None,
            "consultant_bio": None,
            "chat_url": None,
        },
        {"user_message": "我想找 IBCLC 看看乳头疼", "locale": "zh-CN", "timezone": "America/Los_Angeles", "message_sent_at": "2026-05-04T09:00:00-07:00"},
    )
    assert ibclc_card["status"] == "ibclc_consult_card_created"
    assert ibclc_card["card"]["card_type"] == "ibclc_consult_card"
    assert ibclc_card["card"]["consultant"]["name"] == "Emily Chen"
    assert ibclc_card["card"]["consultant"]["bio"]
    assert ibclc_card["card"]["chat"]["url"] == "/ibclc-chat.html"

    loaded_request = build_agent_request(
        {
            "user_message": "我需要 IBCLC 帮忙，我的吸奶量下降了。",
            "locale": "zh-CN",
            "timezone": "America/Los_Angeles",
            "message_sent_at": "2026-05-04T09:01:00-07:00",
        },
        {"loaded_skill_ids": ["milk-management"]},
    )
    loaded_tool_names = {tool["name"] for tool in loaded_request["tools"] if tool["type"] == "function"}
    loaded_deferred_tool_names = _deferred_tool_names(loaded_request)
    assert initial_request["tools"] == loaded_request["tools"]
    assert "handoff_summary_generate" not in loaded_tool_names
    assert "handoff_summary_generate" in loaded_deferred_tool_names
    assert "pumping_log_query" not in loaded_tool_names
    assert "pumping_log_query" not in loaded_deferred_tool_names
    assert "loaded_skill_context:" in loaded_request["input"][0]["content"][0]["text"]
    assert "milk-management/SKILL.md 已在当前会话中读取过" in loaded_request["input"][0]["content"][0]["text"]
    assert "# 奶量管理" not in loaded_request["input"][0]["content"][0]["text"]

    context_state = ContextState()
    first_stateful_request = build_agent_request(
        {
            "user_message": "帮我准备待产包",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-05-04T20:00:00+08:00",
        },
        {"context_state": context_state},
    )
    first_context = first_stateful_request["input"][0]["content"][0]["text"]
    assert first_context.startswith("request_context:")
    assert "locale: zh-CN" in first_context
    assert "timezone: Asia/Shanghai" in first_context
    assert "message_sent_at: 2026-05-04T20:00:00+08:00" in first_context
    assert "current_date:" not in first_context
    assert context_state.environment_sent

    second_stateful_request = build_agent_request(
        {
            "user_message": "我现在是 36 周",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-05-04T20:01:00+08:00",
            "previous_response_id": "resp_previous",
        },
        {"context_state": context_state},
    )
    second_context = second_stateful_request["input"][0]["content"][0]["text"]
    assert second_context.startswith("request_context:")
    assert "skill_manifests:" not in second_context
    assert "## 可用 Skill" not in second_context
    assert "loaded_skill_context:" not in second_context
    assert "locale:" not in second_context
    assert "timezone:" not in second_context
    assert "message_sent_at: 2026-05-04T20:01:00+08:00" in second_context

    loaded_stateful_request = build_agent_request(
        {
            "user_message": "继续",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-05-04T20:02:00+08:00",
            "previous_response_id": "resp_previous",
        },
        {"context_state": context_state, "loaded_skill_ids": ["birth-prep"]},
    )
    loaded_context = loaded_stateful_request["input"][0]["content"][0]["text"]
    assert "<loaded_skill_context_delta>" not in loaded_context
    assert "<loaded_skill_context>" not in loaded_context
    assert "loaded_skill_context:" in loaded_context
    assert "birth-prep/SKILL.md 已在当前会话中读取过" in loaded_context
    assert "不要重复调用 load_skill" in loaded_context
    assert "# Birth Prep" not in loaded_context

    reference_state = ContextState()
    reference_state.loaded_references.append(
        "birth-prep/references/birth-plan-card.md 已在当前会话中读取过；连续同一子服务任务优先复用，不要重复调用 read_skill_file，除非用户切换到新 reference 或上下文不足。"
    )
    reference_context_request = build_agent_request(
        {
            "user_message": "继续生成分娩沟通卡",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-05-04T20:03:00+08:00",
            "previous_response_id": "resp_previous",
        },
        {"context_state": reference_state, "loaded_skill_ids": ["birth-prep"]},
    )
    reference_context = reference_context_request["input"][0]["content"][0]["text"]
    assert "loaded_reference_context:" in reference_context
    assert "birth-prep/references/birth-plan-card.md 已在当前会话中读取过" in reference_context
    assert "不要重复调用 read_skill_file" in reference_context

    profile_state = ContextState()
    build_agent_request(
        {
            "user_message": "我想做 birth plan",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-05-04T20:00:00+08:00",
            "user_profile": {
                "pregnancy_status": "pregnant",
                "due_date": "2026-06-10",
                "language": "zh-CN",
            },
        },
        {"context_state": profile_state},
    )
    profile_context_request = build_agent_request(
        {
            "user_message": "继续",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-05-04T20:01:00+08:00",
            "previous_response_id": "resp_previous",
            "user_profile": {
                "pregnancy_status": "pregnant",
                "due_date": "2026-06-10",
                "language": "zh-CN",
            },
        },
        {"context_state": profile_state},
    )
    profile_context = profile_context_request["input"][0]["content"][0]["text"]
    assert "<context_ledger_delta>" not in profile_context
    assert "<context_invalidations>" not in profile_context
    assert "user_profile.due_date" not in profile_context
    assert "2026-06-10" not in profile_context

    service_skills = execute_skill_runtime_tool("list_skills", {})["skills"]
    service_skill_ids = {skill["id"] for skill in service_skills}
    assert service_skill_ids == {"birth-prep", "milk-management", "emotion-support", "device-guidance"}
    for skill in service_skills:
        allowed_manifest_keys = {"id", "name", "description"}
        assert set(skill.keys()) <= allowed_manifest_keys, (
            f"Unexpected manifest keys for {skill['id']}: {sorted(skill.keys())}"
        )
        skill_doc = execute_skill_runtime_tool("load_skill", {"skill_id": skill["id"]})["skill_md"]
        assert "全局声音契约" in skill_doc, (
            f"{skill['id']} must explicitly inherit the global response style"
        )
        assert "流程连续性" in skill_doc, (
            f"{skill['id']} must define how current service paths continue"
        )

    _assert_strict_function_schemas(initial_request["tools"])

    print("Agent design checks passed.")


def _assert_strict_function_schemas(tools: list[dict]) -> None:
    for tool in tools:
        if tool.get("type") == "function":
            _assert_strict_object_schema(tool["parameters"], path=tool["name"])
        elif tool.get("type") == "namespace":
            for child in tool.get("tools", []):
                _assert_strict_object_schema(child["parameters"], path=child["name"])


def _assert_strict_object_schema(schema: dict, *, path: str) -> None:
    types = schema.get("type")
    if types == "object" or (isinstance(types, list) and "object" in types):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        assert sorted(required) == sorted(properties.keys()), (
            f"strict mode requires every property to be required at {path}: {sorted(properties.keys())} vs {sorted(required)}"
        )
        assert schema.get("additionalProperties") is False, f"strict mode requires additionalProperties=false at {path}"
        for prop_name, prop_schema in properties.items():
            if isinstance(prop_schema, dict):
                _assert_strict_object_schema(prop_schema, path=f"{path}.{prop_name}")
    if types == "array" or (isinstance(types, list) and "array" in types):
        items = schema.get("items")
        if isinstance(items, dict):
            _assert_strict_object_schema(items, path=f"{path}[]")


def _deferred_tool_names(request: dict) -> set[str]:
    names: set[str] = set()
    for tool in request["tools"]:
        if tool.get("type") != "namespace":
            continue
        for child_tool in tool.get("tools", []):
            if child_tool.get("defer_loading") and child_tool.get("name"):
                names.add(child_tool["name"])
    return names


if __name__ == "__main__":
    main()
