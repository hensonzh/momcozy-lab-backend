from __future__ import annotations

import unittest

from momcozy_agent.tool_handlers.ibclc import create_ibclc_consult_card


class IbclcConsultCardTests(unittest.TestCase):
    def test_default_card_includes_professional_context(self) -> None:
        result = create_ibclc_consult_card({}, {"user_message": "", "locale": "zh-CN"})

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
            "我推荐 Emily Chen，是因为这位顾问是 IBCLC 国际认证哺乳顾问，适合帮你一起看含乳、排乳、亲喂/吸奶效果和乳房不适这类问题。",
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
            {"user_message": "", "locale": "zh-CN"},
        )

        self.assertEqual(result["card"]["consultant"]["bio"], "专注亲喂和堵奶支持。")


if __name__ == "__main__":
    unittest.main()
