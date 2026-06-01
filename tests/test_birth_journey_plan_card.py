from __future__ import annotations

import json
import unittest

from momcozy_agent.agents import model_tool_output
from momcozy_agent.tool_handlers.cards import create_birth_journey_plan_card
from momcozy_agent.tool_registry import select_runtime_tools


class BirthJourneyPlanCardTests(unittest.TestCase):
    def test_creates_structured_birth_journey_plan_card_from_week_context(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": {
                    "due_date_or_week": "25周",
                    "first_birth": "是",
                    "birth_path": "剖宫产",
                    "feeding_intention": "母乳",
                    "support_person": "伴侣",
                },
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-05-31T09:00:00+08:00"},
        )

        self.assertEqual(result["tool_name"], "ui_card_create")
        self.assertEqual(result["card"]["card_type"], "birth_journey_plan_card")
        self.assertEqual(result["card"]["schema_version"], "1.0")
        card = result["card"]["card_json"]
        self.assertEqual(card["title"], "生产全过程计划")
        self.assertEqual(card["owner"]["current_week"], "孕25周")
        self.assertEqual(card["owner"]["estimated_due_date"], "2026/09/13")

        phases = card["phases"]
        self.assertGreaterEqual(len(phases), 5)
        self.assertEqual(sum(phase["status"] == "current" for phase in phases), 1)
        self.assertEqual(phases[0]["title"], "确认医院")
        self.assertIn("约 2026/05/31", phases[0]["date_range"])
        self.assertTrue(all(phase.get("goal") and phase.get("watchouts") and phase.get("actions") for phase in phases))

        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn("剖宫产", rendered)
        self.assertIn("母乳", rendered)
        self.assertNotIn("证件", rendered)
        self.assertNotIn("产检资料", rendered)
        self.assertNotIn("| --- |", rendered)
        self.assertNotIn("<br>", rendered)

    def test_exposes_birth_journey_plan_tool(self) -> None:
        tool_names = [tool.get("name") for tool in select_runtime_tools() if isinstance(tool, dict)]

        self.assertIn("birth_journey_plan_card_create", tool_names)

    def test_model_tool_output_compacts_birth_journey_card(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "birth_journey_plan_card_create",
            "result": {
                "status": "card_created",
                "card": {
                    "card_type": "birth_journey_plan_card",
                    "schema_version": "1.0",
                    "card_json": {"title": "生产全过程计划", "phases": [{"title": "阶段"}]},
                },
                "assistant_followup": {"message": "已经根据你的情况整理好了生产全过程计划。"},
            },
        }

        compact = model_tool_output(raw)

        self.assertEqual(compact["status"], "card_created")
        self.assertEqual(compact["card"], {"card_type": "birth_journey_plan_card", "schema_version": "1.0", "created": True})
        self.assertNotIn("phases", json.dumps(compact, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
