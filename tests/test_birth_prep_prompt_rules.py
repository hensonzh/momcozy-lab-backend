from __future__ import annotations

import unittest
from pathlib import Path

from momcozy_agent.agents import _record_birth_prep_tool_state, model_tool_output
from momcozy_agent.contexts import (
    ContextState,
    build_request_context,
    hospital_bag_slots,
    merge_extracted_birth_prep_slots,
    merge_hospital_bag_slots,
)
from momcozy_agent.tool_handlers.cards import (
    create_birth_journey_plan_card,
    create_hospital_bag_card,
    create_hospital_bag_form,
    create_birth_plan_form,
    create_labor_communication_card,
)
from momcozy_agent.tool_registry import DEFERRED_TOOL_NAMESPACES
from momcozy_agent.tool_schemas import FUNCTION_TOOLS


ROOT = Path(__file__).resolve().parents[1]


class BirthPrepPromptRuleTests(unittest.TestCase):
    def test_birth_prep_collection_style_avoids_repeating_known_slots(self) -> None:
        skill = (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("采用 slot-filling 风格", skill)
        self.assertIn("不要在下一轮重述上轮已经采集到的信息", skill)
        self.assertIn("每轮最多问一个缺失字段", skill)
        self.assertIn("调用 `birth_journey_intake_manage`", skill)
        self.assertIn("不要自己在聊天里维护字段清单", skill)
        self.assertIn("工具返回 `ready_to_generate` 后", skill)
        self.assertIn("工具返回 `ready_to_generate` 后，直接调用 `birth_journey_plan_card_create`", skill)
        self.assertNotIn("references/birth-journey-plan.md", skill)
        self.assertIn("当前孕周、是否 IVF（体外受精）", skill)
        self.assertNotIn("一次性收集末次月经、预产期、当前孕周", skill)

    def test_broad_week_preparation_question_is_not_shopping_by_default(self) -> None:
        skill = (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("单独的“准备什么/该准备什么”不算物品词", skill)
        self.assertIn("孕 26 周该准备什么", skill)
        self.assertIn("不默认进入购买、下单或待产包语义", skill)
        self.assertIn("产检问题、医院流程、家庭照护和产后支持沟通", skill)
        self.assertIn("不要主动说买齐、购买、下单或待产包", skill)

    def test_light_birth_prep_answer_must_end_with_one_service_when_intent_is_unclear(self) -> None:
        skill = (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("如果妈妈没有表达非常清晰的问题意图", skill)
        self.assertIn("最终回复的最后一句必须引导三项产前服务中的一个", skill)
        self.assertIn("不要停在纯建议，也不要同时列出三个服务", skill)
        self.assertIn("完全没有取向时默认引导孕期计划", skill)
        self.assertIn("临近生产、准备去医院或想先把眼前事情稳住时引导待产包清单", skill)
        self.assertIn("提到医院、医生、护士、陪产、生产偏好或产房沟通时引导分娩沟通单", skill)
        self.assertIn("轻问答的服务引导只做邀约，不直接调用工具", skill)

    def test_birth_journey_generation_does_not_duplicate_pre_tool_and_final_summary(self) -> None:
        skill = (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8")

        self.assertNotIn("可以把从现在到入院、分娩、出院和产后 42 天按阶段理清楚", skill)
        self.assertIn("可以先把你当前孕周最该做的事理清楚", skill)
        self.assertIn("调用 birth_journey_plan_card_create 前不要输出给用户可见的过渡文本", skill)
        self.assertIn("直接调用工具", skill)
        self.assertIn("不要在工具调用前展开阶段、当前重点、温馨提醒、接下来建议或我能帮你做", skill)
        self.assertIn("只在工具调用后的最终回复里表达一次", skill)
        self.assertNotIn("好，我来帮你整理孕期计划", skill)
        self.assertNotIn("最多只说一句简短过渡", skill)
        self.assertNotIn("首先对完整阶段进行一个口头概述", skill)
        self.assertIn("STATE_D: 删除孕期计划", skill)
        self.assertIn("孕期计划", skill)
        self.assertIn("已经存在 active 孕期计划", skill)
        self.assertIn("不要再次调用 `birth_journey_plan_card_create` 重新生成", skill)
        self.assertIn("birth_journey_plan_delete", skill)
        self.assertIn("confirmed=true", skill)

    def test_birth_prep_runtime_wording_does_not_expose_prefill_jargon(self) -> None:
        runtime_text = "\n".join(
            [
                (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8"),
                str(DEFERRED_TOOL_NAMESPACES["birth_prep"]["description"]),
                str(create_hospital_bag_form({"default_values": {"due_date_or_week": "35 周"}}, {"user_message": ""})),
            ]
        )

        self.assertNotIn("预采集", runtime_text)

    def test_birth_prep_user_visible_wording_avoids_card_as_service_name(self) -> None:
        birth_journey_missing = create_birth_journey_plan_card({"plan_context": {}}, {"user_message": ""})
        birth_journey_compact = model_tool_output(
            {
                "ok": True,
                "tool_name": "birth_journey_plan_card_create",
                "result": birth_journey_missing,
            }
        )
        runtime_text = "\n".join(
            [
                (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8"),
                str(DEFERRED_TOOL_NAMESPACES["birth_prep"]["description"]),
                str(FUNCTION_TOOLS["birth_plan_form_create"]),
                str(FUNCTION_TOOLS["labor_communication_card_create"]),
                str(FUNCTION_TOOLS["birth_journey_plan_card_create"]),
                str(FUNCTION_TOOLS["hospital_bag_form_create"]),
                str(FUNCTION_TOOLS["hospital_bag_card_create"]),
                str(create_hospital_bag_card({}, {"user_message": ""})),
                str(create_labor_communication_card({}, {"user_message": ""})),
                str(birth_journey_compact),
            ]
        )

        for phrase in (
            "待产包" + "卡片",
            "分娩沟通" + "卡片",
            "孕期计划" + "卡片",
            "分娩沟通" + "卡",
            "产房沟通优先级" + "卡片",
            "这张" + "卡",
        ):
            self.assertNotIn(phrase, runtime_text)

        self.assertIn("待产包清单", runtime_text)
        self.assertIn("分娩沟通单", runtime_text)
        self.assertIn("孕期计划", runtime_text)

    def test_birth_prep_slots_merge_async_extracted_confirmed_answer(self) -> None:
        state = ContextState()

        accepted = merge_extracted_birth_prep_slots(
            state,
            [
                {
                    "field_id": "top_worries",
                    "value": ["怕漏带", "怕住院不舒服", "怕母乳喂不好"],
                    "evidence": "怕漏带、怕住院不舒服、怕母乳喂不好",
                    "confidence": 0.94,
                }
            ],
            turn_id=1,
            run_id="run-slot-test",
            updated_at="2026-06-17T09:00:00+08:00",
        )

        context = build_request_context({"user_message": "继续", "locale": "zh-CN"}, state, ["birth-prep"])

        self.assertEqual(accepted["top_worries"], ["怕漏带", "怕住院不舒服", "怕母乳喂不好"])
        self.assertIn("birth_prep_context:", context)
        self.assertIn("top_worries=怕漏带、怕住院不舒服、怕母乳喂不好", context)
        self.assertIn("创建产前表单或待产包表单时复用这些字段", context)
        self.assertNotIn("hospital_bag_next_field", context)
        record = state.birth_prep_slots["hospital_bag"]["top_worries"]
        self.assertEqual(record["status"], "confirmed")
        self.assertEqual(record["source"], "user_text")

    def test_birth_prep_user_text_updates_latest_slot_value(self) -> None:
        state = ContextState()

        merge_extracted_birth_prep_slots(
            state,
            [{"field_id": "due_date_or_week", "value": "孕30周", "evidence": "孕30周", "confidence": 0.9}],
            turn_id=1,
        )
        merge_extracted_birth_prep_slots(
            state,
            [{"field_id": "due_date_or_week", "value": "孕32周", "evidence": "孕32周", "confidence": 0.9}],
            turn_id=2,
        )

        self.assertEqual(hospital_bag_slots(state)["due_date_or_week"], "孕32周")
        self.assertIn(
            "due_date_or_week=孕32周",
            build_request_context({"user_message": "继续", "locale": "zh-CN"}, state, ["birth-prep"]),
        )

    def test_stale_slot_extraction_does_not_overwrite_newer_tool_value(self) -> None:
        state = ContextState()
        state.slot_turn_index = 2

        merge_hospital_bag_slots(state, {"due_date_or_week": "孕33周"})
        merge_extracted_birth_prep_slots(
            state,
            [{"field_id": "due_date_or_week", "value": "孕32周", "evidence": "孕32周", "confidence": 0.9}],
            turn_id=1,
        )

        self.assertEqual(hospital_bag_slots(state)["due_date_or_week"], "孕33周")

    def test_city_or_country_rejects_long_sentence_prefill(self) -> None:
        state = ContextState()

        merge_extracted_birth_prep_slots(
            state,
            [
                {
                    "field_id": "city_or_country",
                    "value": "我现在住在深圳，最近想了解待产包和孕期计划怎么安排。",
                    "evidence": "我现在住在深圳，最近想了解待产包和孕期计划怎么安排。",
                    "confidence": 0.8,
                },
                {"field_id": "city_or_country", "value": "深圳", "evidence": "住在深圳", "confidence": 0.95},
            ],
            turn_id=1,
        )

        self.assertEqual(hospital_bag_slots(state)["city_or_country"], "深圳")

    def test_birth_prep_context_is_injected_when_service_domain_is_empty(self) -> None:
        state = ContextState()

        merge_extracted_birth_prep_slots(
            state,
            [{"field_id": "due_date_or_week", "value": "孕32周", "evidence": "孕32周", "confidence": 0.9}],
            turn_id=1,
        )

        context = build_request_context({"user_message": "继续", "locale": "zh-CN"}, state, ["milk-management"])

        self.assertIn("birth_prep_context:", context)
        self.assertIn("due_date_or_week=孕32周", context)

    def test_birth_prep_context_is_suppressed_for_milk_management_domain(self) -> None:
        state = ContextState()
        state.active_service_domain = "milk_management"

        merge_extracted_birth_prep_slots(
            state,
            [{"field_id": "due_date_or_week", "value": "孕32周", "evidence": "孕32周", "confidence": 0.9}],
            turn_id=1,
        )

        context = build_request_context(
            {
                "user_message": "继续",
                "locale": "zh-CN",
                "user_profile": {"birth_prep_due_date_or_week": "孕32周"},
            },
            state,
            ["milk-management"],
        )

        self.assertNotIn("birth_prep_context:", context)
        self.assertNotIn("birth_prep_profile_context:", context)
        self.assertNotIn("due_date_or_week=孕32周", context)

    def test_explicit_service_domain_overrides_session_domain_for_context_gate(self) -> None:
        state = ContextState()
        state.active_service_domain = "birth_prep"

        merge_extracted_birth_prep_slots(
            state,
            [{"field_id": "due_date_or_week", "value": "孕32周", "evidence": "孕32周", "confidence": 0.9}],
            turn_id=1,
        )

        context = build_request_context(
            {"user_message": "继续", "locale": "zh-CN", "service_domain": "milk-management"},
            state,
            ["birth-prep"],
        )

        self.assertNotIn("birth_prep_context:", context)
        self.assertNotIn("due_date_or_week=孕32周", context)

    def test_hospital_bag_flow_uses_form_without_three_dialogue_questions(self) -> None:
        skill = (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8")
        schema_text = str(FUNCTION_TOOLS["hospital_bag_form_create"])

        self.assertIn("用户确认开始后，直接调用 `hospital_bag_form_create`", skill)
        self.assertIn("不要先用聊天追问三项基础信息", skill)
        self.assertIn("不要先用自然对话收集 3 个字段", skill)
        self.assertIn("用户确认开始待产包整理后可直接调用", schema_text)

    def test_hospital_bag_entry_invite_does_not_reference_removed_fields(self) -> None:
        skill = (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8")
        entry_section = skill.split("### STATE_A: 进入与邀约", 1)[1].split("### STATE_B: 收集信息", 1)[0]
        positive_section = entry_section.split("入口邀约不要说", 1)[0]

        self.assertIn("会按你的孕周、分娩方式、喂养意向、产后支持和最担心的事来取舍", entry_section)
        self.assertIn("入口邀约不要说", entry_section)
        for phrase in (
            "按你的医院",
            "医院情况",
            "住院天数",
            "家里已有物品",
            "已有物品",
        ):
            self.assertNotIn(phrase, positive_section)
            self.assertIn(phrase, entry_section)
        self.assertNotIn("医院要求不一致", entry_section)

    def test_labor_communication_flow_lives_in_skill_not_reference(self) -> None:
        skill = (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8")
        schema_text = "\n".join(
            [
                str(FUNCTION_TOOLS["birth_plan_form_create"]),
                str(FUNCTION_TOOLS["labor_communication_card_create"]),
            ]
        )

        self.assertIn("服务3：[SERVICE S3] “分娩沟通单” 服务", skill)
        self.assertIn("用户确认开始后，调用 `birth_plan_form_create`", skill)
        self.assertIn("不要手写分娩沟通单字段", skill)
        self.assertIn("看到 `confirmed_form_data form_id=\"birth_plan_card_intake\"` 后", skill)
        self.assertIn("直接调用 `labor_communication_card_create`", skill)
        self.assertIn("不要让 LLM 自己生成分娩沟通单 `card_json`", skill)
        self.assertIn("创建分娩沟通单信息采集表单", schema_text)
        self.assertIn("生成前端可渲染的分娩沟通单", schema_text)

    def test_hospital_bag_slots_do_not_capture_budget_preference(self) -> None:
        state = ContextState()

        merge_extracted_birth_prep_slots(
            state,
            [{"field_id": "budget_preference", "value": "高预算", "evidence": "高预算", "confidence": 0.8}],
            turn_id=1,
        )

        context = build_request_context({"user_message": "继续", "locale": "zh-CN"}, state, ["birth-prep"])

        self.assertNotIn("budget_preference", hospital_bag_slots(state))
        self.assertNotIn("budget_preference", context)

    def test_birth_journey_week_reused_by_hospital_bag_form(self) -> None:
        state = ContextState()
        plan_context = {
            "due_date_or_week": "孕30周",
            "birth_path": "顺产",
            "support_person": "伴侣",
            "first_birth": "跳过",
            "fetus_count": "跳过",
            "age": "跳过",
            "city_or_country": "跳过",
            "checkup_status": "跳过",
            "current_symptoms": "跳过",
            "risk_factors": "跳过",
            "lifestyle_context": "跳过",
            "feeding_ibclc_context": "跳过",
        }
        tool_result = create_birth_journey_plan_card({"plan_context": plan_context}, {"user_message": ""})

        _record_birth_prep_tool_state(
            state,
            "birth_journey_plan_card_create",
            {"plan_context": plan_context},
            {
                "ok": True,
                "tool_name": "birth_journey_plan_card_create",
                "result": tool_result,
            },
        )

        context = build_request_context({"user_message": "继续", "locale": "zh-CN"}, state, ["birth-prep"])
        self.assertIn("due_date_or_week=孕30周", context)
        self.assertIn("birth_path=顺产", context)
        self.assertIn("support_person=伴侣", context)
        self.assertEqual(state.active_service_domain, "birth_prep")

        form_result = create_hospital_bag_form(
            {
                "default_values": {
                    "return_to_work_timing": "产假后返工",
                    "top_worries": ["怕漏带"],
                }
            },
            {
                "user_message": "",
                "_birth_prep_hospital_bag_slots": hospital_bag_slots(state),
            },
        )

        self.assertEqual(form_result["status"], "form_created")
        self.assertEqual(form_result["form"]["default_values"]["due_date_or_week"], "孕30周")
        self.assertEqual(form_result["form"]["default_values"]["birth_path"], "顺产")
        self.assertEqual(form_result["form"]["default_values"]["support_person"], "有人全天帮忙")

    def test_hospital_bag_form_state_records_cleaned_defaults_only(self) -> None:
        state = ContextState()
        tool_result = create_hospital_bag_form(
            {
                "default_values": {
                    "due_date_or_week": "我想先整理待产包",
                    "feeding_intention": "母乳",
                }
            },
            {"user_message": ""},
        )

        _record_birth_prep_tool_state(
            state,
            "hospital_bag_form_create",
            {
                "default_values": {
                    "due_date_or_week": "我想先整理待产包",
                    "feeding_intention": "母乳",
                }
            },
            {
                "ok": True,
                "tool_name": "hospital_bag_form_create",
                "result": tool_result,
            },
        )

        slots = hospital_bag_slots(state)
        self.assertNotIn("due_date_or_week", slots)
        self.assertEqual(slots["feeding_intention"], "亲喂母乳")

    def test_hospital_bag_form_output_constrains_final_reply_to_real_form_fields(self) -> None:
        compact = model_tool_output(
            {
                "ok": True,
                "tool_name": "hospital_bag_form_create",
                "result": create_hospital_bag_form({"default_values": {}}, {"user_message": ""}),
            }
        )

        instruction = compact["final_response_instruction"]
        self.assertIn("按表单里确认的信息整理成一份清单", instruction)
        self.assertIn("不要提医院、家里已有物品", instruction)

    def test_birth_prep_context_extracts_shared_fields_for_hospital_bag_form(self) -> None:
        state = ContextState()

        merge_extracted_birth_prep_slots(
            state,
            [
                {"field_id": "due_date_or_week", "value": "孕30周", "evidence": "孕30周", "confidence": 0.9},
                {"field_id": "first_birth", "value": "是", "evidence": "第一胎", "confidence": 0.9},
                {"field_id": "fetus_count", "value": "单胎", "evidence": "单胎", "confidence": 0.9},
                {"field_id": "birth_path", "value": "剖宫产", "evidence": "倾向剖宫产", "confidence": 0.9},
                {"field_id": "feeding_intention", "value": "亲喂母乳", "evidence": "准备母乳", "confidence": 0.9},
                {"field_id": "support_person", "value": "有人全天帮忙", "evidence": "老公陪我", "confidence": 0.9},
                {"field_id": "pregnancy_history_or_notes", "value": ["没有"], "evidence": "没有特殊情况", "confidence": 0.9},
            ],
            turn_id=1,
        )

        defaults = hospital_bag_slots(state)
        self.assertEqual(defaults["due_date_or_week"], "孕30周")
        self.assertEqual(defaults["first_birth"], "是")
        self.assertEqual(defaults["fetus_count"], "单胎")
        self.assertEqual(defaults["birth_path"], "剖宫产")
        self.assertEqual(defaults["feeding_intention"], "亲喂母乳")
        self.assertEqual(defaults["support_person"], "有人全天帮忙")
        self.assertEqual(defaults["pregnancy_history_or_notes"], ["没有"])

    def test_birth_prep_context_reuses_birth_path_for_birth_plan_form(self) -> None:
        state = ContextState()

        merge_extracted_birth_prep_slots(
            state,
            [
                {"field_id": "due_date_or_week", "value": "孕30周", "evidence": "孕30周", "confidence": 0.9},
                {"field_id": "birth_path", "value": "剖宫产", "evidence": "可能剖宫产", "confidence": 0.9},
                {"field_id": "support_person", "value": "有人全天帮忙", "evidence": "老公陪我", "confidence": 0.9},
            ],
            turn_id=1,
        )

        form_result = create_birth_plan_form(
            {"default_values": {}},
            {
                "user_message": "",
                "_birth_prep_hospital_bag_slots": hospital_bag_slots(state),
            },
        )

        fields = {field["id"]: field for field in form_result["form"]["fields"]}
        self.assertEqual(fields["due_date_or_week"]["default_value"], "孕30周")
        self.assertEqual(fields["birth_path"]["default_value"], "剖宫产")
        self.assertEqual(fields["support_person"]["default_value"], "有人全天帮忙")


if __name__ == "__main__":
    unittest.main()
