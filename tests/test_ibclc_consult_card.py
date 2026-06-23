from __future__ import annotations

import unittest

from momcozy_agent.tool_handlers.ibclc import create_ibclc_consult_card


class IbclcConsultCardTests(unittest.TestCase):
    def test_blocks_card_without_explicit_user_confirmation(self) -> None:
        result = create_ibclc_consult_card({}, {"user_message": "", "locale": "zh-CN"})

        self.assertEqual(result["tool_name"], "ibclc_consult_card_create")
        self.assertEqual(result["status"], "ibclc_consult_blocked")
        self.assertEqual(result["reason"], "missing_explicit_ibclc_request")
        self.assertTrue(result["requires_user_confirmation"])
        self.assertIn("IBCLC 在线咨询入口", result["confirmation_question"])
        self.assertNotIn("card", result)

    def test_blocks_short_ok_without_previous_ibclc_offer(self) -> None:
        result = create_ibclc_consult_card({}, {"user_message": "ok", "locale": "zh-CN"})

        self.assertEqual(result["status"], "ibclc_consult_blocked")
        self.assertEqual(result["reason"], "short_confirmation_without_ibclc_offer")
        self.assertNotIn("card", result)

    def test_blocks_negative_ibclc_intent_even_when_keyword_is_present(self) -> None:
        result = create_ibclc_consult_card({}, {"user_message": "先不用找 IBCLC", "locale": "zh-CN"})

        self.assertEqual(result["status"], "ibclc_consult_blocked")
        self.assertEqual(result["reason"], "missing_explicit_ibclc_request")
        self.assertNotIn("card", result)

    def test_blocks_plain_ibclc_mention_without_request_action(self) -> None:
        result = create_ibclc_consult_card({}, {"user_message": "IBCLC 是什么？", "locale": "zh-CN"})

        self.assertEqual(result["status"], "ibclc_consult_blocked")
        self.assertEqual(result["reason"], "missing_explicit_ibclc_request")
        self.assertNotIn("card", result)

    def test_allows_short_ok_after_previous_ibclc_offer(self) -> None:
        result = create_ibclc_consult_card(
            {},
            {
                "user_message": "好的",
                "locale": "zh-CN",
                "previous_assistant_message": "这个情况更适合让 IBCLC 顾问接着看。需要我帮你推荐一位哺乳顾问吗？",
            },
        )

        self.assertEqual(result["status"], "ibclc_consult_card_created")
        self.assertEqual(result["card"]["card_type"], "ibclc_consult_card")

    def test_default_card_includes_professional_context(self) -> None:
        result = create_ibclc_consult_card({}, {"user_message": "我想找 IBCLC", "locale": "zh-CN"})

        self.assertEqual(result["tool_name"], "ibclc_consult_card_create")
        self.assertEqual(result["status"], "ibclc_consult_card_created")

        card = result["card"]
        self.assertEqual(card["card_type"], "ibclc_consult_card")
        self.assertEqual(card["schema_version"], "1.1")
        self.assertEqual(card["title"], "IBCLC 在线咨询")
        self.assertNotIn("subtitle", card)

        consultant = card["consultant"]
        self.assertEqual(consultant["credentials"], "IBCLC 国际认证哺乳顾问")
        self.assertNotIn("国际认证哺乳顾问，", consultant["bio"])
        self.assertEqual(consultant["experience"], "8 年产后哺乳支持经验")
        self.assertIn("拥有 8 年产后哺乳支持经验", consultant["bio"])
        self.assertIn("核心擅长含乳评估", consultant["bio"])
        self.assertIn("有效吸吮与母乳移出观察", consultant["bio"])
        self.assertIn("判断摄入信号", consultant["bio"])
        self.assertIn("个性化调整建议", consultant["bio"])
        self.assertNotIn("specialties", consultant)
        self.assertEqual(
            card["recommendation_reason"],
            "我推荐 Emily Chen，是因为她擅长含乳、排乳、亲喂/吸奶效果和乳房不适；她也恰好和你同城，后面有必要也可以上门服务。",
        )

        self.assertNotIn("help_topics", card)
        self.assertNotIn("prep_items", card)
        self.assertNotIn("boundary_note", card)

        self.assertEqual(card["chat"]["label"], "咨询 IBCLC")
        self.assertEqual(card["chat"]["note"], "启动咨询后，会自动将你的问题同步给顾问")
        self.assertNotIn("hint", card["chat"])

    def test_custom_bio_removes_repeated_certification_prefix(self) -> None:
        result = create_ibclc_consult_card(
            {"consultant_bio": "国际认证泌乳顾问，专注亲喂和堵奶支持。"},
            {"user_message": "打开 IBCLC 咨询入口", "locale": "zh-CN"},
        )

        self.assertEqual(result["card"]["consultant"]["bio"], "专注亲喂和堵奶支持。")

    def test_recommendation_reason_matches_user_issue(self) -> None:
        result = create_ibclc_consult_card(
            {},
            {"user_message": "宝宝尿布变少了，我担心她没吃饱，想找 IBCLC 看看。", "locale": "zh-CN"},
        )

        reason = result["card"]["recommendation_reason"]
        self.assertIn("宝宝摄入判断、尿布/体重信号和喂养安排", reason)
        self.assertIn("正好对应你刚才提到的宝宝摄入不够安心", reason)
        self.assertIn("恰好和你同城", reason)
        self.assertIn("上门服务", reason)

    def test_recommendation_reason_uses_explicit_issue_summary(self) -> None:
        result = create_ibclc_consult_card(
            {
                "issue_summary": "乳头疼，宝宝总是吸不住",
                "recommendation_topic": "含乳评估和亲喂姿势",
            },
            {"user_message": "找哺乳顾问", "locale": "zh-CN"},
        )

        reason = result["card"]["recommendation_reason"]
        self.assertIn("她擅长含乳评估和亲喂姿势", reason)
        self.assertIn("正好对应你刚才提到的乳头疼，宝宝总是吸不住", reason)


if __name__ == "__main__":
    unittest.main()
