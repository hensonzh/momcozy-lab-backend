from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from momcozy_agent.api.routes import _ag_ui_cancel_forward_url
from momcozy_agent.api_app import create_app


class AgUiCancelApiTests(unittest.TestCase):
    def test_unified_api_cancel_route_can_be_disabled_without_404(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENTRY_API_KEY": "test-token",
                "MOMCOZY_AGENT_CANCEL_URL": "disabled",
            },
        ):
            client = TestClient(create_app())

            response = client.post(
                "/api/ag-ui-cancel",
                headers={"Authorization": "Bearer test-token"},
                json={"threadId": "thread-1", "runId": "run-1"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "disabled")
        self.assertEqual(payload["thread_id"], "thread-1")

    def test_unified_api_cancel_treats_missing_active_run_as_idempotent_success(self) -> None:
        class FakeResponse:
            status_code = 404
            text = '{"status":"not_found"}'

            def json(self) -> dict[str, object]:
                return {"status": "not_found", "thread_id": "thread-1", "cancelled": False}

        class FakeAsyncClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, url: str, json: object) -> FakeResponse:
                return FakeResponse()

        with patch.dict(
            os.environ,
            {
                "ENTRY_API_KEY": "test-token",
                "MOMCOZY_AGENT_CANCEL_URL": "http://127.0.0.1:8870/api/ag-ui-cancel",
            },
        ), patch("httpx.AsyncClient", FakeAsyncClient):
            client = TestClient(create_app())

            response = client.post(
                "/api/ag-ui-cancel",
                headers={"Authorization": "Bearer test-token"},
                json={"threadId": "thread-1", "runId": "run-1"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "not_found")
        self.assertFalse(payload["cancelled"])

    def test_cancel_forward_url_follows_chat_sse_url_when_not_explicit(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MOMCOZY_CHAT_SSE_URL": "http://127.0.0.1:8870/api/ag-ui",
            },
        ):
            os.environ.pop("MOMCOZY_AGENT_CANCEL_URL", None)

            self.assertEqual(_ag_ui_cancel_forward_url(), "http://127.0.0.1:8870/api/ag-ui-cancel")

    def test_cancel_forward_url_preserves_explicit_override(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MOMCOZY_AGENT_CANCEL_URL": "disabled",
                "MOMCOZY_CHAT_SSE_URL": "http://127.0.0.1:8870/api/ag-ui",
            },
        ):
            self.assertEqual(_ag_ui_cancel_forward_url(), "disabled")


if __name__ == "__main__":
    unittest.main()
