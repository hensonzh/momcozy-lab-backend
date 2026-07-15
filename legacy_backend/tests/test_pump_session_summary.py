from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from momcozy_agent.server import _format_client_event
from momcozy_agent.api.routes import _client_event_forward_url
from momcozy_agent.services.pump_session_summary import build_pump_session_summary


class PumpSessionSummaryTests(unittest.TestCase):
    def test_builds_display_summary_and_agent_context(self) -> None:
        result = build_pump_session_summary(
            {
                "user_id": "app-user",
                "conversation_id": "conv-1",
                "end_reason": "user-confirm",
                "started_at": "2026-05-15T14:30:00+08:00",
                "ended_at": "2026-05-15T14:48:00+08:00",
                "process_all": 100,
                "left": {"milk_ml": 52, "process": 100},
                "right": {"milk_ml": 48, "process": 100},
            }
        )

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["session"]["duration_seconds"], 1080)
        self.assertEqual(data["session"]["total_milk_ml"], 100)
        self.assertIn("总奶量约 100 ml", data["chat_message"]["content"])
        self.assertIn("左右比较接近", data["chat_message"]["content"])
        self.assertEqual(data["chat_message"]["cardType"], "report")
        card_data = data["chat_message"]["cardData"]
        self.assertEqual(card_data["kind"], "pump-session-summary")
        self.assertEqual(card_data["event_id"], data["session"]["event_id"])
        analysis_card = card_data["analysisCard"]
        self.assertEqual(analysis_card["title"], "吸奶小结")
        self.assertEqual(analysis_card["status_label"], "完成度高")
        self.assertEqual(analysis_card["sections"][1]["metrics"][0]["value"], "52 ml")
        self.assertEqual(analysis_card["sections"][1]["metrics"][1]["value"], "48 ml")
        self.assertNotIn("content", data)
        self.assertNotIn("summary", data)
        self.assertNotIn("agent_context_event", data)
        self.assertNotIn("agent_context_text", data)
        self.assertIn("pump_session_ended", result["context_text"])
        self.assertIn('"duration_seconds":1080', result["context_text"])
        self.assertNotIn("visible_summary", result["context_text"])

    def test_handles_missing_milk_amount_without_failure(self) -> None:
        result = build_pump_session_summary(
            {
                "user_id": "app-user",
                "end_reason": "device-offline-ended-both",
                "duration_seconds": 720,
                "process_all": 70,
            }
        )

        self.assertTrue(result["ok"])
        self.assertIn("暂未获取到奶量数据", result["data"]["chat_message"]["content"])
        self.assertIn("提前结束", result["data"]["chat_message"]["content"])
        self.assertIsNone(result["data"]["session"]["total_milk_ml"])
        self.assertEqual(result["data"]["chat_message"]["cardData"]["analysisCard"]["status_label"], "提前结束")
        self.assertNotIn("total_milk_ml", result["context_text"])

    def test_websocket_response_hides_internal_agent_context(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from momcozy_agent.api_app import create_app
        except Exception as exc:
            self.skipTest(f"FastAPI test client unavailable: {exc}")

        with patch.dict(
            os.environ,
            {
                "ENTRY_API_KEY": "test-token",
                "MOMCOZY_AGENT_CLIENT_EVENT_URL": "disabled",
            },
        ):
            client = TestClient(create_app())
            with client.websocket_connect("/v1/pump/session-summary?token=test-token") as websocket:
                websocket.send_json(
                    {
                        "user_id": "app-user",
                        "conversation_id": "conv-1",
                        "end_reason": "user-confirm",
                        "duration_seconds": 1080,
                        "ended_at": "2026-05-15T14:48:00+08:00",
                        "process_all": 100,
                        "left": {"milk_ml": 52},
                        "right": {"milk_ml": 48},
                    }
                )
                payload = websocket.receive_json()

        self.assertEqual(payload["status"], 200)
        data = payload["data"]
        self.assertEqual(data["session"]["total_milk_ml"], 100)
        self.assertIn("总奶量约 100 ml", data["chat_message"]["content"])
        self.assertNotIn("agent_context_text", data)
        self.assertNotIn("agent_context_event", data)
        self.assertNotIn("agent_context_event", data["chat_message"]["cardData"])

    def test_websocket_requires_conversation_id(self) -> None:
        try:
            from fastapi.testclient import TestClient

            from momcozy_agent.api_app import create_app
        except Exception as exc:
            self.skipTest(f"FastAPI test client unavailable: {exc}")

        with patch.dict(
            os.environ,
            {
                "ENTRY_API_KEY": "test-token",
                "MOMCOZY_AGENT_CLIENT_EVENT_URL": "disabled",
            },
        ):
            client = TestClient(create_app())
            with client.websocket_connect("/v1/pump/session-summary?token=test-token") as websocket:
                websocket.send_json(
                    {
                        "user_id": "app-user",
                        "end_reason": "user-confirm",
                        "duration_seconds": 1080,
                        "left": {"milk_ml": 52},
                        "right": {"milk_ml": 48},
                    }
                )
                payload = websocket.receive_json()

        self.assertEqual(payload["status"], 400)
        self.assertEqual(payload["message"], "conversation_id is required")
        self.assertEqual(payload["data"]["error"], -1)

    def test_client_event_context_text_is_appended_verbatim(self) -> None:
        event = _format_client_event(
            {
                "thread_id": "conv-1",
                "metadata": {
                    "context_text": 'pump_session_ended {"total_milk_ml":100}',
                },
            }
        )

        self.assertEqual(event, 'pump_session_ended {"total_milk_ml":100}')

    def test_client_event_forward_url_follows_chat_sse_url_when_not_explicit(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MOMCOZY_CHAT_SSE_URL": "http://127.0.0.1:8870/api/ag-ui",
            },
        ):
            os.environ.pop("MOMCOZY_AGENT_CLIENT_EVENT_URL", None)

            self.assertEqual(_client_event_forward_url(), "http://127.0.0.1:8870/api/client-event")

    def test_client_event_forward_url_preserves_explicit_override(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MOMCOZY_AGENT_CLIENT_EVENT_URL": "disabled",
                "MOMCOZY_CHAT_SSE_URL": "http://127.0.0.1:8870/api/ag-ui",
            },
        ):
            self.assertEqual(_client_event_forward_url(), "disabled")


if __name__ == "__main__":
    unittest.main()
