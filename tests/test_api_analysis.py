from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ["ENTRY_API_KEY"] = "test-token"
os.environ["MILK_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="momcozy-agent-api-tests-"), "milk_management.db")

from fastapi.testclient import TestClient

from momcozy_agent.api_app import app
from momcozy_agent.services import data_store
from momcozy_agent.services.milk_management.status_advice import evaluate_status_advice_normality, generate_status_advice


class AnalysisCreateApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer test-token"}

    def test_rejects_extra_payload_fields(self) -> None:
        response = self.client.post(
            "/v1/analysis/create",
            json={"user_id": "u1", "type": "daily_summary", "extra": "nope"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"error": -1, "data": {"result": False, "message": "invalid request body"}})

    def test_mom_baby_returns_two_advice_messages_and_updates_profile_advice(self) -> None:
        _seed_user("u1")
        with (
            patch("momcozy_agent.api.routes.evaluate_status_advice_normality", return_value={"result": True}) as normality,
            patch(
                "momcozy_agent.api.routes.generate_status_advice",
                return_value={"lactation_advice": "今天泌乳节奏稳定。", "feeding_advice": "喂养记录整体正常。"},
            ) as advice,
        ):
            response = self.client.post(
                "/v1/analysis/create",
                json={"user_id": "u1", "type": "mom_baby"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["error"], 0)
        self.assertEqual(payload["data"]["result"], True)
        self.assertEqual(payload["data"]["message"], "泌乳建议：今天泌乳节奏稳定。  \r喂养建议：喂养记录整体正常。")
        card = payload["data"]["analysis_card"]
        self.assertEqual(card["kind"], "mom_baby")
        self.assertEqual(card["title"], "每日泌乳/喂养建议")
        self.assertEqual(card["status"], "normal")
        self.assertEqual(card["status_label"], "暂无明显异常")
        self.assertEqual(card["status_tone"], "normal")
        self.assertEqual([section["title"] for section in card["sections"]], ["泌乳建议", "喂养建议"])
        normality.assert_called_once_with(user_id="u1")
        advice.assert_called_once_with(user_id="u1", normality={"result": True})
        profile = _profile("u1")
        self.assertEqual(profile["lactation_advice"], "今天泌乳节奏稳定。")
        self.assertEqual(profile["feeding_advice"], "喂养记录整体正常。")

    def test_mom_baby_card_surfaces_specific_attention_label(self) -> None:
        _seed_user("u1")
        normality_payload = {
            "result": False,
            "lactation_normal": False,
            "feeding_normal": True,
            "reason": "lactation_out_of_range",
            "failed_metrics": [
                {
                    "type": "lactation",
                    "days": [{"date": "2026-05-20", "status": "low"}],
                }
            ],
        }
        with (
            patch("momcozy_agent.api.routes.evaluate_status_advice_normality", return_value=normality_payload),
            patch(
                "momcozy_agent.api.routes.generate_status_advice",
                return_value={"lactation_advice": "近几天奶量略低，先把排乳节奏稳住。", "feeding_advice": "喂养次数暂时可以继续观察。"},
            ),
        ):
            response = self.client.post(
                "/v1/analysis/create",
                json={"user_id": "u1", "type": "mom_baby"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        card = response.json()["data"]["analysis_card"]
        self.assertEqual(card["status"], "attention")
        self.assertEqual(card["status_label"], "奶量低于参考")
        self.assertEqual(card["status_tone"], "attention")

    def test_status_create_does_not_evaluate_normality(self) -> None:
        _seed_user("u1")
        with (
            patch(
                "momcozy_agent.api.routes.generate_status_advice",
                return_value={"lactation_advice": "泌乳建议", "feeding_advice": "喂养建议"},
            ),
            patch("momcozy_agent.api.routes.evaluate_status_advice_normality") as normality,
        ):
            response = self.client.post(
                "/v1/status/create",
                json={"user_id": "u1"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"error": 0})
        normality.assert_not_called()

    def test_status_advice_normality_is_false_below_3_valid_days(self) -> None:
        _seed_user("u1")

        result = evaluate_status_advice_normality(user_id="u1")

        self.assertFalse(result["result"])
        self.assertFalse(result["lactation_normal"])
        self.assertFalse(result["feeding_normal"])
        self.assertEqual(result["reason"], "insufficient_minimum_valid_days")

    def test_status_advice_uses_rule_fallback_when_llm_generation_fails(self) -> None:
        context = {
            "user_profile": {"user_id": "u1", "delivery_date": "2026-04-14"},
            "infant_profile": {"user_id": "u1", "birth_date": "2026-04-14"},
            "window": {"end_at": "2026-05-22 12:00:00"},
            "pumping_records": [
                {
                    "pump_type": 0,
                    "pump_source": 1,
                    "pump_milk_volum": 80,
                    "pump_milk_duration": 15,
                    "pump_start_time": "2026-05-21 09:00:00",
                    "pump_title": "上午吸奶",
                }
            ],
            "feeding_records": [],
        }
        normality = {
            "result": False,
            "reason": "lactation_out_of_range",
            "failed_metrics": [{"type": "lactation", "days": [{"status": "low"}]}],
        }
        with (
            patch("momcozy_agent.services.milk_management.status_advice.data_store.get_status_advice_context", return_value=context),
            patch("momcozy_agent.services.milk_management.status_advice.estimate_breastfeeding_milk", return_value=0),
            patch("momcozy_agent.services.milk_management.status_advice._request_llm_status_advice", return_value=None),
        ):
            advice = generate_status_advice(user_id="u1", normality=normality)

        self.assertIsNotNone(advice)
        self.assertIn("奶量低于参考", advice["lactation_advice"])
        self.assertTrue(advice["feeding_advice"])

    def test_rejects_pumping_type(self) -> None:
        response = self.client.post(
            "/v1/analysis/create",
            json={"user_id": "u1", "type": "pumping"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"error": -1, "data": {"result": False, "message": "unsupported type"}})

    def test_daily_summary_returns_summary_message_and_updates_profile_summary(self) -> None:
        _seed_user("u1")
        message = ["今日喂养 1 次，预估宝宝摄入 80 ml，整体节奏需要关注", "有 1 次间隔超过 4 小时"]
        with patch(
            "momcozy_agent.api.routes.create_daily_summary",
            return_value={
                "ok": True,
                "status": "daily_summary_created",
                "summary": "日结",
                "data": {"message": message},
            },
        ):
            response = self.client.post(
                "/v1/analysis/create",
                json={"user_id": "u1", "type": "daily_summary"},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["error"], 0)
        self.assertEqual(payload["data"]["message"], "  \r".join(message))
        card = payload["data"]["analysis_card"]
        self.assertEqual(card["kind"], "daily_summary")
        self.assertEqual(card["title"], "每日奶量总结")
        self.assertEqual([section["title"] for section in card["sections"]], ["今日概览", "奶量数据"])
        self.assertEqual(_profile("u1")["daily_summary"], "\n".join(message))


def _seed_user(user_id: str) -> None:
    data_store.init_db()
    with data_store._connect() as conn:  # type: ignore[attr-defined]
        conn.execute("DELETE FROM user_profile WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO user_profile(user_id, user_nickname, delivery_date) VALUES (?, 'Test User', '2026-04-14')",
            (user_id,),
        )


def _profile(user_id: str) -> dict[str, object]:
    with data_store._connect() as conn:  # type: ignore[attr-defined]
        row = conn.execute("SELECT * FROM user_profile WHERE user_id = ?", (user_id,)).fetchone()
        return {key: row[key] for key in row.keys()}


if __name__ == "__main__":
    unittest.main()
