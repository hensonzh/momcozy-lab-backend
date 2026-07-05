from __future__ import annotations

import json
import unittest

from momcozy_agent.tool_handlers.cards import _confirmed_form_data, create_form, create_labor_communication_card


def _create_birth_plan_card_for_test(args: dict, inputs: dict) -> dict:
    card_json = args.get("card_json") if isinstance(args.get("card_json"), dict) else {}
    form_data = dict(card_json)
    form_data.pop("medical_notes", None)
    if "baby_after_birth" in form_data and "baby_after_birth_preferences" not in form_data:
        form_data["baby_after_birth_preferences"] = form_data["baby_after_birth"]
    form_data.update(_confirmed_form_data(inputs))
    return create_labor_communication_card(
        {"confirmed_form_data": {}},
        {
            "user_message": (
                "我已确认分娩沟通单信息。\n"
                "form_id: birth_plan_card_intake\n"
                "confirmed_form_data:\n"
                f"{json.dumps(form_data, ensure_ascii=False)}"
            )
        },
    )


class BirthPlanCardTests(unittest.TestCase):
    def test_normalizes_birth_plan_card_for_chinese_mobile_rendering(self) -> None:
        card_json = {
            "title": "Birth Plan Card",
            "subtitle": "Labor room communication priority card",
            "top_priorities": ["拒绝侧切"],
            "baby_after_birth": ["skin-to-skin"],
            "medical_notes": ["模型生成的医疗备注不应进入卡片"],
            "disclaimer": "This card is for communication only. Please follow clinician and hospital guidance.",
        }

        result = _create_birth_plan_card_for_test(
            {
                "card_type": "birth_plan_card",
                "schema_version": "1.0",
                "card_json": card_json,
            },
            {
                "user_message": (
                    "confirmed_form_data:\n"
                    '{"due_date_or_week":"37周","birth_path":"剖腹产","birth_setting":"市妇幼","support_people":"伴侣",'
                    '"medical_notes":"青霉素过敏"}'
                )
            },
        )

        card = result["card"]["card_json"]
        self.assertEqual(card["title"], "分娩沟通单")
        self.assertEqual(card["subtitle"], "产房沟通重点")
        self.assertEqual(card["overview"]["birth_path"], "剖宫产")
        self.assertEqual(card["overview"]["birth_setting"], "市妇幼")
        self.assertTrue(any("37周、剖宫产" in item for item in card["personalized_notes"]))
        self.assertEqual(card["top_priorities"], ["如果需要侧切，请先说明原因并和我沟通。"])
        self.assertIn("如果需要侧切，请先说明原因并和我沟通", card["intervention_preferences"])
        self.assertEqual(card["baby_after_birth"], ["出生后尽早肌肤接触"])
        self.assertEqual(card["medical_notes"], ["青霉素过敏"])
        self.assertEqual(card["disclaimer"], "这份沟通单只用于沟通。请优先遵循医生和医院建议，尤其是因安全原因需要调整计划时。")
        self.assertTrue(any("术后接触宝宝" in item for item in card["questions_for_hospital"]))
        self.assertIn("assistant_followup", result)

    def test_keeps_medical_notes_empty_without_user_confirmed_facts(self) -> None:
        card_json = {
            "medical_notes": ["医生建议立即改用某方案"],
        }

        result = _create_birth_plan_card_for_test(
            {
                "card_type": "birth_plan_card",
                "schema_version": "1.0",
                "card_json": card_json,
            },
            {"user_message": 'confirmed_form_data:\n{"birth_path":"顺产"}'},
        )

        card = result["card"]["card_json"]
        self.assertEqual(card["overview"]["birth_path"], "顺产")
        self.assertEqual(card["medical_notes"], [])
        self.assertTrue(any("疼痛缓解" in item for item in card["questions_for_hospital"]))

    def test_deduplicates_numbered_top_priorities(self) -> None:
        card_json = {
            "top_priorities": ["希望伴侣参与重要决定", "1. 希望伴侣参与重要决定"],
        }

        result = _create_birth_plan_card_for_test(
            {
                "card_type": "birth_plan_card",
                "schema_version": "1.0",
                "card_json": card_json,
            },
            {"user_message": 'confirmed_form_data:\n{"birth_path":"顺产"}'},
        )

        card = result["card"]["card_json"]
        self.assertEqual(card["top_priorities"], ["希望伴侣参与重要决定"])

    def test_birth_plan_form_strips_help_text_and_keeps_group_labels(self) -> None:
        result = create_form(
            {
                "form_id": "birth_plan_card_intake",
                "title": "信息采集",
                "description": "确认几个关键信息后，我会整理成沟通卡。",
                "fields": [
                    {
                        "id": "due_date_or_week",
                        "label": "基本信息｜现在怀孕多久/预产期",
                        "type": "text",
                        "required": True,
                        "help_text": "不应进入前端。",
                    },
                    {
                        "id": "top_priorities",
                        "label": "支持与沟通｜最希望医护知道的事",
                        "type": "multi_select",
                        "required": True,
                        "options": ["希望每一步先解释", "我还没想好，请帮我整理成温和版本", "未确定"],
                        "help_text": "不应进入前端。",
                    },
                ],
            },
            {"user_message": "帮我做分娩沟通单"},
        )

        fields = result["form"]["fields"]
        self.assertEqual(result["form"]["description"], "")
        self.assertEqual(fields[0]["label"], "基本信息｜现在怀孕多久/预产期")
        self.assertEqual(fields[1]["label"], "支持与沟通｜最希望医护知道的事")
        self.assertEqual(fields[1]["options"], ["希望每一步先解释"])
        self.assertTrue(all("help_text" not in field for field in fields))

    def test_birth_plan_uses_priority_notes_and_hospital_question_focus(self) -> None:
        result = _create_birth_plan_card_for_test(
            {
                "card_type": "birth_plan_card",
                "schema_version": "1.0",
                "card_json": {"top_priorities": []},
            },
            {
                "user_message": (
                    "confirmed_form_data:\n"
                    '{"birth_path":"顺产","top_priorities":["希望每一步先解释"],'
                    '"priority_notes":"希望重要决定也问一下我的伴侣",'
                    '"hospital_questions_focus":["无痛或麻醉什么时候可以沟通","产后有没有母乳喂养支持"]}'
                )
            },
        )

        card = result["card"]["card_json"]
        self.assertIn("希望重要决定也问一下我的伴侣", card["top_priorities"])
        self.assertIn("无痛或麻醉什么时候可以沟通", card["questions_for_hospital"])
        self.assertIn("产后有没有母乳喂养支持", card["questions_for_hospital"])

    def test_birth_plan_prefers_unified_support_person_field(self) -> None:
        result = _create_birth_plan_card_for_test(
            {
                "card_type": "birth_plan_card",
                "schema_version": "1.0",
                "card_json": {},
            },
            {"user_message": 'confirmed_form_data:\n{"birth_path":"顺产","support_person":"伴侣"}'},
        )

        card = result["card"]["card_json"]
        self.assertEqual(card["overview"]["support_people"], "伴侣")
        self.assertTrue(any("伴侣" in item for item in card["personalized_notes"]))

    def test_labor_communication_card_create_reads_current_confirmed_form_data_when_args_empty(self) -> None:
        from momcozy_agent.tool_handlers.cards import create_labor_communication_card

        result = create_labor_communication_card(
            {"confirmed_form_data": {}},
            {
                "user_message": (
                    "我已确认信息。\n"
                    "form_id: birth_plan_card_intake\n"
                    'confirmed_form_data:\n{"birth_path":"顺产","support_person":"伴侣"}'
                )
            },
        )

        card = result["card"]["card_json"]
        self.assertEqual(card["overview"]["birth_path"], "顺产")
        self.assertEqual(card["overview"]["support_people"], "伴侣")

    def test_labor_communication_card_rejects_model_supplied_form_data_without_frontend_submission(self) -> None:
        result = create_labor_communication_card(
            {"confirmed_form_data": {"birth_path": "顺产", "support_person": "伴侣"}},
            {"user_message": ""},
        )

        self.assertEqual(result["status"], "needs_confirmed_form_data")
        self.assertNotIn("card", result)

    def test_birth_plan_maps_expanded_preference_fields(self) -> None:
        result = _create_birth_plan_card_for_test(
            {
                "card_type": "birth_plan_card",
                "schema_version": "1.0",
                "card_json": {},
            },
            {
                "user_message": (
                    "confirmed_form_data:\n"
                    '{"birth_path":"顺产","first_birth":"是",'
                    '"labor_preferences":["医生允许时，希望可以走动或换姿势"],'
                    '"intervention_preferences":["如果需要侧切，请先说明原因再和我沟通"],'
                    '"pain_relief_preferences":["想提前了解有哪些减痛/麻醉选择"],'
                    '"pain_relief_notes":"我有点怕疼，希望有人先解释",'
                    '"feeding_intention":"母乳",'
                    '"baby_after_birth_preferences":["如果医院允许，希望晚一点剪脐带","希望宝宝尽量和我在一起"],'
                    '"emergency_authorization":"希望先联系我的伴侣/支持人",'
                    '"hospital_questions_focus":["生产时能不能喝水或吃点东西","紧急情况会怎么沟通和决定"]}'
                )
            },
        )

        card = result["card"]["card_json"]
        self.assertIn("医生允许时，希望可以走动或换姿势", card["labor_preferences"])
        self.assertTrue(any("侧切" in item for item in card["intervention_preferences"]))
        self.assertIn("想提前了解有哪些减痛/麻醉选择", card["pain_relief"])
        self.assertIn("喂养意向：母乳", card["baby_after_birth"])
        self.assertIn("希望先联系我的伴侣/支持人", card["emergency_authorization"])
        self.assertIn("生产时能不能喝水或吃点东西", card["questions_for_hospital"])
        self.assertFalse(any(("分娩沟通" + "卡") in item for item in card["questions_for_hospital"]))
        self.assertTrue(any("第一胎" in item for item in card["personalized_notes"]))

    def test_birth_plan_maps_top_priorities_into_display_groups(self) -> None:
        result = _create_birth_plan_card_for_test(
            {
                "card_type": "birth_plan_card",
                "schema_version": "1.0",
                "card_json": {},
            },
            {
                "user_message": (
                    "confirmed_form_data:\n"
                    '{"birth_path":"顺产","top_priorities":['
                    '"宝宝出生后，想尽早抱一抱/贴一贴",'
                    '"想尽早试着喂母乳",'
                    '"希望伴侣/支持人尽量陪在身边"]}'
                )
            },
        )

        card = result["card"]["card_json"]
        self.assertIn("出生后希望尽早肌肤接触", card["baby_after_birth"])
        self.assertIn("希望尽早尝试母乳", card["baby_after_birth"])
        self.assertIn("重要决定也请同步伴侣/支持人", card["communication"])

    def test_birth_plan_retains_many_selected_options(self) -> None:
        result = _create_birth_plan_card_for_test(
            {
                "card_type": "birth_plan_card",
                "schema_version": "1.0",
                "card_json": {},
            },
            {
                "user_message": (
                    "confirmed_form_data:\n"
                    '{"birth_path":"顺产",'
                    '"communication_preferences":['
                    '"操作前先告诉我为什么",'
                    '"做决定前先问我同不同意",'
                    '"重要决定请同步伴侣/支持人",'
                    '"计划变化时请先说明原因和选择"],'
                    '"labor_preferences":['
                    '"医生允许时，希望可以走动或换姿势",'
                    '"希望用分娩球、热敷或按摩来缓解不适",'
                    '"希望生产时可以小口喝水或吃点东西",'
                    '"希望环境安静一点、灯光柔和一点"],'
                    '"intervention_preferences":['
                    '"如果需要侧切，请先说明原因再和我沟通",'
                    '"如果需要产钳或真空吸引，请先解释原因",'
                    '"如果需要人工破水，请先和我沟通"],'
                    '"baby_after_birth_preferences":['
                    '"如果医院允许，希望晚一点剪脐带",'
                    '"如果安全允许，希望宝宝出生后尽早肌肤接触",'
                    '"如果安全允许，希望尽早尝试母乳",'
                    '"希望宝宝尽量和我在一起"],'
                    '"hospital_questions_focus":['
                    '"陪产和探视规则",'
                    '"拍照或录像规则",'
                    '"生产时能不能喝水或吃点东西",'
                    '"无痛或麻醉什么时候可以沟通"]}'
                )
            },
        )

        card = result["card"]["card_json"]
        self.assertEqual(len(card["communication"]), 4)
        self.assertEqual(len(card["labor_preferences"]), 4)
        self.assertEqual(len(card["intervention_preferences"]), 3)
        self.assertGreaterEqual(len(card["baby_after_birth"]), 4)
        self.assertGreaterEqual(len(card["questions_for_hospital"]), 4)
        self.assertIn("希望环境安静一点、灯光柔和一点", card["labor_preferences"])
        self.assertIn("无痛或麻醉什么时候可以沟通", card["questions_for_hospital"])


if __name__ == "__main__":
    unittest.main()
