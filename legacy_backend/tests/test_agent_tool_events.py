from __future__ import annotations

import os
import tempfile
import json
import unittest
import asyncio
import threading
from unittest.mock import patch

from momcozy_agent import ContextState, build_agent_request
from momcozy_agent.contexts import (
    hospital_bag_slots,
    merge_extracted_birth_prep_slots,
    merge_hospital_bag_slots,
    profile_slots,
    record_milk_management_tool_state,
)
from momcozy_agent.tool_schemas import FUNCTION_TOOLS
from momcozy_agent.agents import (
    AgentRunCancelled,
    artifact_created_event,
    _tool_image_input_item_from_metadata,
    _tool_image_metadata,
    _tool_start_label,
    _InternalToolErrorTextSuppressor,
    clean_web_search_citation_markers,
    clean_internal_tool_error_text,
    model_tool_output,
    run_agent_loop,
    status_custom_event,
    thinking_custom_event,
    tool_call_start_event,
    tool_call_args_event,
    tool_call_end_event,
    tool_call_result_event,
    web_search_status_event,
)
from momcozy_agent.server import (
    ChatRuntime,
    _clone_context_state,
    _runtime_inputs_from_ag_ui,
    _schedule_birth_prep_slot_extraction,
    create_app,
    stream_ag_ui_events,
)


