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

        consultant = card["consultant"]
        self.assertEqual(consultant["credentials"], "IBCLC 国际认证哺乳顾问")
        self.assertIn("产后哺乳支持经验", consultant["experience"])
        self.assertGreaterEqual(len(consultant["specialties"]), 4)

        self.assertGreaterEqual(len(card["help_topics"]), 4)
        self.assertTrue(all(topic["title"] for topic in card["help_topics"]))
        self.assertGreaterEqual(len(card["prep_items"]), 4)
        self.assertIn("医生", card["boundary_note"])

        self.assertEqual(card["chat"]["label"], "咨询 IBCLC")
        self.assertIn("快速接手", card["chat"]["hint"])


if __name__ == "__main__":
    unittest.main()
