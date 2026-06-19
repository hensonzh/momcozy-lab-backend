from __future__ import annotations

import json
import unittest

from momcozy_agent.agents import artifact_events_from_tool_result, model_tool_output, safe_tool_result
from momcozy_agent.tool_handlers.cards import create_birth_plan_form, create_form, create_hospital_bag_card, create_hospital_bag_form


def _hospital_bag_prefill(**overrides: object) -> dict:
    values = {
        "due_date_or_week": "35 周",
        "return_to_work_timing": "6 周后",
        "top_worries": ["怕漏买", "怕母乳不够", "怕产后没人帮"],
    }
    values.update(overrides)
    return values


def _hospital_bag_form_data(**overrides: object) -> dict:
    values = {
        "due_date_or_week": "36 周",
        "first_birth": "是",
        "fetus_count": "单胎",
        "pregnancy_history_or_notes": ["没有"],
        "birth_path": "顺产",
        "feeding_intention": "母乳",
        "return_to_work_timing": "6 周后",
        "support_person": "有人全天帮忙",
        "top_worries": ["怕漏买", "怕母乳不够"],
    }
    values.update(overrides)
    return values


def _hospital_bag_confirmed_inputs(form_data: dict) -> dict:
    return {
        "user_message": (
            "我已确认待产包信息。\n"
            "form_id: hospital_bag_intake\n"
            "confirmed_form_data:\n"
            f"{json.dumps(form_data, ensure_ascii=False)}"
        )
    }


def _create_hospital_bag_card_for_test(form_data: dict) -> dict:
    return create_hospital_bag_card(
        {"confirmed_form_data": {}},
        _hospital_bag_confirmed_inputs(form_data),
    )