def _request_tool_names(tools: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for tool in tools:
        if tool.get("type") == "function" and isinstance(tool.get("name"), str):
            names.append(str(tool["name"]))
        nested = tool.get("tools") if isinstance(tool.get("tools"), list) else []
        for item in nested:
            if isinstance(item, dict) and item.get("type") == "function" and isinstance(item.get("name"), str):
                names.append(str(item["name"]))
    return names


class AgentToolEventTests(unittest.TestCase):
    def test_ag_ui_timing_log_endpoint_records_jsonl(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi test client is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "timing.jsonl")
            with patch.dict(
                os.environ,
                {
                    "MOMCOZY_AG_UI_TIMING_LOG": "1",
                    "MOMCOZY_AG_UI_TIMING_LOG_PATH": log_path,
                },
            ):
                client = TestClient(create_app(runtime=ChatRuntime(object()), include_websocket_bridge=True))
                response = client.post(
                    "/api/ag-ui-timing-log",
                    json={
                        "source": "client",
                        "stage": "client.send_start",
                        "thread_id": "thread-timing",
                        "run_id": "run-timing",
                        "client_timing_id": "ct-test",
                        "user_id": "demo-user",
                        "elapsed_ms": 12.5,
                        "metadata": {"text_len": 8},
                    },
                )

            self.assertEqual(response.status_code, 200)
            with open(log_path, encoding="utf-8") as fh:
                lines = [line for line in fh.read().splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["stage"], "client.send_start")
            self.assertEqual(record["source"], "client")
            self.assertEqual(record["thread_id"], "thread-timing")
            self.assertEqual(record["run_id"], "run-timing")
            self.assertEqual(record["client_timing_id"], "ct-test")
            self.assertEqual(record["metadata"]["text_len"], 8)

    def test_agent_request_defaults_to_concise_reply_style(self) -> None:
        request = build_agent_request({"user_message": "奶量够不够", "locale": "zh-CN"})

        self.assertEqual(request["text"]["verbosity"], "low")
        self.assertIn("默认回复要短", request["instructions"])
        self.assertIn("优先 1-3 句", request["instructions"])
        self.assertIn("已经展示的信息不要再完整复述", request["instructions"])
        self.assertIn("不要先输出用户可见的过渡说明或中间解释", request["instructions"])
        self.assertIn("快捷输入由 runtime 在最终回复后统一生成", request["instructions"])
        self.assertNotIn("使用 `profile_update` 创建", request["instructions"])

    def test_agent_request_omits_service_tier_when_env_is_unset(self) -> None:
        with patch.dict(os.environ, {"MOMCOZY_OPENAI_SERVICE_TIER": ""}, clear=False):
            request = build_agent_request({"user_message": "奶量够不够", "locale": "zh-CN"})

        self.assertNotIn("service_tier", request)

    def test_agent_request_uses_priority_service_tier_from_env(self) -> None:
        with patch.dict(os.environ, {"MOMCOZY_OPENAI_SERVICE_TIER": "priority"}, clear=False):
            request = build_agent_request({"user_message": "奶量够不够", "locale": "zh-CN"})

        self.assertEqual(request["service_tier"], "priority")

    def test_tool_output_model_request_status_is_user_visible(self) -> None:
        event = status_custom_event(
            {
                "type": "agent.status",
                "phase": "requesting_model",
                "message": "Requesting model response with tool outputs.",
                "metadata": {"round": 1},
            }
        )

        self.assertEqual(event["semantic"]["visibility"], "status")
        self.assertEqual(event["semantic"]["label"], "我接着处理下一步")

        initial_event = status_custom_event(
            {
                "type": "agent.status",
                "phase": "requesting_model",
                "message": "Requesting model response.",
                "metadata": {"round": 0},
            }
        )
        self.assertEqual(initial_event["semantic"]["visibility"], "hidden")

    def test_internal_tool_call_errors_are_removed_from_assistant_text(self) -> None:
        text = (
            'שגיאה: "Invalid tool call: anyOf schema"\n'
            '错误: "Invalid tool call: anyOf schema"\n'
            "我已经生成一版温和追奶计划了。\n"
            "如果方向可以，我再帮你同步到日历提醒。"
        )

        cleaned = clean_internal_tool_error_text(text)

        self.assertNotIn("Invalid tool call", cleaned)
        self.assertNotIn("שגיאה", cleaned)
        self.assertIn("我已经生成一版温和追奶计划了。", cleaned)
        self.assertIn("同步到日历提醒", cleaned)

    def test_streaming_internal_tool_call_error_suppressor_handles_split_deltas(self) -> None:
        suppressor = _InternalToolErrorTextSuppressor()
        chunks = [
            "שג",
            'יאה: "Invalid ',
            'tool call: anyOf schema"\n',
            "我已经生成一版温和追奶计划了。",
        ]

        cleaned = "".join(suppressor.feed(chunk) for chunk in chunks) + suppressor.flush()

        self.assertEqual(cleaned, "我已经生成一版温和追奶计划了。")

    def test_thinking_event_is_not_main_status_visible(self) -> None:
        event = thinking_custom_event("started")

        self.assertEqual(event["semantic"]["label"], "我想一下")
        self.assertEqual(event["semantic"]["visibility"], "hidden")

        next_event = thinking_custom_event("running", {"after_output_text": True})
        self.assertEqual(next_event["semantic"]["label"], "我接着处理下一步")
        self.assertEqual(next_event["semantic"]["visibility"], "hidden")

    def test_tool_start_labels_are_specific_for_exposed_tools(self) -> None:
        labels = {name: _tool_start_label(name, {}) for name in FUNCTION_TOOLS}

        self.assertEqual(labels["birth_journey_intake_manage"], "我先整理孕期计划信息～")
        self.assertEqual(labels["handoff_summary_generate"], "我先整理转接摘要～")
        self.assertEqual(labels["run_approved_skill_script"], "我按场景说明处理这一步～")
        self.assertEqual(labels["profile_update"], "我先帮你记一下基础信息～")
        self.assertNotIn("我先处理这一步～", labels.values())

    def test_birth_journey_intake_default_result_label_is_specific(self) -> None:
        event = tool_call_result_event(
            "message-1",
            "call-birth-intake",
            "birth_journey_intake_manage",
            {
                "ok": True,
                "tool_name": "birth_journey_intake_manage",
                "result": {"status": "personalized_followup"},
            },
        )

        self.assertEqual(event["semantic"]["label"], "我整理好这一步信息啦")

    def test_agent_request_can_disable_tools_for_hidden_prewarm(self) -> None:
        request = build_agent_request(
            {"user_message": "隐藏预热", "locale": "zh-CN"},
            {"enable_tools": False, "max_output_tokens": 24},
        )

        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)
        self.assertEqual(request["max_output_tokens"], 24)
        self.assertNotIn("include", request)

    def test_complex_health_question_adds_health_guidance_context_for_web_search(self) -> None:
        request = build_agent_request({"user_message": "乳房红肿还有点发热怎么办", "locale": "zh-CN"})

        web_tools = [tool for tool in request["tools"] if tool.get("type") == "web_search"]
        self.assertEqual(len(web_tools), 1)
        allowed_domains = web_tools[0]["filters"]["allowed_domains"]
        self.assertIn("www.who.int", allowed_domains)
        self.assertIn("www.acog.org", allowed_domains)
        self.assertIn("www.bfmed.org", allowed_domains)
        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "web_search"}],
            },
        )
        self.assertEqual(request["include"], ["web_search_call.action.sources"])
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("health_guidance_context:", request_context)
        self.assertIn("优先使用 web_search 检索", request_context)
        self.assertIn("不要把医生、儿科、药师或 IBCLC 当成默认结论", request_context)
        self.assertIn("只有明显在变严重、持续不缓解", request_context)
        self.assertIn("第一轮先确认几个要紧情况", request_context)
        self.assertIn("先给低风险处理和观察建议", request_context)
        self.assertIn("不能只回复已记录", request_context)
        self.assertIn("第 3 轮左右或关键问题已回答后再分流", request_context)
        self.assertIn("要主动引导 IBCLC 在线咨询", request_context)
        self.assertIn("我这里有很多优秀的 IBCLC 可以帮助到你，你需要我帮你推荐吗", request_context)
        self.assertNotIn("## 健康咨询和 web_search", request["instructions"])

    def test_more_complex_health_question_terms_trigger_web_search(self) -> None:
        messages = (
            "乳汁电导率连续三天偏高正常吗",
            "宝宝尿布变少要紧吗",
            "产后伤口渗液有没有事",
        )

        for message in messages:
            with self.subTest(message=message):
                request = build_agent_request({"user_message": message, "locale": "zh-CN"})
                self.assertEqual(len([tool for tool in request["tools"] if tool.get("type") == "web_search"]), 1)
                self.assertEqual(
                    request["tool_choice"],
                    {
                        "type": "allowed_tools",
                        "mode": "required",
                        "tools": [{"type": "web_search"}],
                    },
                )
                request_context = request["input"][0]["content"][0]["text"]
                self.assertIn("health_guidance_context:", request_context)

    def test_breast_lump_first_turn_asks_triage_without_web_search(self) -> None:
        request = build_agent_request({"user_message": "我有硬块疼痛", "locale": "zh-CN"})

        self.assertEqual(len([tool for tool in request["tools"] if tool.get("type") == "web_search"]), 0)
        self.assertEqual(request["tool_choice"], "auto")
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("health_guidance_context:", request_context)
        self.assertIn("不需要 web_search", request_context)
        self.assertIn("最终回复只做一句承接 + 关键问题", request_context)
        self.assertIn("有没有发烧、寒战", request_context)
        self.assertIn("不要输出冷敷、按摩、排乳、用药、资料引用或 IBCLC 入口推荐", request_context)

    def test_milk_management_breast_fullness_question_allows_health_guidance(self) -> None:
        request = build_agent_request(
            {"user_message": "乳房胀痛怎么办", "locale": "zh-CN"},
            {"loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "web_search"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("loaded_skill_context:", request_context)
        self.assertIn("health_guidance_context:", request_context)

    def test_background_milk_analysis_reminder_followup_uses_milk_intake_not_web_search(self) -> None:
        message = "\n".join(
            [
                "这是后台奶量分析提醒后的自动接续，不是用户新输入的问题。",
                "请基于下面的奶量分析上下文，用自然简短的话继续解释近期奶量偏低的具体情况。",
                "如果用户继续追问奶量分析、追奶、稳奶、减奶或计划制定，必须按新版奶量管理流程核对必要信息，并通过奶量管理工具推进。",
                "不要生成卡片、表单或清单，不要诊断或开药。",
                "",
                "已展示提醒：嗨，我注意到你近期奶量偏低，可以和你聊聊吗？",
                "奶量分析上下文：已生成预置奶量分析；状态：奶量偏低；卡片摘要：数据统计：近7天总量=3600 ml",
            ]
        )
        request = build_agent_request(
            {"user_message": message, "locale": "zh-CN"},
            {"context_state": ContextState(), "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(len([tool for tool in request["tools"] if tool.get("type") == "web_search"]), 1)
        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertNotIn("health_guidance_context:", request_context)

    def test_milk_plan_acceptance_forces_plan_preview_tool(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "analysis_ready",
                "goal": "milk_analysis",
                "plan_type": "increase_milk",
                "checklist": [
                    {"id": field, "status": "collected"}
                    for field in [
                        "records_7d",
                        "infant_wet_diapers",
                        "infant_state_or_satisfaction",
                        "infant_growth_signal",
                        "maternal_red_flags",
                        "maternal_breast_comfort",
                    ]
                ],
                "next_question": "这些关键信息已经齐了。你想现在按这个方向生成一版奶量计划吗？",
                "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
                "assessment_result": {"ok": True, "status": "milk_assessment_ready", "data": {}},
            },
        }

        for message in (
            "先按每天多50ml来做",
            "现在生成计划",
            "现在制定计划",
            "生成奶量计划",
            "帮我生成奶量计划",
            "温和追奶",
            "追奶",
            "稳奶",
            "减奶",
        ):
            with self.subTest(message=message):
                request = build_agent_request(
                    {"user_message": message, "locale": "zh-CN"},
                    {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
                )

                self.assertEqual(
                    request["tool_choice"],
                    {
                        "type": "allowed_tools",
                        "mode": "required",
                        "tools": [{"type": "function", "name": "milk_plan_preview_create"}],
                    },
                )
                request_context = request["input"][0]["content"][0]["text"]
                self.assertIn("milk_intake_turn:", request_context)
                self.assertIn("mode: offer_or_create_plan_preview", request_context)
                self.assertIn("plan_type: increase_milk", request_context)
                self.assertIn("required_next_tool_when_user_accepts: milk_plan_preview_create", request_context)
                self.assertNotIn("milk_workflow_step", request_context)
                top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
                self.assertIn("milk_plan_preview_create", top_level_functions)
                milk_namespace = next(
                    tool for tool in request["tools"] if tool.get("type") == "namespace" and tool.get("name") == "milk_management"
                )
                self.assertNotIn("milk_plan_preview_create", [tool["name"] for tool in milk_namespace["tools"]])

    def test_milk_plan_request_with_incomplete_intake_forces_intake_and_hides_downstream_tools(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "goal": "milk_analysis",
                "current_field": "infant_wet_diapers",
                "next_question": "宝宝近 24 小时尿量或尿布情况大概怎么样？",
                "checklist": [
                    {"id": "records_7d", "status": "collected"},
                    {"id": "infant_wet_diapers", "status": "missing"},
                    {"id": "infant_state_or_satisfaction", "status": "missing"},
                ],
                "analysis_context": {
                    "checklist": [
                        {"id": "records_7d", "status": "collected"},
                        {"id": "infant_wet_diapers", "status": "missing"},
                    ],
                },
            },
        }

        request = build_agent_request(
            {"user_message": "现在生成计划", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )
        tool_names = _request_tool_names(request["tools"])
        self.assertNotIn("milk_analysis_evaluate", tool_names)
        self.assertNotIn("milk_plan_preview_create", tool_names)

    def test_ordinary_hui_answer_does_not_bypass_milk_intake_tool(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "goal": "milk_analysis",
                "current_field": "infant_wet_diapers",
                "next_question": "宝宝近 24 小时尿量或尿布情况大概怎么样？",
                "checklist": [
                    {"id": "records_7d", "status": "collected"},
                    {"id": "infant_wet_diapers", "status": "missing"},
                    {"id": "infant_state_or_satisfaction", "status": "missing"},
                ],
            },
        }

        request = build_agent_request(
            {"user_message": "尿布会有六七片，按计划继续", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("current_slot: infant_wet_diapers", request_context)
        tool_names = _request_tool_names(request["tools"])
        self.assertNotIn("milk_analysis_evaluate", tool_names)
        self.assertNotIn("milk_plan_preview_create", tool_names)

    def test_short_growth_answer_with_question_mark_does_not_pause_intake(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "goal": "milk_analysis",
                "current_field": "infant_growth_signal",
                "next_question": "宝宝最近体重增长看起来还正常吗？",
                "checklist": [
                    {"id": "records_7d", "status": "collected"},
                    {"id": "infant_wet_diapers", "status": "collected"},
                    {"id": "infant_state_or_satisfaction", "status": "collected"},
                    {"id": "infant_growth_signal", "status": "missing"},
                    {"id": "maternal_red_flags", "status": "missing"},
                ],
            },
        }

        request = build_agent_request(
            {"user_message": "看起来正常吧？", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("current_slot: infant_growth_signal", request_context)

    def test_milk_plan_acceptance_without_assessment_forces_evaluate_and_hides_preview_tool(self) -> None:
        complete_checklist = [
            {"id": field, "status": "collected"}
            for field in [
                "records_7d",
                "infant_wet_diapers",
                "infant_state_or_satisfaction",
                "infant_growth_signal",
                "maternal_red_flags",
                "maternal_breast_comfort",
            ]
        ]
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "analysis_ready",
                "goal": "milk_analysis",
                "plan_type": "increase_milk",
                "next_question": "这些关键信息已经齐了。你想现在按这个方向生成一版奶量计划吗？",
                "checklist": complete_checklist,
                "analysis_context": {
                    "records_snapshot": {"status": "collected", "valid_days": 7},
                    "checklist": [
                        {"id": "records_7d", "status": "collected"},
                        {"id": "infant_wet_diapers", "status": "missing"},
                    ],
                },
            },
        }

        request = build_agent_request(
            {"user_message": "好的", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_evaluate"}],
            },
        )
        tool_names = _request_tool_names(request["tools"])
        self.assertIn("milk_analysis_evaluate", tool_names)
        self.assertNotIn("milk_plan_preview_create", tool_names)

    def test_current_milk_plan_question_forces_calendar_query_tool(self) -> None:
        for message in ("我当前的奶量计划是什么", "现在按哪个计划"):
            with self.subTest(message=message):
                request = build_agent_request(
                    {"user_message": message, "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
                    {"context_state": ContextState(), "loaded_skill_ids": ["milk-management"]},
                )

                self.assertEqual(
                    request["tool_choice"],
                    {
                        "type": "allowed_tools",
                        "mode": "required",
                        "tools": [{"type": "function", "name": "milk_calendar_query"}],
                    },
                )
                top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
                self.assertIn("milk_calendar_query", top_level_functions)
                milk_namespace = next(
                    tool for tool in request["tools"] if tool.get("type") == "namespace" and tool.get("name") == "milk_management"
                )
                self.assertNotIn("milk_calendar_query", [tool["name"] for tool in milk_namespace["tools"]])

        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "analysis_ready",
                "assessment_result": {"ok": True, "status": "milk_assessment_ready", "data": {}},
            },
        }
        request = build_agent_request(
            {"user_message": "现在按哪个计划", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )
        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_calendar_query"}],
            },
        )

    def test_specific_calendar_adjustment_intent_forces_reschedule_preview(self) -> None:
        for message in (
            "明天10到12点有会议，帮我调整吸奶提醒",
            "明天10到12点有会议，帮我同步调整吸奶提醒",
            "我接下来每天10~12点都有会议安排",
            "接下来三天每天上午10~12点都有会议，帮我调整吸奶提醒",
            "我明后天上午10~12点都有会议，调整一下我的日程吧",
        ):
            with self.subTest(message=message):
                request = build_agent_request(
                    {"user_message": message, "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
                    {"context_state": ContextState(), "loaded_skill_ids": ["milk-management"]},
                )

                self.assertEqual(
                    request["tool_choice"],
                    {
                        "type": "allowed_tools",
                        "mode": "required",
                        "tools": [{"type": "function", "name": "milk_calendar_reschedule_preview"}],
                    },
                )
                tool_names = _request_tool_names(request["tools"])
                self.assertIn("milk_calendar_reschedule_preview", tool_names)
                top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
                self.assertIn("milk_calendar_reschedule_preview", top_level_functions)
                self.assertNotIn("milk_calendar_query", top_level_functions)

    def test_specific_calendar_adjustment_intent_pauses_milk_intake_required_tool(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "current_field": "maternal_red_flags",
                "checklist": [{"field": "maternal_red_flags", "status": "missing"}],
            },
        }

        request = build_agent_request(
            {"user_message": "明天10到12点有会议，帮我调整吸奶提醒", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_calendar_reschedule_preview"}],
            },
        )
        tool_names = _request_tool_names(request["tools"])
        self.assertIn("milk_calendar_reschedule_preview", tool_names)

    def test_ibclc_consult_request_pauses_milk_intake_required_tool(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "current_field": "infant_state_or_satisfaction",
                "checklist": [{"field": "infant_state_or_satisfaction", "status": "missing"}],
            },
        }

        request = build_agent_request(
            {"user_message": "打开顾问咨询", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")
        tool_names = _request_tool_names(request["tools"])
        self.assertIn("ibclc_consult_card_create", tool_names)
        self.assertIn("milk_analysis_intake_manage", tool_names)

    def test_ibclc_short_confirmation_pauses_milk_plan_required_tool(self) -> None:
        context_state = ContextState()
        context_state.last_assistant_message = "这类吸完还胀、怕堵奶反复，很适合让 IBCLC 看具体排乳和吸奶节奏。要我帮你打开 IBCLC 在线咨询入口吗？"
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "analysis_ready",
                "goal": "milk_analysis",
                "plan_type": "increase_milk",
                "checklist": [
                    {"id": field, "status": "collected"}
                    for field in [
                        "records_7d",
                        "infant_wet_diapers",
                        "infant_state_or_satisfaction",
                        "infant_growth_signal",
                        "maternal_red_flags",
                        "maternal_breast_comfort",
                    ]
                ],
                "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
                "assessment_result": {"ok": True, "status": "milk_assessment_ready", "data": {}},
            },
        }

        request = build_agent_request(
            {"user_message": "好的", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")
        tool_names = _request_tool_names(request["tools"])
        self.assertIn("ibclc_consult_card_create", tool_names)
        self.assertIn("milk_plan_preview_create", tool_names)

    def test_vague_calendar_adjustment_time_does_not_force_reschedule_preview(self) -> None:
        for message in ("明天上午有会议，帮我调整吸奶提醒", "10到12有会议，帮我调整吸奶提醒", "10-12有会议，帮我调整吸奶提醒"):
            with self.subTest(message=message):
                request = build_agent_request(
                    {"user_message": message, "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
                    {"context_state": ContextState(), "loaded_skill_ids": ["milk-management"]},
                )

                self.assertEqual(request["tool_choice"], "auto")

    def test_pending_calendar_adjustment_context_forces_calendar_mutate(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "pending_calendar_adjustment": {
                "operation": "apply_reschedule",
                "target_date": "2026-05-14",
                "proposal": {
                    "action": "reschedule_day_around_busy_windows",
                    "user_id": "app-user",
                    "target_date": "2026-05-14",
                    "updates": [],
                },
                "idempotency_key": "calendar-adjustment-key",
            },
        }

        request = build_agent_request(
            {"user_message": "好的，保存", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_calendar_mutate"}],
            },
        )
        tool_names = _request_tool_names(request["tools"])
        self.assertIn("milk_calendar_mutate", tool_names)
        self.assertIn("milk_plan_mutate", tool_names)
        top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
        self.assertIn("milk_calendar_mutate", top_level_functions)
        milk_namespace = next(
            tool for tool in request["tools"] if tool.get("type") == "namespace" and tool.get("name") == "milk_management"
        )
        self.assertNotIn("milk_calendar_mutate", [tool["name"] for tool in milk_namespace["tools"]])
        self.assertIn("milk_plan_mutate", [tool["name"] for tool in milk_namespace["tools"]])
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("奶量日程调整正在进行中", request_context)
        self.assertIn("如果用户明确确认保存、同步、执行或按这版调整", request_context)
        self.assertIn("调用 milk_calendar_mutate", request_context)
        self.assertIn("用户提出新的时间", request_context)
        self.assertIn("先调用日程调整预览工具重新生成预览", request_context)
        self.assertIn("后端会复用上一轮缓存的预览", request_context)
        self.assertNotIn("如果用户只是想查看或理解调整结果", request_context)
        self.assertNotIn("pending_calendar_adjustment_ready_for_save", request_context)
        self.assertNotIn("current_action_context", request_context)
        self.assertNotIn("cached_action_payload", request_context)
        self.assertNotIn("pending_calendar_adjustment_idempotency_key", request_context)
        self.assertNotIn("pending_calendar_adjustment_target_dates", request_context)
        self.assertNotIn("不要调用 milk_plan_mutate", request_context)

    def test_pending_calendar_adjustment_revision_turn_does_not_force_calendar_mutate(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "pending_calendar_adjustment": {
                "operation": "apply_reschedule",
                "target_date": "2026-05-14",
                "proposal": {
                    "action": "reschedule_day_around_busy_windows",
                    "user_id": "app-user",
                    "target_date": "2026-05-14",
                    "updates": [],
                },
                "idempotency_key": "calendar-adjustment-key",
            },
        }

        request = build_agent_request(
            {"user_message": "再调整一下", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")
        top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
        self.assertIn("milk_calendar_reschedule_preview", top_level_functions)
        self.assertNotIn("milk_calendar_mutate", top_level_functions)
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("先调用日程调整预览工具重新生成预览", request_context)

    def test_pending_calendar_adjustment_cancel_turn_does_not_promote_write_or_preview(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "pending_calendar_adjustment": {
                "operation": "apply_reschedule",
                "target_date": "2026-05-14",
                "proposal": {
                    "action": "reschedule_day_around_busy_windows",
                    "user_id": "app-user",
                    "target_date": "2026-05-14",
                    "updates": [],
                },
                "idempotency_key": "calendar-adjustment-key",
            },
        }

        request = build_agent_request(
            {"user_message": "先不保存", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")
        top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
        self.assertNotIn("milk_calendar_mutate", top_level_functions)
        self.assertNotIn("milk_calendar_reschedule_preview", top_level_functions)
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("不要调用 milk_calendar_mutate", request_context)

    def test_stale_pending_plan_update_does_not_force_or_hide_write_tools(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "pending_milk_plan_update": {
                "operation": "update",
                "plan_id": 12,
                "patch": {"plan_name": "已调整稳奶计划"},
                "idempotency_key": "plan-update-key",
            },
        }

        request = build_agent_request(
            {"user_message": "确认更新", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")
        tool_names = _request_tool_names(request["tools"])
        self.assertIn("milk_plan_mutate", tool_names)
        self.assertIn("milk_calendar_mutate", tool_names)
        request_context = request["input"][0]["content"][0]["text"]
        self.assertNotIn("pending_milk_plan_update_ready_for_save", request_context)
        self.assertNotIn("confirmed=true", request_context)
        self.assertNotIn("不要调用 milk_calendar_mutate", request_context)

    def test_stale_pending_plan_update_revision_keeps_tool_choice_auto(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "pending_milk_plan_update": {
                "operation": "update",
                "plan_id": 12,
                "patch": {"plan_name": "已调整稳奶计划"},
                "idempotency_key": "plan-update-key",
            },
        }

        request = build_agent_request(
            {"user_message": "再调整一下", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")
        tool_names = _request_tool_names(request["tools"])
        self.assertIn("milk_plan_mutate", tool_names)
        self.assertIn("milk_calendar_mutate", tool_names)

    def test_plan_preview_save_context_promotes_plan_mutate_without_hiding_calendar_mutate(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "plan_preview",
                "plan_preview": {
                    "status": "plan_preview_ready",
                    "draft": {"plan_type": "increase_milk", "plan_days": 3},
                    "idempotency_key": "plan-preview-key",
                },
            },
        }

        request = build_agent_request(
            {"user_message": "保存到日历", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        tool_names = _request_tool_names(request["tools"])
        self.assertIn("milk_plan_mutate", tool_names)
        self.assertIn("milk_calendar_reschedule_preview", tool_names)
        self.assertIn("milk_calendar_mutate", tool_names)
        top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
        self.assertIn("milk_calendar_reschedule_preview", top_level_functions)
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("mode: wait_for_plan_save_confirmation", request_context)
        self.assertIn("奶量计划草稿已经准备好", request_context)
        self.assertIn("用户确认保存、同步或按这版执行时调用 milk_plan_mutate", request_context)
        self.assertIn("先调用 milk_calendar_reschedule_preview 生成可同步预览", request_context)
        self.assertNotIn("confirmed=true", request_context)

    def test_saved_milk_plan_context_promotes_calendar_reschedule_preview(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "last_plan_applied": {
                "status": "plan_applied",
                "summary": "已经同步到计划页。",
            },
        }

        request = build_agent_request(
            {"user_message": "明天上午 10 到 12 点有会议", "locale": "zh-CN", "message_sent_at": "2026-05-14 09:00:00"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        tool_names = _request_tool_names(request["tools"])
        self.assertIn("milk_calendar_reschedule_preview", tool_names)
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("奶量计划已经同步到计划页", request_context)
        self.assertIn("不要用纯文本模拟调整结果", request_context)

    def test_milk_plan_context_answer_forces_intake_tool(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "goal": "plan_preview",
                "plan_type": "increase_milk",
                "current_field": "infant_wet_diapers",
                "next_question": "宝宝近 24 小时尿量或尿布情况大概怎么样？",
                "checklist": [
                    {"id": "records_7d", "status": "collected"},
                    {"id": "infant_wet_diapers", "status": "missing"},
                    {"id": "infant_state_or_satisfaction", "status": "missing"},
                ],
            },
        }

        request = build_agent_request(
            {"user_message": "宝宝尿量正常，精神也正常", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("milk_intake_turn:", request_context)
        self.assertIn("mode: collect_slot", request_context)
        self.assertIn("current_slot: infant_wet_diapers", request_context)
        self.assertIn("current_question: 宝宝近 24 小时尿量或尿布情况大概怎么样？", request_context)
        self.assertNotIn("milk_workflow_step", request_context)

    def test_milk_intake_side_question_can_answer_before_resume_confirmation(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "goal": "milk_analysis",
                "current_field": "infant_wet_diapers",
                "next_question": "宝宝近 24 小时尿量或尿布情况大概怎么样？",
                "checklist": [
                    {"id": "records_7d", "status": "collected"},
                    {"id": "infant_wet_diapers", "status": "missing"},
                    {"id": "infant_state_or_satisfaction", "status": "missing"},
                ],
                "progress": {"index": 2, "total": 6, "display": "第 2/6 项"},
                "workflow_control": {"allowed_next_action": "ask_user", "awaiting_user_input": True},
                "field_guidance": {
                    "why_this_field_matters": "尿布/尿量是判断宝宝短期摄入是否足够的重要信号。",
                    "how_to_interpret_answers": "尿布正常会降低短期摄入风险。",
                },
                "joint_reasoning_guidance": ["7 天奶量偏低 + 尿布偏少时要更谨慎。"],
            },
        }

        request = build_agent_request(
            {"user_message": "为什么要问尿布？", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("step: 2/6", request_context)
        self.assertIn("current_slot_why: 尿布/尿量是判断宝宝短期摄入是否足够的重要信号。", request_context)
        self.assertIn("回复结尾必须逐字询问：我们要继续刚才的奶量分析流程吗？", request_context)

    def test_milk_intake_resume_confirmation_forces_intake_tool(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "goal": "milk_analysis",
                "current_field": "infant_wet_diapers",
                "next_question": "宝宝近 24 小时尿量或尿布情况大概怎么样？",
                "checklist": [
                    {"id": "records_7d", "status": "collected"},
                    {"id": "infant_wet_diapers", "status": "missing"},
                ],
            },
        }

        request = build_agent_request(
            {"user_message": "继续刚才的奶量分析", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )

    def test_new_milk_plan_request_without_state_starts_intake_tool(self) -> None:
        request = build_agent_request(
            {"user_message": "帮我制定一个温和追奶计划", "locale": "zh-CN"},
            {"context_state": ContextState(), "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )
        top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
        self.assertIn("milk_analysis_intake_manage", top_level_functions)
        self.assertNotIn("milk_plan_preview_create", top_level_functions)

    def test_milk_fact_read_followup_forces_analysis_intake_tool(self) -> None:
        context_state = ContextState()
        record_milk_management_tool_state(
            context_state,
            "milk_status_query",
            {
                "ok": True,
                "tool_name": "milk_status_query",
                "result": {"ok": True, "status": "milk_status_ready", "data": {}},
            },
        )

        request = build_agent_request(
            {"user_message": "没有漏记", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertNotIn("milk_analysis_fact_read_tool", request_context)
        self.assertNotIn("milk_analysis_fact_read_status", request_context)
        self.assertNotIn("上一轮只读取了奶量事实", request_context)
        self.assertNotIn("不要自行拼接睡眠、压力、经期、生病、吸奶间隔", request_context)

    def test_milk_domain_record_completeness_answer_without_state_forces_intake_tool(self) -> None:
        context_state = ContextState()
        context_state.active_service_domain = "milk_management"

        request = build_agent_request(
            {"user_message": "记录是完整的", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )

    def test_loaded_milk_skill_record_completeness_answer_without_state_forces_intake_tool(self) -> None:
        request = build_agent_request(
            {"user_message": "没有漏记", "locale": "zh-CN"},
            {"context_state": ContextState(), "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )

    def test_hospital_bag_confirmed_form_forces_card_tool(self) -> None:
        form_data = {
            "due_date_or_week": "20周",
            "first_birth": "是",
            "fetus_count": "双胎",
            "pregnancy_history_or_notes": ["妊娠糖尿病"],
            "birth_path": "剖宫产",
            "feeding_intention": "亲喂母乳",
            "return_to_work_timing": "6周后",
            "support_person": "伴侣",
            "top_worries": ["怕漏买"],
        }
        request = build_agent_request(
            {
                "user_message": (
                    "我已确认待产包信息。\n"
                    "form_id: hospital_bag_intake\n"
                    "confirmed_form_data:\n"
                    f"{json.dumps(form_data, ensure_ascii=False)}"
                ),
                "locale": "zh-CN",
            },
            {"context_state": ContextState(), "loaded_skill_ids": ["birth-prep"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "hospital_bag_card_create"}],
            },
        )
        top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
        self.assertIn("hospital_bag_card_create", top_level_functions)

    def test_birth_journey_basic_form_forces_intake_tool(self) -> None:
        form_data = {"current_week": "20周", "fetus_count": "单胎", "age": "31"}
        request = build_agent_request(
            {
                "user_message": (
                    "我已确认孕期计划基础信息。\n"
                    "form_id: birth_journey_basic_info_intake\n"
                    "confirmed_form_data:\n"
                    f"{json.dumps(form_data, ensure_ascii=False)}"
                ),
                "locale": "zh-CN",
            },
            {"context_state": ContextState(), "loaded_skill_ids": ["birth-prep"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "birth_journey_intake_manage"}],
            },
        )
        top_level_functions = [tool["name"] for tool in request["tools"] if tool.get("type") == "function"]
        self.assertIn("birth_journey_intake_manage", top_level_functions)

    def test_forced_milk_intake_does_not_repeat_after_tool_output(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "current_field": "records_7d",
                "next_question": "过去 7 天好像还缺少可计算的奶量记录。",
                "checklist": [{"id": "records_7d", "status": "missing"}],
            },
        }
        client = _FakeClient(
            [
                {
                    "id": "resp-intake-call",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call-intake",
                            "name": "milk_analysis_intake_manage",
                            "arguments": json.dumps(
                                {
                                    "action": "update",
                                    "user_update": "记录是完整的，帮我看看怎么调整",
                                    "as_of_time": None,
                                    "maternal_symptoms": {},
                                    "infant_signals": {},
                                    "plan_type": None,
                                    "target_daily_ml": None,
                                    "delta_ml": None,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ],
                },
                {
                    "id": "resp-final",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "我先确认一下记录同步情况。",
                                }
                            ],
                        }
                    ],
                },
            ]
        )
        intake_result = {
            "ok": True,
            "status": "milk_analysis_intake_collecting",
            "summary": "奶量分析信息采集中。",
            "data": {
                "intake_state": {
                    "stage": "intake_collecting",
                    "current_field": "infant_wet_diapers",
                    "next_question": "宝宝近 24 小时尿量或尿布情况大概怎么样？",
                    "checklist": [
                        {"id": "records_7d", "status": "collected"},
                        {"id": "infant_wet_diapers", "status": "missing"},
                    ],
                },
                "checklist": [
                    {"id": "records_7d", "status": "collected"},
                    {"id": "infant_wet_diapers", "status": "missing"},
                ],
                "missing_fields": ["infant_wet_diapers"],
                "current_field": "infant_wet_diapers",
                "next_question": "宝宝近 24 小时尿量或尿布情况大概怎么样？",
                "remaining_count": 1,
                "workflow_control": {"allowed_next_action": "ask_user", "awaiting_user_input": True},
                "quick_replies": [
                    {"text": "尿布挺多的"},
                    {"text": "尿布有点少"},
                    {"text": "不太确定"},
                ],
                "executed_step": "intake",
                "next_tool": "milk_analysis_intake_manage",
            },
            "assistant_followup": {"message": "宝宝近 24 小时尿量或尿布情况大概怎么样？"},
        }

        def execute_tool(name: str, arguments: dict[str, object], inputs: dict[str, object]) -> dict[str, object]:
            return {"ok": True, "tool_name": "milk_analysis_intake_manage", "result": intake_result}

        with patch(
            "momcozy_agent.agents._execute_project_tool",
            side_effect=execute_tool,
        ):
            run_agent_loop(
                client,
                {
                    "user_message": "记录是完整的，帮我看看怎么调整",
                    "user_profile": {"user_id": "force-intake-loop-guard"},
                    "locale": "zh-CN",
                    "timezone": "Asia/Shanghai",
                    "message_sent_at": "2026-05-14 12:10:00",
                },
                {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
            )

        first_request, second_request = client.responses.requests[:2]
        self.assertEqual(
            first_request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_intake_manage"}],
            },
        )
        self.assertEqual(second_request["input"][0]["type"], "function_call_output")
        self.assertNotIn("tools", second_request)
        self.assertNotIn("tool_choice", second_request)

    def test_milk_intake_ready_forces_analysis_after_tool_output(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "intake_collecting",
                "current_field": "maternal_breast_comfort",
                "next_question": "吸奶或亲喂后乳房是比较舒服，还是还会胀、排不空或疼？",
                "checklist": [{"id": "maternal_breast_comfort", "status": "missing"}],
            },
        }
        intake_result = {
            "ok": True,
            "status": "milk_analysis_ready_to_evaluate",
            "summary": "奶量分析信息采集已完成。",
            "data": {
                "intake_state": {
                    "stage": "ready_to_evaluate",
                    "checklist": [{"id": "records_7d", "status": "collected"}],
                    "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
                },
                "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
                "checklist": [{"id": "records_7d", "status": "collected"}],
                "missing_fields": [],
                "executed_step": "intake",
                "next_tool": "milk_analysis_evaluate",
            },
        }
        client = _FakeClient(
            [
                {
                    "id": "resp-intake-call",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call-intake-ready",
                            "name": "milk_analysis_intake_manage",
                            "arguments": json.dumps({"user_update": "吸完比较舒服"}),
                        }
                    ],
                },
                {"id": "resp-final", "output": [{"type": "message", "content": [{"type": "output_text", "text": "我继续分析。"}]}]},
            ]
        )

        with patch(
            "momcozy_agent.agents._execute_project_tool",
            return_value={"ok": True, "tool_name": "milk_analysis_intake_manage", "result": intake_result},
        ):
            run_agent_loop(
                client,
                {
                    "user_message": "吸完比较舒服",
                    "user_profile": {"user_id": "intake-ready-force-analysis"},
                    "locale": "zh-CN",
                    "timezone": "Asia/Shanghai",
                    "message_sent_at": "2026-05-14 12:10:00",
                },
                {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
            )

        self.assertEqual(
            client.responses.requests[1]["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_analysis_evaluate"}],
            },
        )

    def test_milk_analysis_evaluate_forces_plan_preview_when_user_accepts_plan(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "ready_to_evaluate",
                "plan_type": "increase_milk",
                "checklist": [{"id": "records_7d", "status": "collected"}],
                "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
            },
        }
        flow_decision = {
            "stage": "plan_ready_to_preview",
            "missing_user_inputs": [],
            "plan_decision": {
                "can_start_plan": True,
                "recommended_plan_type": "increase_milk",
                "next_tool": "milk_plan_preview_create",
            },
        }
        analysis_result = {
            "ok": True,
            "status": "milk_analysis_ready",
            "summary": "奶量分析已完成。",
            "data": {
                "intake_state": {
                    "stage": "analysis_ready",
                    "plan_type": "increase_milk",
                    "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
                },
                "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
                "assessment_result": {
                    "ok": True,
                    "status": "milk_assessment_ready",
                    "data": {"milk_flow_decision": flow_decision},
                },
                "milk_flow_decision": flow_decision,
                "executed_step": "assessment",
                "next_tool": "milk_plan_preview_create",
            },
        }
        client = _FakeClient(
            [
                {
                    "id": "resp-analysis-call",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call-analysis",
                            "name": "milk_analysis_evaluate",
                            "arguments": json.dumps({}),
                        }
                    ],
                },
                {"id": "resp-final", "output": [{"type": "message", "content": [{"type": "output_text", "text": "我开始生成计划。"}]}]},
            ]
        )

        with patch(
            "momcozy_agent.agents._execute_project_tool",
            return_value={"ok": True, "tool_name": "milk_analysis_evaluate", "result": analysis_result},
        ):
            run_agent_loop(
                client,
                {
                    "user_message": "可以，帮我生成温和追奶计划",
                    "user_profile": {"user_id": "analysis-force-preview"},
                    "locale": "zh-CN",
                    "timezone": "Asia/Shanghai",
                    "message_sent_at": "2026-05-14 12:10:00",
                },
                {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
            )

        self.assertEqual(
            client.responses.requests[1]["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_plan_preview_create"}],
            },
        )
        top_level_functions = [tool["name"] for tool in client.responses.requests[1]["tools"] if tool.get("type") == "function"]
        self.assertIn("milk_plan_preview_create", top_level_functions)

    def test_milk_analysis_ready_simple_acceptance_does_not_return_to_intake(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "analysis_ready",
                "goal": "milk_analysis",
                "plan_type": "increase_milk",
                "next_question": "这些关键信息已经齐了。你想现在按这个方向生成一版奶量计划吗？",
                "checklist": [
                    {"id": field, "status": "collected"}
                    for field in [
                        "records_7d",
                        "infant_wet_diapers",
                        "infant_state_or_satisfaction",
                        "infant_growth_signal",
                        "maternal_red_flags",
                        "maternal_breast_comfort",
                    ]
                ],
                "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
                "assessment_result": {"ok": True, "status": "milk_assessment_ready", "data": {}},
            },
        }

        request = build_agent_request(
            {"user_message": "好的", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "milk_plan_preview_create"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertNotIn("任何用户追问都必须来自 milk_analysis_intake_manage", request_context)
        self.assertNotIn("milk_analysis_intake_next_question", request_context)

    def test_milk_analysis_evaluate_does_not_force_plan_preview_for_analysis_only_request(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "ready_to_evaluate",
                "checklist": [{"id": "records_7d", "status": "collected"}],
                "analysis_context": {"records_snapshot": {"status": "collected", "valid_days": 7}},
            },
        }
        analysis_result = {
            "ok": True,
            "status": "milk_analysis_ready",
            "summary": "奶量分析已完成。",
            "data": {
                "intake_state": {"stage": "analysis_ready"},
                "assessment_result": {"ok": True, "status": "milk_assessment_ready", "data": {}},
                "executed_step": "assessment",
                "next_tool": "milk_plan_preview_create",
            },
        }
        client = _FakeClient(
            [
                {
                    "id": "resp-analysis-call",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call-analysis-only",
                            "name": "milk_analysis_evaluate",
                            "arguments": json.dumps({}),
                        }
                    ],
                },
                {"id": "resp-final", "output": [{"type": "message", "content": [{"type": "output_text", "text": "奶量分析完成。"}]}]},
            ]
        )

        with patch(
            "momcozy_agent.agents._execute_project_tool",
            return_value={"ok": True, "tool_name": "milk_analysis_evaluate", "result": analysis_result},
        ):
            run_agent_loop(
                client,
                {
                    "user_message": "帮我分析最近奶量",
                    "user_profile": {"user_id": "analysis-only-no-preview"},
                    "locale": "zh-CN",
                    "timezone": "Asia/Shanghai",
                    "message_sent_at": "2026-05-14 12:10:00",
                },
                {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
            )

        self.assertEqual(client.responses.requests[1]["tool_choice"], "auto")
        self.assertEqual(client.responses.requests[1]["input"][0]["type"], "function_call_output")

    def test_failed_milk_plan_save_result_label_does_not_claim_saved(self) -> None:
        event = tool_call_result_event(
            "message-1",
            "call-save",
            "milk_plan_mutate",
            {
                "ok": True,
                "tool_name": "milk_plan_mutate",
                "result": {
                    "ok": False,
                    "status": "milk_plan_invalid",
                    "summary": "追奶计划修改后吸奶任务次数必须大于当前吸奶次数。",
                    "data": {"validation": {"valid": False}},
                },
            },
        )

        self.assertEqual(event["semantic"]["label"], "这次还没保存成功")
        self.assertNotIn("保存好奶量计划", event["semantic"]["label"])

    def test_milk_plan_decline_keeps_tool_choice_auto(self) -> None:
        context_state = ContextState()
        context_state.milk_management_state = {
            "analysis_intake": {
                "stage": "analysis_ready",
                "assessment_result": {
                    "data": {
                        "milk_flow_decision": {
                            "stage": "plan_ready",
                            "missing_user_inputs": [],
                            "plan_decision": {
                                "can_start_plan": True,
                                "next_tool": "milk_plan_preview_create",
                                "recommended_plan_type": "increase_milk",
                            },
                        },
                    },
                },
            }
        }

        request = build_agent_request(
            {"user_message": "先不做计划，我想再看看原因", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")

    def test_milk_management_fullness_with_red_flags_still_forces_health_search(self) -> None:
        request = build_agent_request(
            {"user_message": "吸完还胀，而且乳房红肿发热", "locale": "zh-CN"},
            {"loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(
            request["tool_choice"],
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "web_search"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("health_guidance_context:", request_context)

    def test_light_product_or_urgent_questions_do_not_add_health_guidance_context(self) -> None:
        light_request = build_agent_request({"user_message": "孕26周该准备什么", "locale": "zh-CN"})
        product_request = build_agent_request({"user_message": "帮我整理待产包清单", "locale": "zh-CN"})
        urgent_request = build_agent_request({"user_message": "今天胎动明显减少怎么办", "locale": "zh-CN"})

        for request in (light_request, product_request, urgent_request):
            self.assertEqual(len([tool for tool in request["tools"] if tool.get("type") == "web_search"]), 1)
            self.assertEqual(request["tool_choice"], "auto")
            self.assertEqual(request["include"], ["web_search_call.action.sources"])
            request_context = request["input"][0]["content"][0]["text"]
            self.assertNotIn("health_guidance_context:", request_context)

    def test_ag_ui_emits_clickable_web_search_citations(self) -> None:
        fake_client = _FakeStreamingClient(
            [
                {
                    "id": "resp-citations",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "乳房红肿发热需要警惕乳腺炎风险。",
                                    "annotations": [
                                        {
                                            "type": "url_citation",
                                            "url": "https://www.bfmed.org/protocols",
                                            "title": "Academy of Breastfeeding Medicine Protocols",
                                            "start_index": 0,
                                            "end_index": 8,
                                        },
                                        {
                                            "type": "url_citation",
                                            "url": "https://www.ncbi.nlm.nih.gov/books/NBK148970/",
                                            "title": "",
                                            "start_index": 9,
                                            "end_index": 16,
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        )
        events: list[dict[str, object]] = []

        run_agent_loop(
            fake_client,
            {"user_message": "乳房红肿还有点发热怎么办", "locale": "zh-CN"},
            on_ag_ui_event=events.append,
            on_text_delta=lambda _delta: None,
            ag_ui_run_id="run-citations",
            ag_ui_message_id="assistant-citations",
        )

        citation_events = [
            event
            for event in events
            if event.get("type") == "CUSTOM" and event.get("name") == "momcozy.web_search.citations"
        ]
        self.assertEqual(len(citation_events), 1)
        self.assertEqual(citation_events[0]["message_id"], "assistant-citations")
        value = citation_events[0]["value"]
        self.assertIsInstance(value, dict)
        citations = value["citations"]  # type: ignore[index]
        self.assertEqual(citations[0]["url"], "https://www.bfmed.org/protocols")  # type: ignore[index]
        self.assertEqual(citations[0]["title"], "Academy of Breastfeeding Medicine Protocols")  # type: ignore[index]
        self.assertEqual(citations[0]["index"], 1)  # type: ignore[index]
        self.assertEqual(
            citations[0]["display_text"],  # type: ignore[index]
            "ABM 哺乳医学临床指南：bfmed.org/protocols",
        )
        self.assertEqual(
            citations[0]["displayText"],  # type: ignore[index]
            "ABM 哺乳医学临床指南：bfmed.org/protocols",
        )
        self.assertEqual(citations[1]["title"], "www.ncbi.nlm.nih.gov")  # type: ignore[index]
        self.assertEqual(citations[1]["displayText"], "NCBI 医学资料：ncbi.nlm.nih.gov/books/...")  # type: ignore[index]

    def test_web_search_inline_citation_markers_are_removed_from_streamed_text(self) -> None:
        response = {
            "id": "resp-citation-markers",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "乳房红肿\ue200cite\ue202turn0search0\ue201需要尽快评估【1†source】。",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.cdc.gov/breastfeeding/",
                                    "title": "CDC Breastfeeding",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        stream_events = [
            {"type": "response.output_text.delta", "delta": "乳房红肿"},
            {"type": "response.output_text.delta", "delta": "\ue200cite\ue202turn0"},
            {"type": "response.output_text.delta", "delta": "search0\ue201需要尽快评估"},
            {"type": "response.output_text.delta", "delta": "【1†source】。"},
            {"type": "response.completed", "response": response},
        ]

        class _SplitCitationClient:
            def __init__(self) -> None:
                self.responses = self

            def create(self, **request: object) -> object:
                return stream_events if request.get("stream") else response

        text_parts: list[str] = []
        events: list[dict[str, object]] = []
        run_agent_loop(
            _SplitCitationClient(),
            {"user_message": "乳房红肿还有点发热怎么办", "locale": "zh-CN"},
            on_text_delta=text_parts.append,
            on_ag_ui_event=events.append,
            ag_ui_run_id="run-citation-marker-clean",
            ag_ui_message_id="assistant-citation-marker-clean",
        )

        text = "".join(text_parts)
        self.assertEqual(text, "乳房红肿需要尽快评估。")
        self.assertNotIn("\ue200", text)
        self.assertNotIn("†source", text)
        citation_events = [
            event
            for event in events
            if event.get("type") == "CUSTOM" and event.get("name") == "momcozy.web_search.citations"
        ]
        self.assertEqual(len(citation_events), 1)

    def test_web_search_citation_marker_cleaner_preserves_normal_text(self) -> None:
        self.assertEqual(clean_web_search_citation_markers("请先看【重点】不要用力揉。"), "请先看【重点】不要用力揉。")
        self.assertEqual(
            clean_web_search_citation_markers("参考 \ue200cite\ue202turn0search0\ue201 专业资料【1†source】。"),
            "参考 专业资料。",
        )

    def test_ag_ui_emits_web_search_process_status(self) -> None:
        fake_client = _FakeStreamingClient(
            [
                {
                    "id": "resp-web-search",
                    "output": [
                        {
                            "type": "web_search_call",
                            "id": "ws-1",
                            "status": "completed",
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "先看专业资料里的处理边界。",
                                }
                            ],
                        },
                    ],
                }
            ]
        )
        events: list[dict[str, object]] = []

        run_agent_loop(
            fake_client,
            {"user_message": "乳房红肿还有点发热怎么办", "locale": "zh-CN"},
            on_ag_ui_event=events.append,
            on_text_delta=lambda _delta: None,
            ag_ui_run_id="run-web-search",
            ag_ui_message_id="assistant-web-search",
        )

        web_search_events = [
            event
            for event in events
            if event.get("type") == "CUSTOM" and event.get("name") == "momcozy.agent.web_search"
        ]
        self.assertGreaterEqual(len(web_search_events), 2)
        statuses = [event["value"]["status"] for event in web_search_events]  # type: ignore[index]
        self.assertIn("searching", statuses)
        self.assertIn("completed", statuses)
        self.assertEqual(web_search_events[0]["semantic"]["label"], "我在查专业资料～")  # type: ignore[index]
        self.assertEqual(web_search_events[-1]["semantic"]["label"], "我查好专业资料啦")  # type: ignore[index]
        self.assertEqual(web_search_events[-1]["semantic"]["merge_key"], "web_search:current")  # type: ignore[index]

    def test_ag_ui_uses_streamed_web_search_sources_when_final_response_has_no_annotations(self) -> None:
        fake_client = _FakeStreamingClient(
            [
                {
                    "id": "resp-web-search-sources",
                    "output": [
                        {
                            "type": "web_search_call",
                            "id": "ws-1",
                            "status": "completed",
                            "action": {
                                "sources": [
                                    {
                                        "url": "https://www.bfmed.org/protocols",
                                        "title": "Academy of Breastfeeding Medicine Protocols",
                                    }
                                ]
                            },
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "先别用力揉，也别热敷很久。",
                                }
                            ],
                        },
                    ],
                }
            ]
        )
        events: list[dict[str, object]] = []

        run_agent_loop(
            fake_client,
            {"user_message": "我堵奶疼怎么办", "locale": "zh-CN"},
            on_ag_ui_event=events.append,
            on_text_delta=lambda _delta: None,
            ag_ui_run_id="run-stream-sources",
            ag_ui_message_id="assistant-stream-sources",
        )

        citation_events = [
            event
            for event in events
            if event.get("type") == "CUSTOM" and event.get("name") == "momcozy.web_search.citations"
        ]
        self.assertEqual(len(citation_events), 1)
        citations = citation_events[0]["value"]["citations"]  # type: ignore[index]
        self.assertEqual(citations[0]["url"], "https://www.bfmed.org/protocols")  # type: ignore[index]
        self.assertEqual(citations[0]["index"], 1)  # type: ignore[index]
        self.assertEqual(
            citations[0]["displayText"],  # type: ignore[index]
            "ABM 哺乳医学临床指南：bfmed.org/protocols",
        )

    def test_web_search_process_status_event_has_user_facing_semantics(self) -> None:
        event = web_search_status_event("searching", {"source_event": "response.web_search_call.searching"})

        self.assertEqual(event["name"], "momcozy.agent.web_search")
        self.assertEqual(event["value"]["label"], "我在查专业资料～")  # type: ignore[index]
        self.assertEqual(event["semantic"]["visibility"], "work_item")
        self.assertEqual(event["semantic"]["merge_key"], "web_search:current")

    def test_ag_ui_prewarm_saves_previous_response_without_tools(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi test client is not installed")

        fake_client = _FakeClient([{"id": "resp-prewarm", "output": []}])
        runtime = ChatRuntime(fake_client)
        client = TestClient(create_app(runtime=runtime))

        response = client.post(
            "/api/ag-ui-prewarm",
            json={
                "threadId": "thread-prewarm",
                "runId": "run-prewarm",
                "messages": [
                    {
                        "id": "msg-prewarm",
                        "role": "user",
                        "content": "隐藏预热，请只回复我在。",
                    }
                ],
                "state": {"locale": "zh-CN"},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "warmed")
        self.assertEqual(payload["thread_id"], "thread-prewarm")
        self.assertEqual(payload["response_id"], "resp-prewarm")
        self.assertEqual(runtime.sessions["thread-prewarm"].previous_response_id, "resp-prewarm")
        self.assertTrue(runtime.sessions["thread-prewarm"].context_state.environment_sent)
        self.assertEqual(len(fake_client.responses.requests), 1)
        request = fake_client.responses.requests[0]
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)
        self.assertEqual(request["max_output_tokens"], 24)

    def test_ag_ui_prewarm_namespaces_sessions_by_user_id(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi test client is not installed")

        fake_client = _FakeClient([{"id": "resp-user-a", "output": []}, {"id": "resp-user-b", "output": []}])
        runtime = ChatRuntime(fake_client)
        client = TestClient(create_app(runtime=runtime))

        for user_id in ("demo-phone-a", "demo-phone-b"):
            response = client.post(
                "/api/ag-ui-prewarm",
                json={
                    "threadId": "thread-shared-demo",
                    "runId": f"run-{user_id}",
                    "messages": [
                        {
                            "id": f"msg-{user_id}",
                            "role": "user",
                            "content": "隐藏预热，请只回复我在。",
                        }
                    ],
                    "forwardedProps": {"user_id": user_id, "locale": "zh-CN"},
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "warmed")

        self.assertEqual(len(runtime.sessions), 2)
        user_a = runtime.get_session("thread-shared-demo", user_id="demo-phone-a")
        user_b = runtime.get_session("thread-shared-demo", user_id="demo-phone-b")
        self.assertIsNot(user_a, user_b)
        self.assertEqual(user_a.previous_response_id, "resp-user-a")
        self.assertEqual(user_b.previous_response_id, "resp-user-b")
        self.assertEqual(user_a.conversation_id, "thread-shared-demo")
        self.assertEqual(user_b.conversation_id, "thread-shared-demo")

    def test_ag_ui_prewarm_does_not_overwrite_real_turn_or_context_when_stale(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi test client is not installed")

        fake_client = _MutatingClient([{"id": "resp-prewarm", "output": []}])
        runtime = ChatRuntime(fake_client)
        runtime.get_session("thread-stale")
        fake_client.responses.on_create = lambda: setattr(runtime.sessions["thread-stale"], "previous_response_id", "resp-real")
        client = TestClient(create_app(runtime=runtime))

        response = client.post(
            "/api/ag-ui-prewarm",
            json={
                "threadId": "thread-stale",
                "runId": "run-prewarm",
                "messages": [
                    {
                        "id": "msg-prewarm",
                        "role": "user",
                        "content": "隐藏预热，请只回复我在。",
                    }
                ],
                "state": {"locale": "zh-CN"},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "stale")
        self.assertEqual(runtime.sessions["thread-stale"].previous_response_id, "resp-real")
        self.assertFalse(runtime.sessions["thread-stale"].context_state.environment_sent)

    def test_ag_ui_ignores_client_supplied_previous_response_id(self) -> None:
        async def collect_events() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-next",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "继续处理。"}],
                            }
                        ],
                    }
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            runtime.get_session("thread-prev", user_id="demo-phone-a").previous_response_id = "resp-server"
            payload = {
                "threadId": "thread-prev",
                "runId": "run-prev",
                "messages": [{"role": "user", "content": "继续"}],
                "forwardedProps": {
                    "user_id": "demo-phone-a",
                    "previous_response_id": "resp-client",
                },
            }
            inputs = _runtime_inputs_from_ag_ui(payload)
            self.assertNotIn("previous_response_id", inputs)
            stream = stream_ag_ui_events(payload, inputs, runtime)
            events = [event async for event in stream]
            return events, client.responses.requests

        events, requests = asyncio.run(collect_events())

        self.assertTrue(any(event.get("type") == "RUN_FINISHED" for event in events))
        self.assertEqual(requests[0].get("previous_response_id"), "resp-server")
        self.assertNotEqual(requests[0].get("previous_response_id"), "resp-client")

    def test_profile_onboarding_short_answer_forces_profile_update(self) -> None:
        async def collect_events() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-profile",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "很高兴认识你。"}],
                            }
                        ],
                    }
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            payload = {
                "threadId": "thread-profile-onboarding",
                "runId": "run-profile-onboarding",
                "messages": [{"role": "user", "content": "小雨，29岁"}],
                "forwardedProps": {
                    "user_id": "profile-onboarding-short",
                    "profile_onboarding_pending": True,
                },
            }
            inputs = _runtime_inputs_from_ag_ui(payload)
            events = [event async for event in stream_ag_ui_events(payload, inputs, runtime)]
            return events, client.responses.requests

        events, requests = asyncio.run(collect_events())

        self.assertTrue(any(event.get("type") == "RUN_FINISHED" for event in events))
        request = requests[0]
        self.assertEqual(
            request.get("tool_choice"),
            {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "profile_update"}],
            },
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("profile_onboarding_context:", request_context)
        self.assertIn("parsed_profile_update_args: display_name=小雨；age=29", request_context)
        self.assertIn("本轮必须先调用 profile_update", request_context)

    def test_ag_ui_cancel_marks_active_run_without_waiting_for_run_lock(self) -> None:
        runtime = ChatRuntime(object())
        session = runtime.get_session("thread-cancel-api", user_id="demo-phone-a")
        session.active_run_id = "run-active"
        session.run_lock.acquire()
        try:
            result = runtime.cancel_run("thread-cancel-api", user_id="demo-phone-a", run_id="run-active")
        finally:
            session.run_lock.release()

        self.assertEqual(result["status"], "cancel_requested")
        self.assertTrue(result["cancelled"])
        self.assertIn("run-active", session.cancelled_run_ids)

    def test_ag_ui_waiting_for_previous_run_emits_visible_status(self) -> None:
        async def collect_events() -> tuple[dict[str, object], list[dict[str, object]]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-after-wait",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "继续处理。"}],
                            }
                        ],
                    }
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            session = runtime.get_session("thread-busy", user_id="demo-phone-a")
            session.run_lock.acquire()
            payload = {
                "threadId": "thread-busy",
                "runId": "run-wait",
                "messages": [{"role": "user", "content": "继续"}],
                "forwardedProps": {"user_id": "demo-phone-a"},
            }
            inputs = _runtime_inputs_from_ag_ui(payload)
            stream = stream_ag_ui_events(payload, inputs, runtime)
            try:
                first = await asyncio.wait_for(stream.__anext__(), timeout=1)
            finally:
                session.run_lock.release()
            events = [event async for event in stream]
            return first, events

        first_event, remaining_events = asyncio.run(collect_events())

        self.assertEqual(first_event.get("type"), "CUSTOM")
        self.assertEqual(first_event.get("name"), "momcozy.agent.status")
        value = first_event.get("value")
        self.assertIsInstance(value, dict)
        self.assertEqual(value.get("message"), "上一轮正在安全收尾，我马上继续。")
        self.assertEqual(first_event.get("semantic", {}).get("visibility"), "status")
        self.assertTrue(any(event.get("type") == "RUN_FINISHED" for event in remaining_events))

    def test_ag_ui_cancelled_queued_run_exits_without_waiting_for_run_lock_release(self) -> None:
        async def collect_events() -> tuple[dict[str, object], dict[str, object], bool, list[dict[str, object]]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-should-not-start",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "不应开始。"}],
                            }
                        ],
                    }
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            session = runtime.get_session("thread-queued", user_id="demo-phone-a")
            session.run_lock.acquire()
            payload = {
                "threadId": "thread-queued",
                "runId": "run-queued",
                "messages": [{"role": "user", "content": "继续"}],
                "forwardedProps": {"user_id": "demo-phone-a"},
            }
            inputs = _runtime_inputs_from_ag_ui(payload)
            stream = stream_ag_ui_events(payload, inputs, runtime)
            try:
                first = await asyncio.wait_for(stream.__anext__(), timeout=1)
                runtime.cancel_run("thread-queued", user_id="demo-phone-a", run_id="run-queued")
                second = await asyncio.wait_for(stream.__anext__(), timeout=1)
                try:
                    await asyncio.wait_for(stream.__anext__(), timeout=1)
                    ended = False
                except StopAsyncIteration:
                    ended = True
                return first, second, ended, client.responses.requests
            finally:
                if session.run_lock.locked():
                    session.run_lock.release()

        first_event, second_event, ended, requests = asyncio.run(collect_events())

        self.assertEqual(first_event.get("type"), "CUSTOM")
        self.assertEqual(second_event.get("type"), "RUN_ERROR")
        self.assertEqual(second_event.get("code"), "RUN_CANCELLED")
        self.assertTrue(ended)
        self.assertFalse(requests)

    def test_ag_ui_cancelled_run_does_not_commit_previous_response_id(self) -> None:
        async def collect_events() -> tuple[list[dict[str, object]], list[dict[str, object]], str | None]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-should-not-commit",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "不应提交。"}],
                            }
                        ],
                    }
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            session = runtime.get_session("thread-cancelled", user_id="demo-phone-a")
            session.previous_response_id = "resp-stable"
            runtime.cancel_run("thread-cancelled", user_id="demo-phone-a", run_id="run-cancelled")
            payload = {
                "threadId": "thread-cancelled",
                "runId": "run-cancelled",
                "messages": [{"role": "user", "content": "停止这一轮"}],
                "forwardedProps": {"user_id": "demo-phone-a"},
            }
            inputs = _runtime_inputs_from_ag_ui(payload)
            events = [event async for event in stream_ag_ui_events(payload, inputs, runtime)]
            return events, client.responses.requests, session.previous_response_id

        events, requests, previous_response_id = asyncio.run(collect_events())

        self.assertFalse(requests)
        self.assertEqual(previous_response_id, "resp-stable")
        self.assertTrue(any(event.get("type") == "RUN_ERROR" and event.get("code") == "RUN_CANCELLED" for event in events))

    def test_agent_loop_cancel_after_tool_call_response_stops_before_tool_output_request(self) -> None:
        cancelled = False
        client = _MutatingClient(
            [
                {
                    "id": "resp-tool-call",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call-status",
                            "name": "milk_status_query",
                            "arguments": "{}",
                        }
                    ],
                }
            ]
        )

        def cancel_after_model_response() -> None:
            nonlocal cancelled
            cancelled = True

        client.responses.on_create = cancel_after_model_response

        with self.assertRaises(AgentRunCancelled):
            run_agent_loop(
                client,
                {"user_message": "看看奶量", "locale": "zh-CN"},
                cancel_requested=lambda: cancelled,
            )

        self.assertEqual(len(client.responses.requests), 1)

    def test_agent_loop_cancel_during_blocking_response_stream_closes_stream(self) -> None:
        class BlockingResponseStream:
            def __init__(self) -> None:
                self.iter_started = threading.Event()
                self.closed = threading.Event()

            def __iter__(self) -> "BlockingResponseStream":
                return self

            def __next__(self) -> dict[str, object]:
                self.iter_started.set()
                self.closed.wait(timeout=5)
                raise StopIteration

            def close(self) -> None:
                self.closed.set()

        class BlockingStreamingResponses:
            def __init__(self, stream: BlockingResponseStream) -> None:
                self.stream = stream
                self.requests: list[dict[str, object]] = []

            def create(self, **request: object) -> object:
                self.requests.append(request)
                if request.get("stream"):
                    return self.stream
                return {"id": "resp-non-stream", "output": []}

        class BlockingStreamingClient:
            def __init__(self, stream: BlockingResponseStream) -> None:
                self.responses = BlockingStreamingResponses(stream)

        cancel_event = threading.Event()
        response_stream = BlockingResponseStream()
        client = BlockingStreamingClient(response_stream)
        result: dict[str, BaseException] = {}

        def run_loop() -> None:
            try:
                run_agent_loop(
                    client,
                    {"user_message": "继续", "locale": "zh-CN"},
                    on_text_delta=lambda _delta: None,
                    cancel_requested=cancel_event.is_set,
                )
            except BaseException as exc:
                result["exception"] = exc

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        self.assertTrue(response_stream.iter_started.wait(timeout=1))
        cancel_event.set()
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result.get("exception"), AgentRunCancelled)
        self.assertTrue(response_stream.closed.is_set())
        self.assertEqual(len(client.responses.requests), 1)

    def test_client_event_context_is_namespaced_by_user_id(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi test client is not installed")

        runtime = ChatRuntime(object())
        client = TestClient(create_app(runtime=runtime))

        for user_id, label in (("demo-phone-a", "A 完成咨询"), ("demo-phone-b", "B 完成咨询")):
            response = client.post(
                "/api/client-event",
                json={
                    "thread_id": "thread-client-event",
                    "user_id": user_id,
                    "event_type": "ibclc_completed",
                    "label": label,
                },
            )
            self.assertEqual(response.status_code, 200)

        user_a_events = runtime.get_session("thread-client-event", user_id="demo-phone-a").context_state.client_events
        user_b_events = runtime.get_session("thread-client-event", user_id="demo-phone-b").context_state.client_events

        self.assertEqual(len(user_a_events), 1)
        self.assertEqual(len(user_b_events), 1)
        self.assertIn("A 完成咨询", user_a_events[0])
        self.assertIn("B 完成咨询", user_b_events[0])
        self.assertNotIn("B 完成咨询", user_a_events[0])

    def test_client_event_prefers_existing_agent_session_for_legacy_user_id(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi test client is not installed")

        runtime = ChatRuntime(object())
        creating_session = runtime.get_session("thread-ibclc-card", user_id="demo-phone-a")
        creating_session.previous_response_id = "resp-card"
        legacy_session = runtime.get_session("thread-ibclc-card", user_id="old-ibclc-user")
        legacy_session.context_state.client_events.append("old empty event")
        client = TestClient(create_app(runtime=runtime))

        response = client.post(
            "/api/client-event",
            json={
                "thread_id": "thread-ibclc-card",
                "user_id": "old-ibclc-user",
                "event_type": "ibclc_consult_completed",
                "label": "IBCLC 已结束",
                "metadata": {"source": "ibclc-chat"},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(creating_session.context_state.client_events), 1)
        self.assertIn("IBCLC 已结束", creating_session.context_state.client_events[0])
        self.assertEqual(legacy_session.context_state.client_events, ["old empty event"])

    def test_client_event_with_wrong_user_id_stays_in_requested_namespace_unless_legacy_ibclc(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi test client is not installed")

        runtime = ChatRuntime(object())
        creating_session = runtime.get_session("thread-client-event-fallback", user_id="demo-phone-a")
        creating_session.previous_response_id = "resp-card"
        stale_session = runtime.get_session("thread-client-event-fallback", user_id="stale-device-user")
        client = TestClient(create_app(runtime=runtime))

        response = client.post(
            "/api/client-event",
            json={
                "thread_id": "thread-client-event-fallback",
                "user_id": "stale-device-user",
                "event_type": "pump_session_ended",
                "label": "吸奶已结束",
                "metadata": {"source": "pump-session"},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(creating_session.context_state.client_events, [])
        self.assertEqual(len(stale_session.context_state.client_events), 1)
        self.assertIn("吸奶已结束", stale_session.context_state.client_events[0])

    def test_context_clone_preserves_milk_and_birth_intake_state(self) -> None:
        state = ContextState()
        state.birth_journey_intake = {"next_step": "birth_path", "profile": {"first_birth": True}}
        state.milk_management_state = {"last_plan_preview": {"draft": {"plan_type": "increase_milk"}}}

        cloned = _clone_context_state(state)
        cloned.birth_journey_intake["profile"]["first_birth"] = False
        cloned.milk_management_state["last_plan_preview"]["draft"]["plan_type"] = "maintain_milk"

        self.assertTrue(state.birth_journey_intake["profile"]["first_birth"])
        self.assertEqual(state.milk_management_state["last_plan_preview"]["draft"]["plan_type"], "increase_milk")
        self.assertFalse(cloned.birth_journey_intake["profile"]["first_birth"])
        self.assertEqual(cloned.milk_management_state["last_plan_preview"]["draft"]["plan_type"], "maintain_milk")

    def test_birth_prep_slot_extraction_runs_sidecar_and_merges_confirmed_slots(self) -> None:
        class FakeSlotExtractor:
            def __init__(self) -> None:
                self.requests = []

            def extract(self, request):
                self.requests.append(request)
                return [
                    {
                        "field_id": "due_date_or_week",
                        "value": "孕32周",
                        "evidence": "我现在孕32周",
                        "confidence": 0.91,
                    }
                ]

        extractor = FakeSlotExtractor()
        runtime = ChatRuntime(object(), slot_extractor=extractor)
        session = runtime.get_session("thread-slots")
        merge_hospital_bag_slots(session.context_state, {"due_date_or_week": "孕30周"})
        merge_extracted_birth_prep_slots(
            session.context_state,
            [{"field_id": "display_name", "value": "Henson", "evidence": "我叫 Henson", "confidence": 0.95}],
            turn_id=1,
        )
        session.context_state.last_assistant_message = "你现在孕几周？"

        with session.run_lock:
            thread = _schedule_birth_prep_slot_extraction(
                session,
                runtime,
                {
                    "user_message": "我现在孕32周",
                    "recent_user_messages": ["我叫 Henson", "我 28 岁", "我现在孕32周"],
                    "locale": "zh-CN",
                    "timezone": "Asia/Shanghai",
                    "message_sent_at": "2026-06-17T09:00:00+08:00",
                },
                run_id="run-slots",
            )
            self.assertIsNotNone(thread)

        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(extractor.requests[0].current_slots["due_date_or_week"], "孕30周")
        self.assertEqual(extractor.requests[0].current_slots["display_name"], "Henson")
        self.assertEqual(extractor.requests[0].recent_user_messages, ["我叫 Henson", "我 28 岁", "我现在孕32周"])
        self.assertEqual(extractor.requests[0].previous_assistant_message, "你现在孕几周？")
        self.assertEqual(hospital_bag_slots(session.context_state)["due_date_or_week"], "孕32周")
        self.assertEqual(profile_slots(session.context_state)["display_name"], "Henson")
        record = session.context_state.birth_prep_slots["hospital_bag"]["due_date_or_week"]
        self.assertEqual(record["status"], "confirmed")
        self.assertEqual(record["source"], "user_text")
        self.assertEqual(record["turn_id"], 1)

    def test_runtime_inputs_include_recent_three_user_messages_for_slot_extraction(self) -> None:
        inputs = _runtime_inputs_from_ag_ui(
            {
                "messages": [
                    {"role": "user", "content": "我想制定孕期计划"},
                    {"role": "assistant", "content": "你叫什么？"},
                    {"role": "user", "content": "我叫 Henson"},
                    {"role": "assistant", "content": "你今年多大？"},
                    {"role": "user", "content": "28 岁"},
                    {"role": "assistant", "content": "你现在孕几周？"},
                    {"role": "user", "content": "孕32周"},
                ],
                "state": {"locale": "zh-CN"},
            }
        )

        self.assertEqual(inputs["user_message"], "孕32周")
        self.assertEqual(inputs["recent_user_messages"], ["我叫 Henson", "28 岁", "孕32周"])

    def test_runtime_inputs_forward_background_milk_record_context_policy(self) -> None:
        inputs = _runtime_inputs_from_ag_ui(
            {
                "messages": [{"role": "user", "content": "后台奶量分析"}],
                "forwardedProps": {
                    "user_id": "milk-background-inputs",
                    "serviceDomain": "milk_management",
                    "triggerSource": "background",
                    "milkContextMode": "analysis",
                    "milkRecordContextPolicy": {
                        "include_raw_records": True,
                        "raw_days": 7,
                        "rollup_days": 7,
                        "raw_limit": 160,
                    },
                },
            }
        )

        self.assertEqual(inputs["service_domain"], "milk_management")
        self.assertEqual(inputs["trigger_source"], "background")
        self.assertEqual(inputs["milk_context_mode"], "analysis")
        self.assertEqual(inputs["milk_record_context_policy"]["raw_days"], 7)
        self.assertTrue(inputs["milk_record_context_policy"]["include_raw_records"])

    def test_chat_runtime_sessions_have_distinct_run_locks(self) -> None:
        runtime = ChatRuntime(object())
        first = runtime.get_session("thread-lock-a")
        second = runtime.get_session("thread-lock-b")

        self.assertIsNot(first.run_lock, second.run_lock)
        self.assertTrue(first.run_lock.acquire(blocking=False))
        self.addCleanup(first.run_lock.release)
        self.assertTrue(second.run_lock.acquire(blocking=False))
        self.addCleanup(second.run_lock.release)

    def test_chat_runtime_sessions_are_distinct_for_same_thread_and_different_users(self) -> None:
        runtime = ChatRuntime(object())
        first = runtime.get_session("thread-same", user_id="demo-phone-a")
        second = runtime.get_session("thread-same", user_id="demo-phone-b")
        anonymous = runtime.get_session("thread-same")

        first.previous_response_id = "resp-a"
        second.previous_response_id = "resp-b"

        self.assertIsNot(first, second)
        self.assertIsNot(first, anonymous)
        self.assertIsNot(second, anonymous)
        self.assertEqual(runtime.get_session("thread-same", user_id="demo-phone-a").previous_response_id, "resp-a")
        self.assertEqual(runtime.get_session("thread-same", user_id="demo-phone-b").previous_response_id, "resp-b")
        self.assertIsNone(anonymous.previous_response_id)

    def test_tool_call_phase_events_include_tool_name(self) -> None:
        args_event = tool_call_args_event(
            "call-1",
            "milk_plan_preview_create",
            {"plan_type": "increase_milk"},
            response_id="resp-1",
            output_index=0,
            item_id="item-1",
        )
        end_event = tool_call_end_event(
            "call-1",
            "milk_plan_preview_create",
            response_id="resp-1",
            output_index=0,
            item_id="item-1",
        )
        result_event = tool_call_result_event(
            "message-1",
            "call-1",
            "milk_plan_preview_create",
            {"ok": True, "tool_name": "milk_plan_preview_create", "result": {"status": "plan_preview_ready"}},
            response_id="resp-1",
            output_index=0,
            item_id="item-1",
        )

        self.assertEqual(args_event["tool_call_name"], "milk_plan_preview_create")
        self.assertEqual(end_event["tool_call_name"], "milk_plan_preview_create")
        self.assertEqual(result_event["tool_call_name"], "milk_plan_preview_create")
        self.assertEqual(json.loads(result_event["content"])["tool_name"], "milk_plan_preview_create")
        self.assertEqual(args_event["semantic"]["visibility"], "work_item")
        self.assertEqual(args_event["semantic"]["phase"], "planning")
        self.assertEqual(args_event["semantic"]["label"], "我先帮你拟一版奶量计划～")
        self.assertEqual(result_event["semantic"]["label"], "我拟好奶量计划草稿啦")

    def test_tool_start_events_include_user_facing_semantic_contract(self) -> None:
        start_event = tool_call_start_event(
            "call-1",
            "milk_records_query",
            response_id="resp-1",
            output_index=0,
            item_id="item-1",
        )

        self.assertEqual(
            start_event["semantic"],
            {
                "phase": "reading",
                "label": "我先看看吸奶和喂养记录～",
                "visibility": "work_item",
                "merge_key": "tool:call-1",
                "priority": 50,
            },
        )

    def test_artifact_tool_result_events_include_specific_done_label(self) -> None:
        result_event = tool_call_result_event(
            "message-1",
            "call-bag",
            "hospital_bag_card_create",
            {"ok": True, "tool_name": "hospital_bag_card_create", "result": {"status": "card_created"}},
            response_id="resp-1",
            output_index=0,
            item_id="item-1",
        )

        self.assertEqual(result_event["semantic"]["visibility"], "work_item")
        self.assertEqual(result_event["semantic"]["label"], "我已经帮你生成好待产包清单啦")

    def test_milk_analysis_artifact_uses_analysis_done_label(self) -> None:
        event = artifact_created_event(
            artifact_id="analysis-1",
            artifact_type="milk_analysis_card",
            tool_call_id="call-analysis",
            tool_call_name="milk_analysis_evaluate",
            artifact={"card_type": "milk_analysis_card"},
        )

        self.assertEqual(event["semantic"]["label"], "我已经整理好奶量分析结果啦")

    def test_artifact_tool_outputs_include_specific_final_response_instructions(self) -> None:
        birth_plan_form = model_tool_output(
            {
                "ok": True,
                "tool_name": "birth_plan_form_create",
                "result": {
                    "tool_name": "ui_form_create",
                    "status": "form_created",
                    "form": {"id": "birth_plan_card_intake", "title": "信息采集", "fields": []},
                },
            }
        )
        ibclc_card = model_tool_output(
            {
                "ok": True,
                "tool_name": "ibclc_consult_card_create",
                "result": {
                    "tool_name": "ibclc_consult_card_create",
                    "status": "ibclc_consult_card_created",
                    "card": {
                        "card_type": "ibclc_consult_card",
                        "schema_version": "1.1",
                        "consultant": {"name": "Emily Chen"},
                        "recommendation_reason": "我推荐 Emily Chen，是因为这位顾问是 IBCLC 国际认证哺乳顾问，适合帮你一起看含乳和排乳细节问题。",
                        "chat": {"note": "启动咨询后，会自动将你的问题同步给顾问"},
                    },
                },
            }
        )
        support_ticket = model_tool_output(
            {
                "ok": True,
                "tool_name": "support_ticket_draft_create",
                "result": {
                    "tool_name": "support_ticket_draft_create",
                    "status": "ticket_draft_created",
                    "ticket": {"draft_id": "draft_1"},
                    "submit_label": "确认并提交",
                },
            }
        )

        self.assertIn("分娩沟通单信息表已经展示", birth_plan_form["final_response_instruction"])
        self.assertIn("不要提表单里没有的字段", birth_plan_form["final_response_instruction"])
        self.assertIn("IBCLC 咨询入口已经展示", ibclc_card["final_response_instruction"])
        self.assertIn("不要承诺已经预约、已经接通", ibclc_card["final_response_instruction"])
        self.assertIn("不用一个人反复猜", ibclc_card["final_response_instruction"])
        self.assertIn("我推荐 Emily Chen", ibclc_card["final_response_instruction"])
        self.assertIn("不要补写位置、距离、排班", ibclc_card["final_response_instruction"])
        self.assertIn("说明更适合让 IBCLC 顾问继续看", FUNCTION_TOOLS["ibclc_consult_card_create"]["description"])
        self.assertIn("不可调用场景", FUNCTION_TOOLS["ibclc_consult_card_create"]["description"])
        self.assertIn("用户只是问“需不需要/要不要/是不是该找", FUNCTION_TOOLS["ibclc_consult_card_create"]["description"])
        self.assertIn("售后工单信息表已经展示", support_ticket["final_response_instruction"])
        self.assertIn("不要提“草稿”“未提交”“确认后才提交”", support_ticket["final_response_instruction"])
        self.assertIn("结合当前问题场景做情绪承接", support_ticket["final_response_instruction"])
        self.assertNotIn("assistant_followup", support_ticket)
        self.assertNotIn("assistant_followup.message", support_ticket["final_response_instruction"])
        self.assertIn("最多两段", support_ticket["final_response_instruction"])
        self.assertIn("交付信息只能出现一次", support_ticket["final_response_instruction"])
        self.assertIn("不要列举购买渠道、照片、视频、联系方式", support_ticket["final_response_instruction"])

    def test_ibclc_blocked_tool_output_keeps_confirmation_contract(self) -> None:
        blocked = model_tool_output(
            {
                "ok": True,
                "tool_name": "ibclc_consult_card_create",
                "result": {
                    "tool_name": "ibclc_consult_card_create",
                    "status": "ibclc_consult_blocked",
                    "reason": "missing_explicit_ibclc_request",
                    "requires_confirmation": True,
                    "confirmation_question": "要我帮你打开 IBCLC 在线咨询入口吗？",
                },
            }
        )

        self.assertEqual(blocked["status"], "ibclc_consult_blocked")
        self.assertTrue(blocked["requires_confirmation"])
        self.assertIn("IBCLC 在线咨询入口", blocked["confirmation_question"])
        self.assertIn("咨询卡片没有创建", blocked["final_response_instruction"])
        self.assertIn("不要说已经打开", blocked["final_response_instruction"])
        self.assertNotIn("card", blocked)

    def test_loop_emits_single_status_channel_and_explicit_artifact_events(self) -> None:
        client = _FakeClient(
            [
                {
                    "id": "resp-tool",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "item-1",
                            "call_id": "call-1",
                            "name": "support_ticket_draft_create",
                            "arguments": json.dumps(
                                {
                                    "issue_type": "malfunction",
                                    "issue_summary": "吸奶器无法启动",
                                    "urgency": "normal",
                                    "user_confirmed": True,
                                }
                            ),
                        }
                    ],
                },
                {"id": "resp-final", "output": []},
            ]
        )
        events: list[dict[str, object]] = []

        run_agent_loop(
            client,
            {"user_message": "帮我提交售后", "locale": "zh-CN"},
            on_ag_ui_event=events.append,
            ag_ui_thread_id="thread-1",
            ag_ui_run_id="run-1",
        )

        event_types = [str(event.get("type")) for event in events]
        self.assertNotIn("ACTIVITY_SNAPSHOT", event_types)
        self.assertIn("CUSTOM", event_types)
        self.assertIn("TOOL_CALL_RESULT", event_types)
        self.assertIn("ARTIFACT_CREATED", event_types)
        self.assertIn("CONFIRMATION_REQUIRED", event_types)
        self.assertLess(event_types.index("TOOL_CALL_RESULT"), event_types.index("ARTIFACT_CREATED"))
        self.assertLess(event_types.index("ARTIFACT_CREATED"), event_types.index("CONFIRMATION_REQUIRED"))
        status_messages = [
            str(event.get("value", {}).get("message", ""))
            for event in events
            if event.get("type") == "CUSTOM" and event.get("name") == "momcozy.agent.status"
        ]
        self.assertTrue(status_messages)
        self.assertFalse(any("support_ticket_draft_create" in message for message in status_messages))
        self.assertFalse(any(message.startswith("Tool ") for message in status_messages))

        artifact = next(event for event in events if event.get("type") == "ARTIFACT_CREATED")
        self.assertEqual(artifact["artifact_type"], "support_ticket")
        self.assertEqual(artifact["semantic"]["visibility"], "artifact")
        confirmation = next(event for event in events if event.get("type") == "CONFIRMATION_REQUIRED")
        self.assertEqual(confirmation["title"], "我需要你确认售后信息")
        self.assertNotIn("草稿", confirmation["message"])
        self.assertNotIn("确认后才会提交", confirmation["message"])
        self.assertEqual(confirmation["semantic"]["phase"], "confirming")

    def test_model_tool_output_compacts_artifact_payloads(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "labor_communication_card_create",
            "result": {
                "status": "card_created",
                "card": {
                    "card_type": "birth_plan_card",
                    "schema_version": "1.0",
                    "card_json": {
                        "card_type": "birth_plan_card",
                        "schema_version": "1.0",
                        "communication": ["a", "b", "c"],
                    },
                },
                "assistant_followup": {"message": "卡片已经生成好了。"},
            },
        }

        compact = model_tool_output(raw)

        self.assertEqual(compact["status"], "card_created")
        self.assertNotIn("assistant_followup", compact)
        self.assertEqual(compact["card"], {"card_type": "birth_plan_card", "schema_version": "1.0", "created": True})
        self.assertIn("建议内容", compact["final_response_instruction"])
        self.assertIn("卡片已经生成好了", compact["final_response_instruction"])
        self.assertIn("自然表达", compact["final_response_instruction"])
        self.assertNotIn("card_json", json.dumps(compact, ensure_ascii=False))

    def test_hospital_bag_cart_update_final_response_links_to_cart(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "hospital_bag_cart_update",
            "result": {
                "tool_name": "hospital_bag_cart_update",
                "status": "cart_updated",
                "summary": "已帮你从购物车里删掉「防溢乳垫」。",
                "cart_update": {
                    "action": "remove_items",
                    "message": "已帮你从购物车里删掉「防溢乳垫」。",
                    "groups": [],
                    "totals": {"total": 120, "itemCount": 2},
                },
            },
        }

        compact = model_tool_output(raw)
        instruction = compact["final_response_instruction"]

        self.assertIn("待产包购物车已经处理完本次调整或确认没有变化", instruction)
        self.assertIn("最后一行必须单独使用这个 Markdown 购物车链接", instruction)
        self.assertIn("**[打开待产包购物车](/hospital-bag-cart)**", instruction)
        self.assertEqual(instruction.count("/hospital-bag-cart"), 1)
        self.assertIn("已帮你从购物车里删掉", instruction)

        unchanged = model_tool_output(
            {
                "ok": True,
                "tool_name": "hospital_bag_cart_update",
                "result": {
                    "tool_name": "hospital_bag_cart_update",
                    "status": "cart_unchanged",
                    "summary": "当前购物车先不改。",
                    "cart_update": {"action": "clarify", "message": "当前购物车先不改。"},
                },
            }
        )
        self.assertIn("**[打开待产包购物车](/hospital-bag-cart)**", unchanged["final_response_instruction"])

    def test_hospital_bag_cart_update_clarification_does_not_force_cart_link(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "hospital_bag_cart_update",
            "result": {
                "tool_name": "hospital_bag_cart_update",
                "status": "needs_clarification",
                "summary": "你想删掉哪一件？",
                "cart_update": {"action": "clarify", "message": "你想删掉哪一件？"},
            },
        }

        compact = model_tool_output(raw)

        self.assertNotIn("final_response_instruction", compact)
        self.assertNotIn("/hospital-bag-cart", json.dumps(compact, ensure_ascii=False))

    def test_read_skill_file_records_loaded_reference_context(self) -> None:
        context_state = ContextState()
        client = _FakeClient(
            [
                {
                    "id": "resp-tool",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "item-1",
                            "call_id": "call-1",
                            "name": "read_skill_file",
                            "arguments": json.dumps(
                                {
                                    "skill_id": "device-guidance",
                                    "kind": "references",
                                    "path": "references/air1/faq.md",
                                }
                            ),
                        }
                    ],
                },
                {"id": "resp-final", "output": []},
            ]
        )

        run_agent_loop(
            client,
            {"user_message": "Air1 指示灯是什么意思", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
            ag_ui_thread_id="thread-1",
            ag_ui_run_id="run-1",
        )

        self.assertTrue(any("device-guidance/references/air1/faq.md 已在当前会话中读取过" in item for item in context_state.loaded_references))

        request = build_agent_request(
            {"user_message": "继续", "locale": "zh-CN", "previous_response_id": "resp-final"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("loaded_skill_context:", request_context)
        self.assertIn("loaded_reference_context:", request_context)
        self.assertIn("不要重复调用 load_skill", request_context)
        self.assertIn("不要重复调用 read_skill_file", request_context)

    def test_device_manual_search_relevant_images_are_recorded_not_immediately_forwarded(self) -> None:
        context_state = ContextState()
        client = _FakeClient(
            [
                {
                    "id": "resp-tool",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "item-1",
                            "call_id": "call-1",
                            "name": "device_manual_search",
                            "arguments": json.dumps(
                                {
                                    "model": "Air1",
                                    "query": "14mm",
                                    "topic": "flange",
                                    "measured_nipple_mm": 14,
                                    "max_results": 2,
                                }
                            ),
                        }
                    ],
                },
                {"id": "resp-final", "output": []},
            ]
        )

        run_agent_loop(
            client,
            {"user_message": "14mm", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
            ag_ui_thread_id="thread-1",
            ag_ui_run_id="run-1",
        )

        followup_input = client.responses.requests[1]["input"]
        self.assertEqual(len(followup_input), 1)
        self.assertEqual(followup_input[0]["type"], "function_call_output")
        self.assertTrue(context_state.available_tool_images)
        self.assertEqual(context_state.available_tool_images[0]["url"], "/skill-assets/device-guidance/air1/images/air1_guide_flange_measurement.png")

    def test_later_device_step_image_keeps_metadata_when_displayed(self) -> None:
        context_state = ContextState()
        charging_url = "/skill-assets/device-guidance/air1/images/air1_guide_charging_methods.png"
        client = _FakeClient(
            [
                {
                    "id": "resp-tool",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "item-1",
                            "call_id": "call-1",
                            "name": "device_manual_search",
                            "arguments": json.dumps(
                                {
                                    "model": "Air1",
                                    "query": "我刚收到吸奶器，想开箱",
                                    "topic": "unboxing",
                                    "max_results": 2,
                                }
                            ),
                        }
                    ],
                },
                {
                    "id": "resp-final",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": f"![Air1 充电方式]({charging_url})\n先确认电量。",
                                }
                            ],
                        }
                    ],
                },
            ]
        )

        run_agent_loop(
            client,
            {"user_message": "我刚收到吸奶器，想开箱", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
            ag_ui_thread_id="thread-1",
            ag_ui_run_id="run-1",
        )

        recorded_urls = [image.get("url") for image in context_state.available_tool_images]
        self.assertIn(charging_url, recorded_urls)
        self.assertEqual(context_state.last_displayed_tool_image["url"], charging_url)
        self.assertEqual(context_state.last_displayed_tool_image["alt"], "Air1 充电方式")
        self.assertEqual(context_state.last_displayed_tool_image["module"], "guide.charging")
        self.assertEqual(context_state.active_device_module, "guide.charging")

    def test_prior_tool_images_are_forwarded_only_when_user_asks_for_image_help(self) -> None:
        context_state = ContextState()
        context_state.available_tool_images = [
            {
                "alt": "Air1 乳头测量与法兰选择",
                "module": "guide.flange",
                "url": "/skill-assets/device-guidance/air1/images/air1_guide_flange_measurement.png",
            }
        ]

        regular_request = build_agent_request(
            {"user_message": "继续下一步", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
        )
        self.assertEqual(len(regular_request["input"]), 1)

        image_request = build_agent_request(
            {"user_message": "刚才这张图怎么看？", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
        )

        self.assertEqual(len(image_request["input"]), 2)
        image_item = image_request["input"][1]
        self.assertEqual(image_item["role"], "user")
        content = image_item["content"]
        self.assertIn("官方步骤图", content[0]["text"])
        image_parts = [part for part in content if part["type"] == "input_image"]
        self.assertGreaterEqual(len(image_parts), 1)
        self.assertTrue(image_parts[0]["image_url"].startswith("data:image/png;base64,"))

        numbered_label_request = build_agent_request(
            {"user_message": "图中编号11是什么？", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
        )
        self.assertEqual(len(numbered_label_request["input"]), 2)
        numbered_label_content = numbered_label_request["input"][1]["content"]
        self.assertIn("官方步骤图", numbered_label_content[0]["text"])
        self.assertTrue(
            any(part["type"] == "input_image" for part in numbered_label_content),
        )

    def test_prior_tool_image_numbered_label_uses_last_displayed_image(self) -> None:
        context_state = ContextState()
        context_state.available_tool_images = [
            {
                "alt": "Air1 核心部件",
                "module": "guide.parts",
                "url": "/skill-assets/device-guidance/air1/images/air1_guide_parts_components.png",
                "image_text": "Air1 核心部件编号清单：编号3=Flange Cover x2；编号11=Quick Start Guide x1。",
            },
            {
                "alt": "Air1 主机按钮与指示灯",
                "module": "guide.controls",
                "url": "/skill-assets/device-guidance/air1/images/air1_guide_controls_button_indicator.png",
                "image_text": "Air1 主机按钮与指示灯编号清单：编号3=Increase Suction Level / 增加吸力键。",
            },
        ]
        context_state.last_displayed_tool_image = context_state.available_tool_images[1]
        context_state.active_device_module = "guide.controls"

        request = build_agent_request(
            {"user_message": "编号3是什么？", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
        )

        self.assertEqual(len(request["input"]), 2)
        content = request["input"][1]["content"]
        self.assertIn("编号3=Increase Suction Level", content[0]["text"])
        self.assertNotIn("编号3=Flange Cover", content[0]["text"])
        self.assertIn("本轮只补充结构化图片文字，没有附带原图", content[0]["text"])
        self.assertFalse(any(part["type"] == "input_image" for part in content))

    def test_final_markdown_image_records_last_displayed_tool_image(self) -> None:
        context_state = ContextState()
        context_state.available_tool_images = [
            {
                "alt": "Air1 主机按钮与指示灯",
                "module": "guide.controls",
                "url": "/skill-assets/device-guidance/air1/images/air1_guide_controls_button_indicator.png",
                "image_text": "Air1 主机按钮与指示灯编号清单：编号3=Increase Suction Level / 增加吸力键。",
            }
        ]
        client = _FakeClient(
            [
                {
                    "id": "resp-final",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "![Air1 主机按钮与指示灯](/skill-assets/device-guidance/air1/images/air1_guide_controls_button_indicator.png)\n先看按钮。",
                                }
                            ],
                        }
                    ],
                }
            ]
        )

        run_agent_loop(
            client,
            {"user_message": "继续", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
            ag_ui_thread_id="thread-1",
            ag_ui_run_id="run-1",
        )

        self.assertEqual(
            context_state.last_displayed_tool_image["url"],
            "/skill-assets/device-guidance/air1/images/air1_guide_controls_button_indicator.png",
        )
        self.assertEqual(context_state.active_device_module, "guide.controls")
        self.assertEqual(
            context_state.shown_step_image_urls,
            ["/skill-assets/device-guidance/air1/images/air1_guide_controls_button_indicator.png"],
        )

        request = build_agent_request(
            {"user_message": "编号3是什么？", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
        )
        self.assertIn("last_displayed_tool_image", request["input"][0]["content"][0]["text"])
        self.assertIn("对照上图", request["input"][0]["content"][0]["text"])

    def test_prior_tool_images_are_not_forwarded_when_user_uploaded_images(self) -> None:
        context_state = ContextState()
        context_state.available_tool_images = [
            {
                "alt": "Air1 乳头测量与法兰选择",
                "url": "/skill-assets/device-guidance/air1/images/air1_guide_flange_measurement.png",
            }
        ]

        request = build_agent_request(
            {
                "user_message": "这张图怎么看？",
                "locale": "zh-CN",
                "images": [{"image_url": "data:image/png;base64,abc", "detail": "auto"}],
            },
            {"context_state": context_state, "loaded_skill_ids": ["device-guidance"]},
        )

        self.assertEqual(len(request["input"]), 1)
        content = request["input"][0]["content"]
        self.assertEqual(len([part for part in content if part["type"] == "input_image"]), 1)

    def test_tool_image_input_item_rejects_non_skill_asset_urls(self) -> None:
        result = {
            "ok": True,
            "tool_name": "device_manual_search",
            "result": {
                "relevant_images": [
                    {"alt": "bad", "url": "https://example.com/air1.png"},
                    {"alt": "bad", "url": "/skill-assets/device-guidance/../SKILL.md"},
                ]
            },
        }

        self.assertEqual(_tool_image_metadata(result), [])
        self.assertIsNone(_tool_image_input_item_from_metadata(_tool_image_metadata(result)))

    def test_birth_journey_intake_guides_quick_replies_after_tool_output(self) -> None:
        async def collect_events() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-intake",
                        "output": [
                            {
                                "type": "function_call",
                                "id": "item-intake",
                                "call_id": "call-intake",
                                "name": "birth_journey_intake_manage",
                                "arguments": json.dumps(
                                    {
                                        "action": "submit_basic_info",
                                        "payload": json.dumps(
                                            {
                                                "nickname": "Henson",
                                                "age": "36",
                                                "current_week": "16周",
                                                "ivf": "不确定/暂不说",
                                                "fetus_count": "单胎",
                                                "first_birth": "是",
                                                "birth_path": "还没确定",
                                                "city_or_country": "深圳",
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "我先把会影响计划的健康和复查信息问清楚。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            session = runtime.get_session("thread-birth-quick")
            session.loaded_skill_ids = ["birth-prep"]
            stream = stream_ag_ui_events(
                {"thread_id": "thread-birth-quick", "run_id": "run-birth-quick"},
                {"user_message": "提交表单", "locale": "zh-CN"},
                runtime,
            )
            events = [event async for event in stream]
            return events, client.responses.requests

        events, requests = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]
        quick_event = next(event for event in events if event.get("type") == "QUICK_REPLIES")

        self.assertEqual(len(requests), 2)
        self.assertIn("tools", requests[1])
        self.assertIn("TEXT_MESSAGE_END", event_types)
        self.assertLess(event_types.index("TEXT_MESSAGE_END"), event_types.index("QUICK_REPLIES"))
        self.assertNotIn("profile_update", json.dumps(events, ensure_ascii=False))
        self.assertEqual(
            quick_event["replies"],
            [
                {"text": "血压/血糖"},
                {"text": "甲状腺/用药"},
                {"text": "暂无异常"},
            ],
        )

    def test_birth_journey_start_keeps_followup_tools_for_basic_info_form(self) -> None:
        async def collect_events() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-intake-start",
                        "output": [
                            {
                                "type": "function_call",
                                "id": "item-intake",
                                "call_id": "call-intake",
                                "name": "birth_journey_intake_manage",
                                "arguments": json.dumps({"action": "start", "payload": {}}),
                            }
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "我先打开孕周与基本情况表单。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            session = runtime.get_session("thread-birth-start")
            session.loaded_skill_ids = ["birth-prep"]
            stream = stream_ag_ui_events(
                {"thread_id": "thread-birth-start", "run_id": "run-birth-start"},
                {"user_message": "好，开始制定", "locale": "zh-CN"},
                runtime,
            )
            events = [event async for event in stream]
            return events, client.responses.requests

        events, requests = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]

        self.assertEqual(len(requests), 2)
        self.assertIn("tools", requests[1])
        self.assertIn("TEXT_MESSAGE_CONTENT", event_types)
        self.assertIn("ARTIFACT_CREATED", event_types)
        self.assertIn("RUN_FINISHED", event_types)
        artifact = next(event for event in events if event.get("type") == "ARTIFACT_CREATED")
        self.assertEqual(artifact["artifact_type"], "form")
        self.assertEqual(artifact["artifact_id"], "birth_journey_basic_info_intake")
        self.assertNotIn("QUICK_REPLIES", event_types)

    def test_birth_journey_ready_auto_generates_plan_without_extra_model_tool_round(self) -> None:
        async def collect_events() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-intake-ready",
                        "output": [
                            {
                                "type": "function_call",
                                "id": "item-intake",
                                "call_id": "call-intake",
                                "name": "birth_journey_intake_manage",
                                "arguments": json.dumps(
                                    {
                                        "action": "skip_checkup_records",
                                        "payload": json.dumps({"checkup_status": "暂时没有产检记录"}, ensure_ascii=False),
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "你的孕期计划已生成，可以在宝宝和我页面查看。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            session = runtime.get_session("thread-birth-auto-plan")
            session.loaded_skill_ids = ["birth-prep"]
            session.context_state.birth_journey_intake = _birth_journey_ready_for_skip_checkup_state()
            stream = stream_ag_ui_events(
                {"thread_id": "thread-birth-auto-plan", "run_id": "run-birth-auto-plan"},
                {"user_message": "先跳过这步", "locale": "zh-CN"},
                runtime,
            )
            events = [event async for event in stream]
            return events, client.responses.requests

        events, requests = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]
        plan_tool_events = [
            event
            for event in events
            if str(event.get("type")).startswith("TOOL_CALL")
            and event.get("tool_call_name") == "birth_journey_plan_card_create"
        ]

        self.assertEqual(len(requests), 2)
        self.assertIn("ARTIFACT_CREATED", event_types)
        self.assertIn("TEXT_MESSAGE_CONTENT", event_types)
        self.assertNotIn("QUICK_REPLIES", event_types)
        self.assertEqual(
            [event.get("type") for event in plan_tool_events],
            ["TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"],
        )
        artifact = next(event for event in events if event.get("type") == "ARTIFACT_CREATED")
        self.assertEqual(artifact["artifact_type"], "birth_journey_plan_card")
        followup_input = requests[1]["input"]
        self.assertEqual(len(followup_input), 1)
        self.assertEqual(followup_input[0]["call_id"], "call-intake")
        model_output = json.loads(str(followup_input[0]["output"]))
        self.assertEqual(model_output["auto_executed_tool"], "birth_journey_plan_card_create")
        self.assertIn("不要再次调用 birth_journey_plan_card_create", model_output["final_response_instruction"])
        self.assertNotIn("下一步必须直接调用 birth_journey_plan_card_create", model_output["final_response_instruction"])

    def test_stream_forwards_text_deltas_during_tool_loop(self) -> None:
        async def collect_events() -> list[dict[str, object]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-intermediate",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "我先整理一下，再继续处理。"}],
                            },
                            {
                                "type": "function_call",
                                "id": "item-profile",
                                "call_id": "call-profile",
                                "name": "profile_update",
                                "arguments": json.dumps(
                                    {
                                        "display_name": "小雨",
                                        "age": None,
                                        "onboarding_skipped": None,
                                    }
                                ),
                            },
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "这是最终回复。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-intermediate"},
                {"user_message": "帮我处理一下", "locale": "zh-CN"},
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        text = "".join(
            str(event.get("delta") or "")
            for event in events
            if event.get("type") == "TEXT_MESSAGE_CONTENT"
        )

        self.assertEqual(text, "我先整理一下，再继续处理。这是最终回复。")

    def test_stream_does_not_append_tool_followup_when_final_text_exists(self) -> None:
        async def collect_events() -> list[dict[str, object]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-card",
                        "output": [
                            {
                                "type": "function_call",
                                "id": "item-card",
                                "call_id": "call-card",
                                "name": "labor_communication_card_create",
                                "arguments": json.dumps({"confirmed_form_data": {}}),
                            }
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "你可以提前和医院确认，并在产检或入院前把这份沟通单给医生/护士看。",
                                    }
                                ],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-followup-no-dup"},
                {
                    "user_message": (
                        "我已确认信息。\n"
                        "form_id: birth_plan_card_intake\n"
                        'confirmed_form_data:\n{"birth_path":"顺产","support_person":"伴侣"}'
                    ),
                    "locale": "zh-CN",
                },
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        text = "".join(
            str(event.get("delta") or "")
            for event in events
            if event.get("type") == "TEXT_MESSAGE_CONTENT"
        )

        self.assertIn("你可以提前和医院确认", text)
        self.assertEqual(text.count("你可以提前和医院确认"), 1)
        self.assertIn("ARTIFACT_CREATED", [event.get("type") for event in events])

    def test_stream_suppresses_quick_replies_when_form_artifact_created(self) -> None:
        async def collect_events() -> list[dict[str, object]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-form",
                        "output": [
                            {
                                "type": "function_call",
                                "id": "item-form",
                                "call_id": "call-form",
                                "name": "birth_plan_form_create",
                                "arguments": json.dumps(
                                    {
                                        "default_values": {
                                            "due_date_or_week": "孕36周",
                                            "birth_path": "顺产",
                                        }
                                    }
                                ),
                            }
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "我先把需要确认的信息准备好了。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-form-no-quick"},
                {"user_message": "帮我做分娩沟通单", "locale": "zh-CN"},
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]

        self.assertIn("ARTIFACT_CREATED", event_types)
        self.assertNotIn("QUICK_REPLIES", event_types)
        self.assertIn("RUN_FINISHED", event_types)
        artifact = next(event for event in events if event.get("type") == "ARTIFACT_CREATED")
        self.assertEqual(artifact["artifact_type"], "form")

    def test_stream_suppresses_quick_replies_when_support_ticket_form_created(self) -> None:
        async def collect_events() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-ticket",
                        "output": [
                            {
                                "type": "function_call",
                                "id": "item-ticket",
                                "call_id": "call-ticket",
                                "name": "support_ticket_draft_create",
                                "arguments": json.dumps(
                                    {
                                        "issue_type": "malfunction",
                                        "issue_summary": "吸奶器无法启动",
                                        "product_model": "Air1",
                                        "user_confirmed": True,
                                    }
                                ),
                            }
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "这件事确实很让人着急。\n\n我已经帮你把售后信息整理好了，你可以看一下有没有需要补充或修改的地方。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-ticket-no-quick"},
                {"user_message": "帮我建售后工单", "locale": "zh-CN"},
                runtime,
            )
            events = [event async for event in stream]
            return events, client.responses.requests

        events, requests = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]

        self.assertIn("ARTIFACT_CREATED", event_types)
        self.assertNotIn("QUICK_REPLIES", event_types)
        self.assertIn("RUN_FINISHED", event_types)
        self.assertGreaterEqual(len(requests), 2)
        followup_input = requests[1]["input"]
        self.assertEqual(len(followup_input), 1)
        model_output = json.loads(str(followup_input[0]["output"]))
        self.assertNotIn("assistant_followup", model_output)
        self.assertNotIn("assistant_followup.message", model_output["final_response_instruction"])
        self.assertIn("建议内容", model_output["final_response_instruction"])
        self.assertIn("确实很让人着急", model_output["final_response_instruction"])
        self.assertIn("不要提“草稿”“未提交”“确认后才提交”", model_output["final_response_instruction"])
        self.assertIn("交付信息只能出现一次", model_output["final_response_instruction"])
        self.assertIn("不要列举购买渠道、照片、视频、联系方式", model_output["final_response_instruction"])
        tool_result_event = next(event for event in events if event.get("type") == "TOOL_CALL_RESULT")
        tool_result_payload = json.loads(str(tool_result_event.get("content") or "{}"))
        self.assertNotIn("assistant_followup", tool_result_payload)
        artifact = next(event for event in events if event.get("type") == "ARTIFACT_CREATED")
        self.assertEqual(artifact["artifact_type"], "support_ticket")
        self.assertEqual(artifact["semantic"]["label"], "请确认售后信息")
        text = "".join(
            str(event.get("delta") or "")
            for event in events
            if event.get("type") == "TEXT_MESSAGE_CONTENT"
        )
        self.assertIn("确实很让人着急", text)
        self.assertIn("我已经帮你把售后信息整理好了", text)
        self.assertEqual(text.count("我已经帮你把售后信息整理好了"), 1)
        self.assertNotIn("设备还是没法正常使用", text)
        self.assertNotIn("售后工单草稿", text)

    def test_stream_executes_pseudo_tool_use_text_without_leaking_markup(self) -> None:
        form_data = {
            "due_date_or_week": "20周",
            "first_birth": "是",
            "fetus_count": "双胎",
            "pregnancy_history_or_notes": ["妊娠糖尿病"],
            "birth_path": "剖宫产",
            "feeding_intention": "亲喂母乳",
            "return_to_work_timing": "6周后",
            "support_person": "伴侣",
            "top_worries": ["怕漏买"],
        }
        pseudo_tool_text = (
            '<tool_use>{"recipient_name":"birth_prep.hospital_bag_card_create","parameters":{}}</tool_use>'
            '<tool_use>{"recipient_name":"functions.profile_update","parameters":{"display_name":"小雨","age":null,"onboarding_skipped":null}}</tool_use>'
            "表单我看到了，但这次卡片生成没有接上。"
        )

        async def collect_events() -> list[dict[str, object]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-pseudo-tool",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": pseudo_tool_text}],
                            }
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "待产包清单我整理好了。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-pseudo-tool"},
                {
                    "user_message": (
                        "我已确认待产包信息。\n"
                        "form_id: hospital_bag_intake\n"
                        "confirmed_form_data:\n"
                        f"{json.dumps(form_data, ensure_ascii=False)}"
                    ),
                    "locale": "zh-CN",
                },
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        text = "".join(
            str(event.get("delta") or "")
            for event in events
            if event.get("type") == "TEXT_MESSAGE_CONTENT"
        )
        event_types = [str(event.get("type")) for event in events]

        self.assertIn("ARTIFACT_CREATED", event_types)
        artifact = next(event for event in events if event.get("type") == "ARTIFACT_CREATED")
        self.assertEqual(artifact["artifact_type"], "hospital_bag_card")
        self.assertIn("待产包清单我整理好了", text)
        self.assertNotIn("<tool_use>", text)
        self.assertNotIn("recipient_name", text)
        self.assertNotIn("表单我看到了", text)

    def test_stream_strips_pseudo_tool_call_markup_but_keeps_reply_text(self) -> None:
        pseudo_tool_text = (
            '<tool_call>profile_update {"display_name":"小雨","age":null,"onboarding_skipped":null}</tool_call>'
            "好，那这个下降趋势就更值得认真看一下。宝宝近 24 小时尿量或尿布情况大概怎么样？"
        )

        async def collect_events() -> list[dict[str, object]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-pseudo-tool-call",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": pseudo_tool_text}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-pseudo-tool-call"},
                {"user_message": "记录是完整的", "locale": "zh-CN"},
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        text = "".join(
            str(event.get("delta") or "")
            for event in events
            if event.get("type") == "TEXT_MESSAGE_CONTENT"
        )

        self.assertIn("好，那这个下降趋势", text)
        self.assertIn("尿量或尿布情况", text)
        self.assertNotIn("<tool_call>", text)
        self.assertNotIn("profile_update", text)


def _birth_journey_ready_for_skip_checkup_state() -> dict[str, object]:
    return {
        "started": True,
        "basic_info": {
            "current_week": "30周",
            "age": "32",
            "ivf": "不确定/暂不说",
            "fetus_count": "单胎",
            "first_birth": "不确定/暂不说",
            "birth_path": "还没确定",
            "city_or_country": "深圳",
        },
        "final_plan_confirmed": True,
        "checkup_records_uploaded": False,
        "checkup_status": "未上传产检记录",
        "next_step": "checkup_records_upload",
        "completed_groups": ["basic_info"],
    }


class _FakeClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = _FakeResponses(responses)


class _MutatingClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = _MutatingResponses(responses)


class _FakeResponses:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def create(self, **request: object) -> dict[str, object]:
        self.requests.append(request)
        if not self._responses:
            return {"id": "resp-empty", "output": []}
        return self._responses.pop(0)


class _MutatingResponses(_FakeResponses):
    def __init__(self, responses: list[dict[str, object]]) -> None:
        super().__init__(responses)
        self.on_create = lambda: None

    def create(self, **request: object) -> dict[str, object]:
        self.requests.append(request)
        self.on_create()
        if not self._responses:
            return {"id": "resp-empty", "output": []}
        return self._responses.pop(0)


class _FakeStreamingClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = _FakeStreamingResponses(responses)


class _FakeStreamingResponses:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def create(self, **request: object) -> object:
        self.requests.append(request)
        if not self._responses:
            response = {"id": "resp-empty", "output": []}
        else:
            response = self._responses.pop(0)
        if request.get("stream"):
            return _response_stream_events(response)
        return response


def _response_stream_events(response: dict[str, object]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for output_index, item in enumerate(response.get("output", [])):  # type: ignore[union-attr]
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            events.append({"type": "response.output_item.added", "output_index": output_index, "item": item})
            events.append({"type": "response.web_search_call.searching", "output_index": output_index, "item": item})
            done_item = {**item, "status": item.get("status") or "completed"}
            events.append({"type": "response.output_item.done", "output_index": output_index, "item": done_item})
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                events.append({"type": "response.output_text.delta", "delta": part["text"]})
    events.append({"type": "response.completed", "response": response})
    return events


if __name__ == "__main__":
    unittest.main()
