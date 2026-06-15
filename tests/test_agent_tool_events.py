from __future__ import annotations

import json
import unittest
import asyncio

from momcozy_agent import ContextState, build_agent_request
from momcozy_agent.tool_schemas import FUNCTION_TOOLS
from momcozy_agent.agents import (
    artifact_created_event,
    _tool_image_input_item_from_metadata,
    _tool_image_metadata,
    model_tool_output,
    run_agent_loop,
    tool_call_start_event,
    tool_call_args_event,
    tool_call_end_event,
    tool_call_result_event,
    web_search_status_event,
)
from momcozy_agent.server import ChatRuntime, create_app, stream_ag_ui_events


class AgentToolEventTests(unittest.TestCase):
    def test_agent_request_defaults_to_concise_reply_style(self) -> None:
        request = build_agent_request({"user_message": "奶量够不够", "locale": "zh-CN"})

        self.assertEqual(request["text"]["verbosity"], "low")
        self.assertIn("默认回复要短", request["instructions"])
        self.assertIn("优先 1-3 句", request["instructions"])
        self.assertIn("已经展示的信息不要再完整复述", request["instructions"])
        self.assertIn("不要先输出用户可见的过渡说明或中间解释", request["instructions"])
        self.assertIn("Agent loop 过程中的中间判断、准备动作和工具选择不要写进正文", request["instructions"])
        self.assertIn("使用 `ui_quick_replies_create` 创建", request["instructions"])

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
        self.assertIn("要主动引导 IBCLC 在线咨询", request_context)
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

    def test_milk_management_fullness_without_red_flags_does_not_force_health_search(self) -> None:
        request = build_agent_request(
            {"user_message": "没有红肿发热，就是吸完还胀", "locale": "zh-CN"},
            {"loaded_skill_ids": ["milk-management"]},
        )

        self.assertEqual(request["tool_choice"], "auto")
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("loaded_skill_context:", request_context)
        self.assertNotIn("health_guidance_context:", request_context)

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

    def test_tool_call_phase_events_include_tool_name(self) -> None:
        args_event = tool_call_args_event(
            "call-1",
            "milk_plan_preview",
            {"plan_type": "increase_milk"},
            response_id="resp-1",
            output_index=0,
            item_id="item-1",
        )
        end_event = tool_call_end_event(
            "call-1",
            "milk_plan_preview",
            response_id="resp-1",
            output_index=0,
            item_id="item-1",
        )
        result_event = tool_call_result_event(
            "message-1",
            "call-1",
            "milk_plan_preview",
            {"ok": True, "tool_name": "milk_plan_preview", "result": {"status": "plan_preview_ready"}},
            response_id="resp-1",
            output_index=0,
            item_id="item-1",
        )

        self.assertEqual(args_event["tool_call_name"], "milk_plan_preview")
        self.assertEqual(end_event["tool_call_name"], "milk_plan_preview")
        self.assertEqual(result_event["tool_call_name"], "milk_plan_preview")
        self.assertEqual(json.loads(result_event["content"])["tool_name"], "milk_plan_preview")
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
            tool_call_name="milk_assessment_evaluate",
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
        self.assertIn("主动说明更适合让 IBCLC 顾问继续看", FUNCTION_TOOLS["ibclc_consult_card_create"]["description"])
        self.assertIn("售后工单信息表已经展示", support_ticket["final_response_instruction"])
        self.assertIn("不要提“草稿”“未提交”“确认后才提交”", support_ticket["final_response_instruction"])
        self.assertIn("结合当前问题场景做情绪承接", support_ticket["final_response_instruction"])
        self.assertIn("参考 assistant_followup.message", support_ticket["final_response_instruction"])
        self.assertIn("最多两段", support_ticket["final_response_instruction"])
        self.assertIn("交付信息只能出现一次", support_ticket["final_response_instruction"])
        self.assertIn("不要列举购买渠道、照片、视频、联系方式", support_ticket["final_response_instruction"])

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
        self.assertEqual(compact["assistant_followup"], {"message": "卡片已经生成好了。"})
        self.assertEqual(compact["card"], {"card_type": "birth_plan_card", "schema_version": "1.0", "created": True})
        self.assertIn("参考 assistant_followup.message", compact["final_response_instruction"])
        self.assertIn("自然表达", compact["final_response_instruction"])
        self.assertNotIn("card_json", json.dumps(compact, ensure_ascii=False))

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

    def test_quick_replies_tool_streams_ui_event_after_text_end(self) -> None:
        async def collect_events() -> list[dict[str, object]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-quick",
                        "output": [
                            {
                                "type": "function_call",
                                "id": "item-quick",
                                "call_id": "call-quick",
                                "name": "ui_quick_replies_create",
                                "arguments": json.dumps(
                                    {
                                        "replies": [
                                            {"text": "继续下一步", "send_text": "继续下一步"},
                                            {"text": "换个方案", "send_text": "我想换个方案"},
                                            {"text": "先帮我总结", "send_text": "先帮我总结"},
                                        ]
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
                                "content": [{"type": "output_text", "text": "我们先从最关键的一步开始。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-quick"},
                {"user_message": "我该怎么办", "locale": "zh-CN"},
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]

        self.assertIn("TEXT_MESSAGE_CONTENT", event_types)
        self.assertIn("TEXT_MESSAGE_END", event_types)
        self.assertIn("QUICK_REPLIES", event_types)
        self.assertIn("RUN_FINISHED", event_types)
        self.assertLess(event_types.index("TEXT_MESSAGE_END"), event_types.index("QUICK_REPLIES"))
        self.assertLess(event_types.index("QUICK_REPLIES"), event_types.index("RUN_FINISHED"))
        self.assertFalse(
            any(
                event.get("tool_call_name") == "ui_quick_replies_create"
                for event in events
                if str(event.get("type")).startswith("TOOL_CALL")
            )
        )

        quick_event = next(event for event in events if event.get("type") == "QUICK_REPLIES")
        self.assertEqual(quick_event["message_id"], "run-quick:assistant")
        self.assertEqual(
            quick_event["replies"],
            [
                {"text": "继续下一步", "send_text": "继续下一步"},
                {"text": "换个方案", "send_text": "我想换个方案"},
                {"text": "先帮我总结", "send_text": "先帮我总结"},
            ],
        )

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
                                "id": "item-quick",
                                "call_id": "call-quick",
                                "name": "ui_quick_replies_create",
                                "arguments": json.dumps(
                                    {
                                        "replies": [
                                            {"text": "继续", "send_text": "继续"},
                                        ]
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

    def test_stream_adds_default_quick_replies_when_model_omits_tool(self) -> None:
        async def collect_events() -> list[dict[str, object]]:
            client = _FakeStreamingClient(
                [
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "我们先从最关键的一步开始。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-default-quick"},
                {"user_message": "我该怎么办", "locale": "zh-CN"},
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]

        self.assertIn("TEXT_MESSAGE_END", event_types)
        self.assertIn("QUICK_REPLIES", event_types)
        self.assertIn("RUN_FINISHED", event_types)
        self.assertLess(event_types.index("TEXT_MESSAGE_END"), event_types.index("QUICK_REPLIES"))
        self.assertLess(event_types.index("QUICK_REPLIES"), event_types.index("RUN_FINISHED"))

        quick_event = next(event for event in events if event.get("type") == "QUICK_REPLIES")
        self.assertEqual(quick_event["message_id"], "run-default-quick:assistant")
        self.assertEqual(
            quick_event["replies"],
            [
                {"text": "继续这个问题", "send_text": "继续这个问题"},
                {"text": "换个说法", "send_text": "请换个说法再解释一遍"},
                {"text": "我想问别的", "send_text": "我想问另一个问题"},
            ],
        )

    def test_stream_adds_default_quick_replies_even_without_text_message(self) -> None:
        async def collect_events() -> list[dict[str, object]]:
            client = _FakeStreamingClient([{"id": "resp-empty", "output": []}])
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-empty-quick"},
                {"user_message": "打开表单", "locale": "zh-CN"},
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]

        self.assertNotIn("TEXT_MESSAGE_END", event_types)
        self.assertIn("QUICK_REPLIES", event_types)
        self.assertIn("RUN_FINISHED", event_types)
        self.assertLess(event_types.index("QUICK_REPLIES"), event_types.index("RUN_FINISHED"))

        quick_event = next(event for event in events if event.get("type") == "QUICK_REPLIES")
        self.assertEqual(quick_event["message_id"], "run-empty-quick:assistant")
        self.assertEqual(len(quick_event["replies"]), 3)

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

    def test_stream_suppresses_default_quick_replies_when_support_ticket_form_created(self) -> None:
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
        self.assertIn("assistant_followup", model_output)
        self.assertIn("参考 assistant_followup.message", model_output["final_response_instruction"])
        self.assertIn("不要提“草稿”“未提交”“确认后才提交”", model_output["final_response_instruction"])
        self.assertIn("交付信息只能出现一次", model_output["final_response_instruction"])
        self.assertIn("不要列举购买渠道、照片、视频、联系方式", model_output["final_response_instruction"])
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

    def test_stream_keeps_quick_replies_when_card_artifact_created(self) -> None:
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
                                "name": "ibclc_consult_card_create",
                                "arguments": json.dumps({}),
                            }
                        ],
                    },
                    {
                        "id": "resp-final",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "IBCLC 咨询入口我准备好了。"}],
                            }
                        ],
                    },
                ]
            )
            runtime = ChatRuntime(client, model="test-model")
            stream = stream_ag_ui_events(
                {"thread_id": "thread-1", "run_id": "run-card-quick"},
                {"user_message": "我想找 IBCLC", "locale": "zh-CN"},
                runtime,
            )
            return [event async for event in stream]

        events = asyncio.run(collect_events())
        event_types = [str(event.get("type")) for event in events]

        self.assertIn("ARTIFACT_CREATED", event_types)
        self.assertIn("QUICK_REPLIES", event_types)
        self.assertIn("RUN_FINISHED", event_types)
        self.assertLess(event_types.index("QUICK_REPLIES"), event_types.index("RUN_FINISHED"))
        artifact = next(event for event in events if event.get("type") == "ARTIFACT_CREATED")
        self.assertEqual(artifact["artifact_type"], "ibclc_consult_card")
        quick_event = next(event for event in events if event.get("type") == "QUICK_REPLIES")
        self.assertEqual(quick_event["message_id"], "run-card-quick:assistant")
        self.assertEqual(len(quick_event["replies"]), 3)


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
