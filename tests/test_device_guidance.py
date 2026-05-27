from __future__ import annotations

import unittest

from momcozy_agent.server import STATIC_CONTENT_TYPES
from momcozy_agent.tool_handlers.device import search_device_manual


class DeviceGuidanceTests(unittest.TestCase):
    def test_air1_unboxing_returns_highlights_and_quick_start_resources(self) -> None:
        result = search_device_manual(
            {"model": "Air1", "query": "我刚收到吸奶器，想开箱", "topic": "unboxing"},
            {"user_message": "我刚收到吸奶器，想开箱"},
        )

        self.assertTrue(str(result["status"]).startswith("manual_loaded"))
        self.assertEqual(result["model"], "Air1")
        self.assertIsInstance(result["manual"], dict)

        highlights = result["product_highlights"]
        self.assertGreaterEqual(len(highlights), 2)
        self.assertTrue(any("无线可穿戴" in item for item in highlights))

        resources = result["quick_start_resources"]
        self.assertEqual([resource["kind"] for resource in resources], ["pdf", "video"])
        self.assertEqual(
            resources[0]["url"],
            "/skill-assets/device-guidance/air1/quick-start/momcozy-air1-quick-start-guidance.pdf",
        )
        self.assertEqual(
            resources[0]["markdown_link"],
            "[Air1 快速上手指南](/skill-assets/device-guidance/air1/quick-start/momcozy-air1-quick-start-guidance.pdf)",
        )
        self.assertEqual(
            resources[1]["url"],
            "/skill-assets/device-guidance/air1/videos/air1-operation-zh.mp4",
        )
        self.assertEqual(
            resources[1]["markdown_link"],
            "[Air1 中文操作视频](/skill-assets/device-guidance/air1/videos/air1-operation-zh.mp4)",
        )
        self.assertIn("只使用每个资源的 markdown_link 字段", result["usage_guidance"])
        self.assertIn("禁止直接展示 url 或 /skill-assets/...", result["usage_guidance"])
        self.assertIn("不要直接开始 manual 第一步", result["usage_guidance"])
        self.assertIn("不要从 guide.parts 直接跳到 guide.controls", result["usage_guidance"])

        manual_text = result["manual"]["content"]
        self.assertIn("用户回复“好了 / 搞定 / 完成了”后，仍然停留在 `guide.parts`", manual_text)
        self.assertIn("主机/整机、充电舱、磁吸充电线", manual_text)

    def test_air1_quick_start_resources_are_returned_when_manual_already_loaded(self) -> None:
        result = search_device_manual(
            {"model": "Air1", "query": "继续开箱", "topic": "unboxing"},
            {
                "user_message": "继续开箱",
                "_loaded_references": ["device-guidance/Air1/references/air1/manual.md 已在当前会话中加载过"],
            },
        )

        self.assertTrue(str(result["status"]).startswith("manual_already_loaded"))
        self.assertIsNone(result["manual"])
        self.assertEqual(result["loaded_reference"], "device-guidance/Air1/references/air1/manual.md")
        self.assertEqual([resource["kind"] for resource in result["quick_start_resources"]], ["pdf", "video"])

    def test_static_content_types_include_quick_start_media(self) -> None:
        self.assertEqual(STATIC_CONTENT_TYPES[".pdf"], "application/pdf")
        self.assertEqual(STATIC_CONTENT_TYPES[".mp4"], "video/mp4")

    def test_skill_asset_route_serves_quick_start_media(self) -> None:
        from fastapi.testclient import TestClient

        from momcozy_agent.server import create_app

        client = TestClient(create_app())
        cases = [
            (
                "/skill-assets/device-guidance/air1/quick-start/momcozy-air1-quick-start-guidance.pdf",
                "application/pdf",
            ),
            (
                "/skill-assets/device-guidance/air1/videos/air1-operation-zh.mp4",
                "video/mp4",
            ),
        ]

        for path, expected_content_type in cases:
            with self.subTest(path=path):
                response = client.get(path, headers={"range": "bytes=0-15"})

                self.assertIn(response.status_code, {200, 206})
                self.assertTrue(response.headers["content-type"].startswith(expected_content_type))
                head_response = client.head(path)
                self.assertEqual(head_response.status_code, 200)
                self.assertTrue(head_response.headers["content-type"].startswith(expected_content_type))

    def test_unified_api_serves_quick_start_media(self) -> None:
        from fastapi.testclient import TestClient

        from momcozy_agent.api_app import create_app

        client = TestClient(create_app())
        response = client.get(
            "/skill-assets/device-guidance/air1/quick-start/momcozy-air1-quick-start-guidance.pdf",
            headers={"range": "bytes=0-15"},
        )

        self.assertIn(response.status_code, {200, 206})
        self.assertTrue(response.headers["content-type"].startswith("application/pdf"))
        head_response = client.head(
            "/skill-assets/device-guidance/air1/quick-start/momcozy-air1-quick-start-guidance.pdf"
        )
        self.assertEqual(head_response.status_code, 200)
        self.assertTrue(head_response.headers["content-type"].startswith("application/pdf"))


if __name__ == "__main__":
    unittest.main()
