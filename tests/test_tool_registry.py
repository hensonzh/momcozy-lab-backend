from __future__ import annotations

import unittest

from momcozy_agent.tool_schemas import FUNCTION_TOOLS
from momcozy_agent.tool_registry import DEFERRED_TOOL_NAMESPACES, select_runtime_tools


class ToolRegistryTests(unittest.TestCase):
    def test_pump_recommendation_has_independent_deferred_namespace(self) -> None:
        self.assertEqual(
            DEFERRED_TOOL_NAMESPACES["pump_recommendation"]["tool_names"],
            ["hospital_bag_pump_recommend"],
        )
        self.assertEqual(
            DEFERRED_TOOL_NAMESPACES["hospital_bag_cart"]["tool_names"],
            ["hospital_bag_cart_update"],
        )

    def test_runtime_tools_expose_pump_recommendation_namespace(self) -> None:
        namespaces = {
            str(tool.get("name")): tool
            for tool in select_runtime_tools()
            if tool.get("type") == "namespace"
        }

        self.assertIn("pump_recommendation", namespaces)
        tool_names = [tool["name"] for tool in namespaces["pump_recommendation"]["tools"]]
        self.assertEqual(tool_names, ["hospital_bag_pump_recommend"])
        self.assertTrue(namespaces["pump_recommendation"]["tools"][0]["defer_loading"])

    def test_runtime_tools_expose_global_quick_replies_tool(self) -> None:
        tools = {
            str(tool.get("name")): tool
            for tool in select_runtime_tools()
            if tool.get("type") == "function"
        }

        self.assertIn("ui_quick_replies_create", tools)
        description = str(tools["ui_quick_replies_create"]["description"])
        self.assertIn("每轮最终回复都应调用一次", description)
        self.assertIn("只提供 3 个短提示", description)
        self.assertIn("不能绕过保存、提交、替换、转接等确认流程", description)

    def test_deferred_namespace_descriptions_include_boundaries(self) -> None:
        expected_tokens = {
            "care_handoffs": ("已经决定转接", "不要用于", "设备售后工单"),
            "device_support": ("已经拥有或正在使用", "不要用于购买前型号选型", "奶量记录"),
            "milk_management": ("用户自身", "不要用于吸奶器型号购买选型", "设备故障排查"),
            "hospital_bag_cart": ("已经进入待产包购物车", "不要用于生成待产包卡片", "独立吸奶器型号选型"),
            "pump_recommendation": ("购买前", "不要用于已购设备故障", "购物车直接修改"),
        }

        for namespace, tokens in expected_tokens.items():
            description = str(DEFERRED_TOOL_NAMESPACES[namespace]["description"])
            for token in tokens:
                self.assertIn(token, description)

    def test_deferred_tool_descriptions_include_cross_namespace_boundaries(self) -> None:
        expected_tokens = {
            "handoff_summary_generate": ("已经决定转接", "不要用于普通回答总结", "设备售后工单"),
            "device_manual_search": ("已购/正在使用", "不要用于购买前型号推荐", "milk_management"),
            "support_ticket_draft_create": ("不会对外提交", "device_manual_search", "不要用于普通操作指导"),
            "milk_snapshot_get": ("不要用它替代 milk_status_query", "milk_assessment_evaluate", "milk_plan_query"),
            "milk_status_query": ("不要用它替代 milk_assessment_evaluate", "milk_records_query"),
            "hospital_bag_cart_update": ("current_hospital_bag_cart", "不要用于首次生成待产包卡片", "独立吸奶器型号选型"),
            "hospital_bag_pump_recommend": ("购买前选型工具", "不要用于已购设备故障", "milk_management"),
        }

        for tool_name, tokens in expected_tokens.items():
            description = str(FUNCTION_TOOLS[tool_name]["description"])
            for token in tokens:
                self.assertIn(token, description)


if __name__ == "__main__":
    unittest.main()