class HospitalBagCardTests(unittest.TestCase):
    def test_hospital_bag_form_submit_label_is_submit(self) -> None:
        result = create_hospital_bag_form({"default_values": _hospital_bag_prefill()}, {"user_message": ""})

        self.assertEqual(result["form"]["id"], "hospital_bag_intake")
        self.assertEqual(result["form"]["submit_label"], "提交")

    def test_hospital_bag_form_uses_revised_intake_questions(self) -> None:
        result = create_hospital_bag_form({"default_values": _hospital_bag_prefill()}, {"user_message": ""})

        fields = result["form"]["fields"]
        field_ids = [field["id"] for field in fields]
        self.assertEqual(
            field_ids,
            [
                "due_date_or_week",
                "first_birth",
                "fetus_count",
                "pregnancy_history_or_notes",
                "birth_path",
                "feeding_intention",
                "return_to_work_timing",
                "support_person",
                "top_worries",
            ],
        )
        self.assertNotIn("birth_setting", field_ids)
        self.assertNotIn("hospital_provided_items", field_ids)

        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["pregnancy_history_or_notes"]["options"][-1], "其它")
        self.assertTrue(by_id["pregnancy_history_or_notes"]["allow_other_input"])
        self.assertNotIn("不确定", by_id["pregnancy_history_or_notes"]["options"])
        self.assertNotIn("计划剖宫产", by_id["pregnancy_history_or_notes"]["options"])
        self.assertEqual(by_id["birth_path"]["label"], "生产信息｜分娩方式")
        self.assertEqual(by_id["feeding_intention"]["options"], ["亲喂母乳", "配方奶", "混合喂养", "还不确定"])
        self.assertEqual(by_id["return_to_work_timing"]["type"], "text")
        self.assertEqual(by_id["return_to_work_timing"]["label"], "喂养信息｜产后多久返工")
        self.assertNotIn("budget_preference", by_id)
        self.assertEqual(by_id["top_worries"]["label"], "偏好信息｜最焦虑的事")
        self.assertEqual(by_id["top_worries"]["options"][-1], "其它")
        self.assertTrue(by_id["top_worries"]["allow_other_input"])

    def test_hospital_bag_form_prefills_known_default_values(self) -> None:
        result = create_hospital_bag_form(
            {
                "default_values": {
                    **_hospital_bag_prefill(),
                    "first_birth": "是",
                    "fetus_count": "单胎",
                    "pregnancy_history_or_notes": ["没有"],
                    "birth_path": "顺产",
                    "feeding_intention": "母乳",
                    "support_person": "有人全天帮忙",
                }
            },
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["first_birth"]["default_value"], "是")
        self.assertEqual(by_id["fetus_count"]["default_value"], "单胎")
        self.assertEqual(by_id["pregnancy_history_or_notes"]["default_value"], "没有")
        self.assertEqual(by_id["birth_path"]["default_value"], "顺产")
        self.assertEqual(by_id["feeding_intention"]["default_value"], "亲喂母乳")
        self.assertEqual(by_id["support_person"]["default_value"], "有人全天帮忙")
        self.assertEqual(result["form"]["default_values"]["first_birth"], "是")
        self.assertEqual(result["form"]["default_values"]["birth_path"], "顺产")
        self.assertEqual(result["form"]["default_values"]["feeding_intention"], "亲喂母乳")

    def test_hospital_bag_form_prefills_birth_path_from_delivery_method_alias(self) -> None:
        result = create_hospital_bag_form(
            {
                "default_values": {
                    "delivery_method": "剖腹产",
                    "due_date_or_week": "孕35周",
                }
            },
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["birth_path"]["default_value"], "剖宫产")
        self.assertEqual(result["form"]["default_values"]["birth_path"], "剖宫产")

    def test_birth_plan_form_prefills_birth_path_from_shared_unknown_answer(self) -> None:
        result = create_birth_plan_form(
            {"default_values": {"delivery_method": "还不确定"}},
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["birth_path"]["default_value"], "还没确定")

    def test_hospital_bag_form_uses_session_slots_when_model_omits_defaults(self) -> None:
        result = create_hospital_bag_form(
            {"default_values": {}},
            {
                "user_message": "",
                "_birth_prep_hospital_bag_slots": _hospital_bag_prefill(
                    top_worries=["怕漏带", "怕住院不舒服"],
                ),
            },
        )

        self.assertEqual(result["status"], "form_created")
        self.assertEqual(
            result["form"]["default_values"],
            _hospital_bag_prefill(
                top_worries=["怕漏带", "怕住院不舒服"],
            ),
        )

    def test_generic_hospital_bag_form_preserves_known_field_defaults(self) -> None:
        result = create_form(
            {
                "form_id": "hospital_bag_intake",
                "title": "信息采集",
                "submit_label": "提交",
                "fields": [
                    {
                        "id": "due_date_or_week",
                        "label": "基本信息｜预产期或当前孕周",
                        "type": "text",
                        "required": True,
                        "default_value": "37 周",
                    },
                    {
                        "id": "first_birth",
                        "label": "基本信息｜是否第一胎",
                        "type": "select",
                        "required": True,
                        "default_value": "是",
                        "options": ["是", "否"],
                    },
                    {
                        "id": "feeding_intention",
                        "label": "喂养信息｜喂养意向",
                        "type": "select",
                        "required": True,
                        "default_value": "母乳",
                        "options": ["亲喂母乳", "配方奶", "混合喂养", "还不确定"],
                    },
                ],
            },
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["due_date_or_week"]["default_value"], "37 周")
        self.assertEqual(by_id["first_birth"]["default_value"], "是")
        self.assertEqual(by_id["feeding_intention"]["default_value"], "亲喂母乳")

    def test_generic_form_with_hospital_bag_fields_is_normalized_to_hospital_bag_intake(self) -> None:
        result = create_form(
            {
                "form_id": "form",
                "title": "信息采集",
                "submit_label": "提交",
                "fields": [
                    {
                        "id": "due_date_or_week",
                        "label": "预产期或当前孕周",
                        "type": "text",
                        "required": True,
                        "default_value": "30周",
                    },
                    {
                        "id": "first_birth",
                        "label": "是否第一胎",
                        "type": "select",
                        "required": True,
                        "default_value": "是",
                        "options": ["是", "否"],
                    },
                    {
                        "id": "fetus_count",
                        "label": "这次是单胎、双胎，还是三胎及以上？",
                        "type": "select",
                        "required": True,
                        "default_value": "单胎",
                        "options": ["单胎", "双胎", "三胎及以上"],
                    },
                ],
            },
            {"user_message": ""},
        )

        self.assertEqual(result["form"]["id"], "hospital_bag_intake")
        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["due_date_or_week"]["default_value"], "30周")
        self.assertEqual(by_id["first_birth"]["default_value"], "是")
        self.assertEqual(by_id["fetus_count"]["default_value"], "单胎")
        self.assertNotIn("budget_preference", by_id)
        self.assertEqual(
            result["form"]["default_values"],
            {"due_date_or_week": "30周", "first_birth": "是", "fetus_count": "单胎"},
        )

    def test_hospital_bag_form_does_not_default_uncollected_fields(self) -> None:
        result = create_hospital_bag_form(
            {"default_values": _hospital_bag_prefill(due_date_or_week="35 周")},
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["due_date_or_week"]["default_value"], "35 周")
        self.assertNotIn("default_value", by_id["first_birth"])
        self.assertNotIn("default_value", by_id["birth_path"])
        self.assertNotIn("default_value", by_id["feeding_intention"])

    def test_hospital_bag_form_can_render_without_dialogue_prefill(self) -> None:
        result = create_hospital_bag_form(
            {"default_values": {"due_date_or_week": "35 周"}},
            {"user_message": ""},
        )

        self.assertEqual(result["status"], "form_created")
        self.assertEqual(result["form"]["id"], "hospital_bag_intake")
        self.assertEqual(result["form"]["default_values"], {"due_date_or_week": "35 周"})

    def test_hospital_bag_form_can_render_with_empty_defaults(self) -> None:
        result = create_hospital_bag_form({"default_values": {}}, {"user_message": ""})

        self.assertEqual(result["status"], "form_created")
        self.assertEqual(result["form"]["id"], "hospital_bag_intake")
        self.assertEqual(result["form"]["default_values"], {})

    def test_hospital_bag_form_rejects_intent_text_as_due_default(self) -> None:
        result = create_hospital_bag_form(
            {"default_values": {"due_date_or_week": "我想先整理待产包"}},
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(result["form"]["default_values"], {})
        self.assertNotIn("default_value", by_id["due_date_or_week"])

    def test_hospital_bag_form_prefills_pre_dialogue_fields_when_collected(self) -> None:
        result = create_hospital_bag_form(
            {
                "default_values": (
                    '{"due_date_or_week":"35 周","return_to_work_timing":"6 周后",'
                    '"budget_preference":"中预算","top_worries":["怕漏买","怕母乳不够","怕产后没人帮"],'
                    '"feeding_intention":"母乳"}'
                )
            },
            {"user_message": ""},
        )

        field_ids = [field["id"] for field in result["form"]["fields"]]
        self.assertIn("due_date_or_week", field_ids)
        self.assertIn("return_to_work_timing", field_ids)
        self.assertNotIn("budget_preference", field_ids)
        self.assertIn("top_worries", field_ids)
        self.assertIn("first_birth", field_ids)
        self.assertIn("feeding_intention", field_ids)
        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["due_date_or_week"]["default_value"], "35 周")
        self.assertEqual(by_id["return_to_work_timing"]["default_value"], "6 周后")
        self.assertEqual(by_id["top_worries"]["default_value"], "怕漏买, 怕母乳不够, 怕产后没人帮")
        self.assertEqual(result["form"]["default_values"]["due_date_or_week"], "35 周")
        self.assertNotIn("budget_preference", result["form"]["default_values"])
        self.assertEqual(result["form"]["default_values"]["feeding_intention"], "亲喂母乳")
        self.assertEqual(by_id["feeding_intention"]["default_value"], "亲喂母乳")
        self.assertEqual(result["form"]["default_values"]["top_worries"], ["怕漏买", "怕母乳不够", "怕产后没人帮"])

    def test_hospital_bag_card_requires_frontend_confirmed_form_submission(self) -> None:
        result = create_hospital_bag_card(
            {"confirmed_form_data": _hospital_bag_form_data()},
            {"user_message": ""},
        )

        self.assertEqual(result["status"], "needs_confirmed_form_data")
        self.assertNotIn("card", result)

    def test_hospital_bag_card_guard_does_not_emit_artifact(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "hospital_bag_card_create",
            "result": create_hospital_bag_card(
                {"confirmed_form_data": _hospital_bag_form_data()},
                {"user_message": ""},
            ),
        }

        safe = safe_tool_result(raw)
        events = artifact_events_from_tool_result(
            tool_call_id="call_hospital_bag",
            tool_call_name="hospital_bag_card_create",
            safe_result=safe,
        )
        compact = model_tool_output(raw)

        self.assertEqual(events, [])
        self.assertNotIn("card", safe)
        self.assertIn("工具没有生成表单或结构化内容", compact["final_response_instruction"])

    def test_hospital_bag_card_requires_all_required_form_fields(self) -> None:
        form_data = _hospital_bag_form_data()
        form_data.pop("birth_path")

        result = create_hospital_bag_card(
            {"confirmed_form_data": {}},
            _hospital_bag_confirmed_inputs(form_data),
        )

        self.assertEqual(result["status"], "needs_required_form_fields")
        self.assertEqual(result["missing_fields"], ["birth_path"])
        self.assertNotIn("card", result)

    def test_hospital_bag_card_model_instruction_requires_cart_link_and_specific_examples(self) -> None:
        result = _create_hospital_bag_card_for_test(
            _hospital_bag_form_data(
                fetus_count="双胎",
                birth_path="剖宫产",
                feeding_intention="混合喂养",
                return_to_work_timing="6 周后",
            )
        )

        compact = model_tool_output({"ok": True, "tool_name": "hospital_bag_card_create", "result": result})
        instruction = compact["final_response_instruction"]

        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("建议内容", instruction)
        self.assertNotIn("工具素材", instruction)
        self.assertNotIn("生成依据", instruction)
        self.assertIn("最终回复的内容结构", instruction)
        self.assertIn("正文要提炼其中 2-4 条", instruction)
        self.assertIn("回复示例", instruction)
        self.assertIn("最后一行必须使用回复示例里的 Markdown 购物车链接", instruction)
        self.assertIn("**[打开待产包购物车](/hospital-bag-cart)**", instruction)
        self.assertIn("下方内容只供提炼最终回复", instruction)
        self.assertIn("剖宫产时可以说准备了高腰宽松内裤/不压腹出院裤", instruction)
        self.assertIn("混合喂养时可以说保留哺乳文胸/哺乳背心、防溢乳垫", instruction)
        self.assertIn("双胎时可以说宝宝出院衣物和包被数量按双胎调整", instruction)
        self.assertIn("产后返工时可以说加入冷藏包/冰袋和吸奶配件清洁包", instruction)
        self.assertIn("考虑到你倾向剖宫产", instruction)
        self.assertIn("哺乳文胸/哺乳背心、防溢乳垫、便携式吸奶器和储奶袋/储奶瓶", instruction)
        self.assertEqual(instruction.count("/hospital-bag-cart"), 1)

    def test_generated_card_adds_uncommon_item_explanations(self) -> None:
        result = _create_hospital_bag_card_for_test(
            _hospital_bag_form_data(
                due_date_or_week="32 周",
                birth_path="剖宫产",
                feeding_intention="母乳",
                support_person="有，且需要准备物品",
            )
        )

        groups = result["card"]["card_json"]["packing_groups"]
        items = [item for group in groups for item in group["items"]]
        labels = [item["label"] for item in items]
        breast_pad = next(item for item in items if item["label"] == "防溢乳垫")
        fetal_monitor_band = next(item for item in items if item["label"] == "胎监带")
        belly_band = next(item for item in items if item["label"] == "收腹带")
        identity_document = next(item for item in items if item["label"] == "身份证件")
        birth_communication_card = next(item for item in items if item["label"] == "分娩沟通单")

        self.assertNotIn("润唇膏", labels)
        self.assertEqual(breast_pad["explain"], "放在内衣里吸收漏奶，避免衣服被打湿。")
        self.assertEqual(fetal_monitor_band["explain"], "做胎心监护时固定探头用，有些医院要求自带。")
        self.assertEqual(belly_band["explain"], "产后腹部支撑用品，剖宫产尤其要先问医生。")
        self.assertNotIn("note", identity_document)
        self.assertNotIn("explain", identity_document)
        self.assertEqual(
            birth_communication_card["explain"],
            "记录生产偏好和需要提前沟通的事，入院时方便给医护看。",
        )

    def test_generated_card_marks_field_driven_items(self) -> None:
        result = _create_hospital_bag_card_for_test(
            _hospital_bag_form_data(
                first_birth="否",
                fetus_count="双胎",
                pregnancy_history_or_notes=["宝宝可能 NICU"],
                birth_path="剖宫产",
                feeding_intention="混合喂养",
                return_to_work_timing="6 周后",
                support_person="支持少",
                top_worries=["怕母乳不够", "怕宝宝用品准备不全"],
            )
        )

        groups = result["card"]["card_json"]["packing_groups"]
        items = [item for group in groups for item in group["items"]]
        high_waist = next(item for item in items if item["label"] == "高腰宽松内裤")
        baby_clothes = next(item for item in items if item["label"] == "宝宝出院衣物")
        pump = next(item for item in items if item["label"] == "便携式吸奶器")
        remote_contacts = next(item for item in items if item["label"] == "远程联系人名单")

        self.assertTrue(_has_source(high_waist, "birth_path", "剖宫产"))
        self.assertTrue(_has_source(baby_clothes, "fetus_count", "双胎"))
        self.assertTrue(_has_source(pump, "feeding_intention", "混合"))
        self.assertTrue(_has_source(pump, "return_to_work_timing", "6 周后"))
        self.assertNotIn("personalized_by", remote_contacts)

        followup = result["assistant_followup"]["message"]
        self.assertIn("待产包清单我整理好了", followup)
        self.assertIn("特殊物品我按这几个情况做了取舍：", followup)
        self.assertIn("\n- 考虑到你倾向剖宫产", followup)
        self.assertIn("考虑到你倾向剖宫产", followup)
        self.assertIn("我为你准备了高腰宽松内裤和不压腹出院裤/裙", followup)
        self.assertIn("收腹带先放在需要问医生的项目里", followup)
        self.assertIn("\n- 考虑到你准备混合喂养", followup)
        self.assertIn("考虑到你准备混合喂养", followup)
        self.assertIn("哺乳文胸/哺乳背心、防溢乳垫、便携式吸奶器和储奶袋/储奶瓶", followup)
        self.assertIn("\n- 考虑到你预计6 周后返工", followup)
        self.assertIn("考虑到你预计6 周后返工", followup)
        self.assertIn("冷藏包/冰袋", followup)
        self.assertIn("吸奶配件清洁包", followup)
        self.assertIn("具体可以看下面的待产包清单", followup)
        self.assertIn("按实际情况删减后再决定是否购买", followup)
        self.assertNotIn("配方奶", followup)
        self.assertNotIn("家里有什么", followup)
        self.assertNotIn("直接下单", followup)
        self.assertNotIn("一键打包下单", followup)
        self.assertIn("/hospital-bag-cart", followup)

    def test_hospital_bag_followup_only_mentions_existing_special_items(self) -> None:
        result = _create_hospital_bag_card_for_test(
            _hospital_bag_form_data(
                fetus_count="双胎",
                birth_path="顺产",
                feeding_intention="母乳",
                return_to_work_timing="暂不返工",
                top_worries=["怕宝宝用品准备不全"],
            )
        )

        groups = result["card"]["card_json"]["packing_groups"]
        labels = {item["label"] for group in groups for item in group["items"]}
        followup = result["assistant_followup"]["message"]

        self.assertIn("特殊物品我按这几个情况做了取舍：", followup)
        self.assertIn("\n- 考虑到这次是双胎", followup)
        self.assertIn("宝宝出院衣物", followup)
        self.assertIn("包被", followup)
        self.assertIn("具体可以看下面的待产包清单", followup)
        self.assertNotIn("冷藏包/冰袋", followup)
        self.assertNotIn("吸奶配件清洁包", followup)
        self.assertNotIn("配方奶", followup)
        for absent_label in ["冷藏包/冰袋", "吸奶配件清洁包", "配方奶"]:
            self.assertNotIn(absent_label, labels)

    def test_generated_card_suppresses_selected_personalization_reasons(self) -> None:
        result = _create_hospital_bag_card_for_test(
            _hospital_bag_form_data(
                fetus_count="双胎",
                pregnancy_history_or_notes=["妊娠糖尿病", "宝宝可能 NICU"],
                birth_path="剖宫产",
                feeding_intention="混合喂养",
                top_worries=["不知道什么时候去医院", "怕宝宝用品准备不全"],
            )
        )

        groups = result["card"]["card_json"]["packing_groups"]
        items = [item for group in groups for item in group["items"]]
        by_label = {item["label"]: item for item in items}
        for label in [
            "检查报告/化验单",
            "医院预登记信息",
            "紧急联系人信息",
            "医生/医院联系电话",
            "手机充电线和充电器",
            "医院路线和停车信息",
            "夜间入口信息",
        ]:
            self.assertIn(label, by_label)
            self.assertNotIn("personalized_by", by_label[label])

        support_group = next(group for group in groups if group["group_id"] == "support_person_bag")
        self.assertTrue(support_group["items"])
        for item in support_group["items"]:
            self.assertNotIn("personalized_by", item)


def _has_source(item: dict, field: str, condition: str) -> bool:
    return any(
        source.get("field") == field and condition in source.get("condition", "")
        for source in item.get("personalized_by", [])
    )


if __name__ == "__main__":
    unittest.main()
