from __future__ import annotations

import unittest
from pathlib import Path

from momcozy_agent.agents import MAX_TOOL_IMAGE_BYTES, model_tool_output, safe_tool_result
from momcozy_agent.server import STATIC_CONTENT_TYPES
from momcozy_agent.tool_handlers.device import create_support_ticket_draft, search_device_manual


class DeviceGuidanceTests(unittest.TestCase):
    def test_support_ticket_prompt_asks_order_details_only_when_relevant(self) -> None:
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "device-guidance" / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")

        self.assertNotIn("尽量收集型号、订单号、购买渠道、问题摘要", skill_text)
        self.assertIn("不要为了完整而固定追问订单号", skill_text)
        self.assertIn("只有退货/退款、换货、保修、订单/物流等需要定位购买记录的场景，才询问订单号和购买渠道", skill_text)

    def test_support_ticket_progress_query_is_declared_unsupported(self) -> None:
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "device-guidance" / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")

        self.assertIn("目前系统不支持查看工单进度", skill_text)
        self.assertIn("不要编造确认页、短信/邮件、账户售后记录或其他查看路径", skill_text)
        self.assertIn("创建售后工单前必须先和用户确认", skill_text)
        self.assertIn("如果用户只是继续描述问题或说“还是不行”，不要直接创建工单", skill_text)

    def test_support_ticket_service_prioritizes_emotion_and_resolution_before_ticket(self) -> None:
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "device-guidance" / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")

        self.assertIn("### 处理原则", skill_text)
        self.assertIn("先识别用户情绪", skill_text)
        self.assertIn("默认目标是尽量帮助用户当场解决问题", skill_text)
        self.assertIn("只有安全风险、缺件/破损/明显产品缺陷", skill_text)

    def test_support_ticket_tool_asks_confirmation_before_creating_ticket(self) -> None:
        result = create_support_ticket_draft(
            {
                "issue_type": "malfunction",
                "issue_summary": "换了 5V/2A 适配器和线，还是完全没灯",
                "product_model": "Air1",
                "troubleshooting_done": ["更换 5V/2A 适配器", "更换 USB 线"],
                "urgency": "high",
                "user_confirmed": False,
            },
            {"locale": "zh-CN", "user_message": "换了 5V/2A 适配器和线，还是完全没灯"},
        )

        self.assertEqual(result["status"], "needs_support_ticket_confirmation")
        self.assertNotIn("ticket", result)
        message = result["assistant_followup"]["message"]
        self.assertIn("非常抱歉没有解决你的问题", message)
        self.assertIn("需要我现在帮你创建吗", message)

        compact = model_tool_output({"ok": True, "tool_name": "support_ticket_draft_create", "result": result})
        self.assertTrue(compact["requires_confirmation"])
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("assistant_followup.message", compact["final_response_instruction"])
        self.assertIn("建议内容", compact["final_response_instruction"])
        self.assertIn("非常抱歉没有解决你的问题", compact["final_response_instruction"])
        self.assertIn("售后工单还没有创建", compact["final_response_instruction"])
        self.assertNotIn("售后工单信息表已经展示", compact["final_response_instruction"])
        self.assertNotIn("必须说明售后信息已经整理好", compact["final_response_instruction"])
        self.assertIn("自然表达", compact["final_response_instruction"])
        self.assertIn("最多两段", compact["final_response_instruction"])

    def test_support_ticket_tool_returns_warm_followup_for_model_after_confirmation(self) -> None:
        result = create_support_ticket_draft(
            {
                "issue_type": "malfunction",
                "issue_summary": "换了 5V/2A 适配器和线，还是完全没灯",
                "product_model": "Air1",
                "troubleshooting_done": ["更换 5V/2A 适配器", "更换 USB 线"],
                "urgency": "high",
                "user_confirmed": True,
            },
            {"locale": "zh-CN", "user_message": "需要，请帮我创建售后工单"},
        )

        followup = result["assistant_followup"]["message"]
        self.assertIn("确实很让人着急", followup)
        self.assertIn("我已经帮你把售后信息整理好了", followup)
        self.assertNotIn("草稿", followup)
        self.assertNotIn("未提交", followup)

        compact = model_tool_output({"ok": True, "tool_name": "support_ticket_draft_create", "result": result})
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("assistant_followup.message", compact["final_response_instruction"])
        self.assertIn("建议内容", compact["final_response_instruction"])
        self.assertIn("确实很让人着急", compact["final_response_instruction"])
        self.assertIn("不要提“草稿”“未提交”“确认后才提交”", compact["final_response_instruction"])
        self.assertIn("交付信息只能出现一次", compact["final_response_instruction"])

    def test_air1_step_images_stay_within_model_injection_budget(self) -> None:
        image_dir = Path(__file__).resolve().parents[1] / "skills" / "device-guidance" / "assets" / "air1" / "images"
        oversized = [
            f"{path.name}={path.stat().st_size}"
            for path in sorted(image_dir.glob("*.png"))
            if path.stat().st_size > MAX_TOOL_IMAGE_BYTES
        ]

        self.assertEqual(oversized, [])

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
        self.assertIn("markdown_image", result["usage_guidance"])
        self.assertIn("每个新视觉步骤首次展示当前步骤图", result["usage_guidance"])
        self.assertIn("分步指导以 manual 的 guide.* 模块为一轮主步骤", result["usage_guidance"])
        self.assertIn("不要把每个 bullet 都拆成一轮", result["usage_guidance"])

        relevant_images = result["relevant_images"]
        self.assertGreaterEqual(len(relevant_images), 1)
        self.assertEqual(relevant_images[0]["module"], "guide.parts")
        self.assertEqual(relevant_images[0]["alt"], "Air1 核心部件")
        self.assertEqual(
            relevant_images[0]["url"],
            "/skill-assets/device-guidance/air1/images/air1_guide_parts_components.png",
        )
        self.assertEqual(
            relevant_images[0]["markdown_image"],
            "![Air1 核心部件](/skill-assets/device-guidance/air1/images/air1_guide_parts_components.png)",
        )
        self.assertIn("编号11=Quick Start Guide x1", relevant_images[0]["image_text"])
        self.assertEqual(relevant_images[0]["voice_policy"], "announce")
        self.assertEqual(
            relevant_images[0]["spoken_label"],
            "我放了一张当前步骤的对照图，你可以边看图边完成这一步。",
        )

        safe = safe_tool_result({"ok": True, "tool_name": "device_manual_search", "result": result})
        self.assertEqual(safe["media_voice"][0]["media_id"], relevant_images[0]["url"])
        self.assertEqual(safe["media_voice"][0]["voice_policy"], "announce")
        self.assertEqual(safe["media_voice"][0]["spoken_label"], relevant_images[0]["spoken_label"])
        self.assertLessEqual(len(safe["media_voice"]), 2)

        manual_text = result["manual"]["content"]
        self.assertIn("每个新视觉步骤首次展示当前步骤对应图片", manual_text)
        self.assertIn("对话步进粒度以 `guide.*` 模块为一轮主步骤", manual_text)
        self.assertIn("用户回复“好了 / 搞定 / 完成了”后，仍然停留在 `guide.parts`", manual_text)
        self.assertIn("`guide.disassembly` 作为一个拆卸步骤，不拆成多轮", manual_text)
        self.assertIn("让用户全部完成后回复“拆好了”", manual_text)
        self.assertIn("主机/整机、充电舱、磁吸充电线", manual_text)

    def test_air1_controls_image_has_structured_numbered_labels(self) -> None:
        result = search_device_manual(
            {"model": "Air1", "query": "认识一下主机按钮，编号3是什么", "topic": "unboxing"},
            {"user_message": "编号3是什么"},
        )

        controls = next(image for image in result["relevant_images"] if image["module"] == "guide.controls")
        self.assertIn("编号3=Increase Suction Level / 增加吸力键", controls["image_text"])

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

    def test_air1_flange_recommendation_uses_measured_nipple_size(self) -> None:
        result = search_device_manual(
            {"model": "Air1", "query": "法兰尺寸", "topic": "flange", "measured_nipple_mm": 14},
            {
                "user_message": "14mm",
                "_loaded_references": ["device-guidance/Air1/references/air1/manual.md 已在当前会话中加载过"],
            },
        )

        recommendation = result["flange_recommendation"]
        self.assertEqual(recommendation["status"], "recommended")
        self.assertEqual(recommendation["measured_nipple_mm"], 14)
        self.assertEqual(recommendation["matched_range"], "13-15mm")
        self.assertEqual(recommendation["recommended_flange_mm"], 17)
        self.assertEqual(recommendation["recommended_insert_mm"], 17)
        self.assertTrue(recommendation["included_with_air1"])
        self.assertIn("Air1 随机附带 17mm", recommendation["message"])
        self.assertIn("flange_recommendation", result["usage_guidance"])
        self.assertIn("不要再让用户自己对照图片", result["usage_guidance"])

    def test_air1_flange_recommendation_parses_measurement_from_user_message(self) -> None:
        result = search_device_manual(
            {"model": "Air1", "query": "14 毫米", "topic": "flange"},
            {"user_message": "量到 14 毫米"},
        )

        recommendation = result["flange_recommendation"]
        self.assertEqual(recommendation["measured_nipple_mm"], 14)
        self.assertEqual(recommendation["recommended_flange_mm"], 17)

    def test_air1_flange_recommendation_handles_base_flange(self) -> None:
        result = search_device_manual(
            {"model": "Air1", "query": "22mm", "topic": "flange"},
            {"user_message": "22mm"},
        )

        recommendation = result["flange_recommendation"]
        self.assertEqual(recommendation["matched_range"], "20-23mm")
        self.assertEqual(recommendation["recommended_flange_mm"], 24)
        self.assertIsNone(recommendation["recommended_insert_mm"])
        self.assertEqual(recommendation["accessory_type"], "base_flange")
        self.assertTrue(recommendation["included_with_air1"])
        self.assertIn("不需要额外法兰硅胶塞", recommendation["message"])

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
            (
                "/skill-assets/device-guidance/air1/faq-images/image1.png",
                "image/png",
            ),
            (
                "/images/Air_img/image1.png",
                "image/png",
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

    def test_chat_sse_status_routes_replace_removed_demo_root(self) -> None:
        from fastapi.testclient import TestClient

        from momcozy_agent.server import create_app

        client = TestClient(create_app())

        root_response = client.get("/")
        self.assertEqual(root_response.status_code, 200)
        self.assertEqual(root_response.json()["service"], "momcozy-chat-sse")
        self.assertEqual(root_response.json()["web_demo"], "removed")
        self.assertEqual(root_response.json()["endpoints"]["ag_ui"], "/api/ag-ui")

        health_response = client.head("/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertTrue(health_response.headers["content-type"].startswith("application/json"))

        removed_demo_asset = client.get("/app.js")
        self.assertEqual(removed_demo_asset.status_code, 404)

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

    def test_unified_api_status_routes(self) -> None:
        from fastapi.testclient import TestClient

        from momcozy_agent.api_app import create_app

        client = TestClient(create_app())

        root_response = client.get("/")
        self.assertEqual(root_response.status_code, 200)
        self.assertEqual(root_response.json()["service"], "momcozy-api")
        self.assertEqual(root_response.json()["web_demo"], "removed")
        self.assertEqual(root_response.json()["endpoints"]["ag_ui_ws"], "/api/ag-ui-ws")

        health_response = client.head("/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertTrue(health_response.headers["content-type"].startswith("application/json"))

    def test_unified_api_serves_legacy_air_faq_images(self) -> None:
        from fastapi.testclient import TestClient

        from momcozy_agent.api_app import create_app

        client = TestClient(create_app())
        response = client.get("/images/Air_img/image1.png", headers={"range": "bytes=0-15"})

        self.assertIn(response.status_code, {200, 206})
        self.assertTrue(response.headers["content-type"].startswith("image/png"))
        head_response = client.head("/images/Air_img/image1.png")
        self.assertEqual(head_response.status_code, 200)
        self.assertTrue(head_response.headers["content-type"].startswith("image/png"))


if __name__ == "__main__":
    unittest.main()
