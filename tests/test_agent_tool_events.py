from __future__ import annotations

import json
import unittest

from momcozy_agent import ContextState, build_agent_request
from momcozy_agent.agents import (
    model_tool_output,
    run_agent_loop,
    tool_call_args_event,
    tool_call_end_event,
    tool_call_result_event,
)


class AgentToolEventTests(unittest.TestCase):
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
        confirmation = next(event for event in events if event.get("type") == "CONFIRMATION_REQUIRED")
        self.assertEqual(confirmation["title"], "请确认售后工单")

    def test_model_tool_output_compacts_artifact_payloads(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "birth_plan_card_create",
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
                                    "skill_id": "birth-prep",
                                    "kind": "references",
                                    "path": "references/birth-plan-card.md",
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
            {"user_message": "帮我做分娩沟通卡", "locale": "zh-CN"},
            {"context_state": context_state, "loaded_skill_ids": ["birth-prep"]},
            ag_ui_thread_id="thread-1",
            ag_ui_run_id="run-1",
        )

        self.assertTrue(any("birth-prep/references/birth-plan-card.md 已在当前会话中读取过" in item for item in context_state.loaded_references))

        request = build_agent_request(
            {"user_message": "继续", "locale": "zh-CN", "previous_response_id": "resp-final"},
            {"context_state": context_state, "loaded_skill_ids": ["birth-prep"]},
        )
        request_context = request["input"][0]["content"][0]["text"]
        self.assertIn("loaded_skill_context:", request_context)
        self.assertIn("loaded_reference_context:", request_context)
        self.assertIn("不要重复调用 load_skill", request_context)
        self.assertIn("不要重复调用 read_skill_file", request_context)


class _FakeClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = _FakeResponses(responses)


class _FakeResponses:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)

    def create(self, **request: object) -> dict[str, object]:
        _ = request
        if not self._responses:
            return {"id": "resp-empty", "output": []}
        return self._responses.pop(0)


if __name__ == "__main__":
    unittest.main()
