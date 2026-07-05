from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from momcozy_agent.agents import model_tool_output, safe_tool_result
from momcozy_agent.contexts import build_request_context
from momcozy_agent.services import data_store
from momcozy_agent.tool_handlers.cards import (
    BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS,
    BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS,
    birth_journey_intake_quick_reply_guidance,
    create_birth_journey_plan_card,
    delete_birth_journey_plan,
    manage_birth_journey_intake,
    update_birth_journey_plan_todo,
    update_birth_journey_plan_todo_completion_for_user,
)
from momcozy_agent.tool_registry import select_runtime_tools


def _plan_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "due_date_or_week": "30周",
        "birth_path": "还没确定",
        "support_person": "暂时没有",
        "ivf": "跳过",
        "first_birth": "跳过",
        "fetus_count": "跳过",
        "age": "跳过",
        "city_or_country": "跳过",
        "checkup_status": "跳过",
        "current_symptoms": "跳过",
        "risk_factors": "跳过",
        "lifestyle_context": "跳过",
        "feeding_ibclc_context": "跳过",
    }
    context.update(overrides)
    return context


def _birth_journey_todo_periods(card: dict[str, object]) -> list[dict[str, object]]:
    todo_plan = card.get("todo_plan") if isinstance(card.get("todo_plan"), dict) else {}
    periods = todo_plan.get("periods") if isinstance(todo_plan.get("periods"), list) else []
    return [period for period in periods if isinstance(period, dict)]


def _birth_journey_todo_items(card: dict[str, object]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for period in _birth_journey_todo_periods(card):
        period_items = period.get("items") if isinstance(period.get("items"), list) else []
        items.extend(item for item in period_items if isinstance(item, dict))
    return items


def _birth_journey_first_period_items(card: dict[str, object]) -> list[dict[str, object]]:
    periods = _birth_journey_todo_periods(card)
    if not periods:
        return []
    items = periods[0].get("items") if isinstance(periods[0].get("items"), list) else []
    return [item for item in items if isinstance(item, dict)]


def _find_birth_journey_todo_item(card: dict[str, object], item_id: str) -> dict[str, object] | None:
    for item in _birth_journey_todo_items(card):
        if str(item.get("id") or "") == item_id:
            return item
    return None


def _assert_birth_journey_item_text_lengths(testcase: unittest.TestCase, card: dict[str, object]) -> None:
    items = _birth_journey_todo_items(card)
    testcase.assertTrue(items)
    for item in items:
        title = str(item.get("title") or "")
        reason = str(item.get("reason") or "")
        plan_reason = str(item.get("plan_reason") or "")
        testcase.assertLessEqual(len(title), BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS)
        testcase.assertLessEqual(len(reason), BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS)
        testcase.assertLessEqual(len(plan_reason), BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS)


def _assert_birth_journey_item_reasons_use_current_contract(testcase: unittest.TestCase, card: dict[str, object]) -> None:
    blocked_fragments = ("目的是", "【重要】", "【建议】")
    items = _birth_journey_todo_items(card)
    testcase.assertTrue(items)
    for item in items:
        testcase.assertIn(item.get("priority_label"), {"重要", "建议"})
        testcase.assertTrue(str(item.get("plan_reason") or ""))
        if item.get("hide_reason"):
            testcase.assertFalse(str(item.get("reason") or ""))
            testcase.assertFalse(str(item.get("why_for_you") or ""))
        for key in ("reason", "why_for_you", "plan_reason"):
            text = str(item.get(key) or "")
            testcase.assertNotIn("考虑到孕", text)
            for fragment in blocked_fragments:
                testcase.assertNotIn(fragment, text)


def _assert_birth_journey_item_titles_are_plain(testcase: unittest.TestCase, card: dict[str, object]) -> None:
    rendered = json.dumps(card, ensure_ascii=False)
    blocked_title_fragments = (
        '"title": "把未完成产检项排进计划"',
        '"title": "建立胎动和异常联系机制"',
        '"title": "把高龄监测纳入产检节奏"',
        '"title": "把医生提醒落到观察日程"',
        '"title": "准备剖宫产史评估资料"',
        '"title": "完成剖宫产术前准备路径"',
        '"title": "完成B超安排和回看闭环"',
        '"title": "完成GBS和入院材料收口"',
        '"title": "设置临产出发方案"',
        '"title": "完成生产医院入院流程确认"',
    )
    for fragment in blocked_title_fragments:
        testcase.assertNotIn(fragment, rendered)


def _assert_current_todo_items(testcase: unittest.TestCase, card: dict[str, object]) -> None:
    items = _birth_journey_first_period_items(card)
    testcase.assertTrue(items)
    weak_title_fragments = (
        "确认高龄孕期关注重点",
        "补齐下次产检时间",
        "给生活压力留缓冲",
        "确认高龄孕期监测安排",
        "确认血压血糖监测安排",
        "问清高龄孕期",
        "从产检报告圈出",
        "补问剖宫产",
        "机制",
        "闭环",
        "收口",
        "纳入",
        "排进计划",
        "观察日程",
        "评估资料",
    )
    for item in items:
        testcase.assertTrue(str(item.get("id") or "").strip())
        testcase.assertIs(item.get("completed"), False)
        testcase.assertIsNone(item.get("completed_at"))
        testcase.assertIsNone(item.get("completed_source"))
        testcase.assertIn(item.get("priority_type"), {"essential", "supportive"})
        testcase.assertIn(item.get("priority_label"), {"重要", "建议"})
        testcase.assertFalse(str(item.get("title") or "").startswith(("【重要】", "【建议】")))
        testcase.assertNotIn("done_criteria", item)
        for fragment in weak_title_fragments:
            testcase.assertNotIn(fragment, str(item.get("title") or ""))
        testcase.assertIsInstance(item.get("steps"), list)
        testcase.assertTrue(item.get("steps"))
        testcase.assertTrue(item.get("after_done_value"))
        testcase.assertIn("做完后", str(item.get("after_done_value") or ""))
        testcase.assertTrue(item.get("completion_followup"))


def _assert_no_birth_journey_done_criteria(testcase: unittest.TestCase, value: object) -> None:
    if isinstance(value, dict):
        testcase.assertNotIn("done_criteria", value)
        for child in value.values():
            _assert_no_birth_journey_done_criteria(testcase, child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_birth_journey_done_criteria(testcase, child)


def _assert_no_legacy_birth_journey_fallback_items(testcase: unittest.TestCase, card: dict[str, object]) -> None:
    rendered = json.dumps(card, ensure_ascii=False)
    legacy_fragments = (
        "完成建档、NT和早筛闭环",
        "确认中期筛查选择",
        "完成大排畸并问清复查",
        "完成糖耐并记录复查结果",
        "固定胎动和血压观察",
        "确认胎位、生长和入院流程",
        "问清GBS筛查和临产联系",
        "按每周产检收口入院清单",
        "按多胎节奏确认监测",
        "记录孕反、饮食和补剂执行",
        "安排运动、加餐和补铁补钙提醒",
        "记录胎动和晚孕身体变化",
        "补充孕周后换算检查窗口",
        "整理产检节奏和医院要求",
        "产后 0-42 天：保留恢复支持",
        "写下医生提醒原话",
        "写下风险因素原话",
        "写下上一胎分娩方式",
        "写下已经做完的检查",
        "问清高龄孕期 3 个监测重点",
        "补问剖宫产术前术后 4 件事",
        "从产检信息圈出 3 个待确认点",
        "看完大排畸后问清复查",
        "确认GBS筛查和待产证件",
        "固定胎动记录和异常联系规则",
        "整理既往剖宫产 3 个信息",
        "确认基础疾病和用药复查",
        "把未完成产检项排进计划",
        "建立胎动和异常联系机制",
        "把高龄监测纳入产检节奏",
        "把医生提醒落到观察日程",
        "准备剖宫产史评估资料",
        "完成剖宫产术前准备路径",
        "完成B超安排和回看闭环",
        "完成GBS和入院材料收口",
        "设置临产出发方案",
    )
    for fragment in legacy_fragments:
        testcase.assertNotIn(fragment, rendered)


def _answer_birth_journey_personalized_followups(
    inputs: dict[str, object],
    current: dict[str, object],
    answers: dict[str, str] | None = None,
) -> dict[str, object]:
    answers = answers or {}
    while current.get("next_step") == "personalized_followup":
        data = current.get("data") if isinstance(current.get("data"), dict) else {}
        followup = _birth_journey_suggested_topics(current)[0]
        followup_id = str(followup.get("id") or "").strip()
        current = manage_birth_journey_intake(
            {
                "action": "submit_personalized_followup",
                "payload": {
                    "topic": followup_id,
                    "question": followup.get("followup_question"),
                    "answer": answers.get(followup_id, "还不确定"),
                    "plan_impact": followup.get("meaning"),
                },
            },
            {**inputs, "_birth_journey_intake_state": current["intake_state"]},
        )
    return current


def _birth_journey_personalization_context(result: dict[str, object]) -> dict[str, object]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    context = data.get("personalization_context") if isinstance(data.get("personalization_context"), dict) else {}
    return context


def _birth_journey_suggested_topics(result: dict[str, object]) -> list[dict[str, object]]:
    context = _birth_journey_personalization_context(result)
    topics = context.get("suggested_topics") if isinstance(context.get("suggested_topics"), list) else []
    return [topic for topic in topics if isinstance(topic, dict)]


def _birth_journey_suggested_topic(result: dict[str, object], topic_id: str) -> dict[str, object]:
    for topic in _birth_journey_suggested_topics(result):
        if topic.get("id") == topic_id:
            return topic
    return {}


def _confirm_birth_journey_ready_to_generate(
    inputs: dict[str, object],
    current: dict[str, object],
    *,
    additional_info: str | None = None,
) -> dict[str, object]:
    if additional_info is None:
        action = "confirm_ready_to_generate"
        payload: dict[str, object] = {}
    else:
        action = "submit_final_additional_info"
        payload = {"final_additional_info": additional_info}
    return manage_birth_journey_intake(
        {"action": action, "payload": payload},
        {**inputs, "_birth_journey_intake_state": current["intake_state"]},
    )


def _basic_info_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "current_week": "20周",
        "ivf": "不确定/暂不说",
        "fetus_count": "单胎",
        "age": "31",
        "first_birth": "不确定/暂不说",
        "birth_path": "还没确定",
    }
    payload.update(overrides)
    return payload


def _assert_birth_journey_todo_plan(
    testcase: unittest.TestCase,
    card: dict[str, object],
    *,
    cadence: str,
    first_period_title: str,
) -> None:
    todo_plan = card.get("todo_plan")
    testcase.assertIsInstance(todo_plan, dict)
    if not isinstance(todo_plan, dict):
        return
    testcase.assertEqual(todo_plan.get("cadence"), cadence)
    testcase.assertTrue(str(todo_plan.get("route_summary") or ""))
    periods = todo_plan.get("periods")
    testcase.assertIsInstance(periods, list)
    testcase.assertTrue(periods)
    first_period = periods[0]
    testcase.assertIsInstance(first_period, dict)
    if not isinstance(first_period, dict):
        return
    testcase.assertEqual(first_period.get("title"), first_period_title)
    testcase.assertEqual(first_period.get("granularity"), cadence)
    testcase.assertEqual(first_period.get("display_mode"), "expanded")
    testcase.assertEqual(first_period.get("status"), "current")
    items = first_period.get("items")
    testcase.assertIsInstance(items, list)
    testcase.assertTrue(items)
    terminal_period = periods[-1]
    testcase.assertIsInstance(terminal_period, dict)
    if isinstance(terminal_period, dict):
        testcase.assertEqual(terminal_period.get("title"), "临产与住院生产")
        testcase.assertEqual(terminal_period.get("granularity"), "terminal")
        testcase.assertEqual(terminal_period.get("display_mode"), "terminal")
        testcase.assertEqual(terminal_period.get("status"), "terminal")
        terminal_titles = {str(item.get("title") or "") for item in terminal_period.get("items") or [] if isinstance(item, dict)}
        testcase.assertIn("做临产入院准备与联系流程", terminal_titles)
    blocked_fragments = ("目的是", "【重要】", "【建议】")
    for period in periods:
        testcase.assertIsInstance(period, dict)
        testcase.assertTrue(period.get("title"))
        testcase.assertTrue(period.get("subtitle"))
        for item in period.get("items") or []:
            testcase.assertIsInstance(item, dict)
            testcase.assertNotIn("done_criteria", item)
            title = str(item.get("title") or "")
            reason = str(item.get("reason") or "")
            plan_reason = str(item.get("plan_reason") or "")
            testcase.assertLessEqual(len(title), BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS)
            testcase.assertLessEqual(len(reason), BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS)
            testcase.assertLessEqual(len(plan_reason), BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS)
            testcase.assertIn(item.get("priority_label"), {"重要", "建议"})
            if item.get("hide_reason"):
                testcase.assertFalse(str(item.get("reason") or ""))
                testcase.assertFalse(str(item.get("why_for_you") or ""))
            else:
                testcase.assertTrue(str(item.get("reason") or "").startswith(("重要｜", "建议｜")))
                testcase.assertTrue(str(item.get("why_for_you") or ""))
            testcase.assertTrue(plan_reason)
            testcase.assertNotIn("考虑到孕", plan_reason)
            testcase.assertIsInstance(item.get("steps"), list)
            testcase.assertTrue(item.get("steps"))
            testcase.assertGreaterEqual(len(item.get("steps") or []), 4)
            testcase.assertTrue(item.get("source_tags"))
            rendered_steps = json.dumps(item.get("steps") or [], ensure_ascii=False)
            testcase.assertNotIn("明确这项下一步要做什么", rendered_steps)
            testcase.assertNotIn("安排执行时间或确认对象", rendered_steps)
            testcase.assertNotIn("把需要的材料或联系入口放好", rendered_steps)
            for fragment in blocked_fragments:
                testcase.assertNotIn(fragment, reason)
    _assert_no_birth_journey_done_criteria(testcase, todo_plan)


class BirthJourneyPlanCardTests(unittest.TestCase):
    def test_creates_early_pregnancy_phase_for_newly_pregnant_users(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="5周",
                    birth_path="还没确定",
                    support_person="伴侣",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-02T09:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        self.assertEqual(card["owner"]["current_week"], "孕5周")
        phases = card["phases"]
        self.assertEqual([phase["title"] for phase in phases], ["孕早期", "孕中期", "孕晚期", "临产阶段", "住院分娩", "产后恢复"])
        self.assertEqual(phases[0]["status"], "current")
        self.assertIn("约 2026/06/02", phases[0]["date_range"])
        self.assertIn("首次产检", phases[0]["goal"])
        self.assertEqual(phases[-1]["title"], "产后恢复")

    def test_creates_structured_birth_journey_plan_card_from_week_context(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="25周",
                    first_birth="是",
                    birth_path="剖宫产",
                    feeding_intention="母乳",
                    feeding_ibclc_context="计划母乳，想了解产后支持",
                    checkup_status="已做 NT、NIPT、大排畸，结果正常，还没预约糖耐",
                    current_symptoms="最近久坐上班，腰酸，晚上腿抽筋",
                    risk_factors="孕前 BMI 28",
                    lifestyle_context="久坐上班，晚上腿抽筋",
                    support_person="伴侣",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-05-31T09:00:00+08:00"},
        )

        self.assertEqual(result["tool_name"], "birth_journey_plan_card_create")
        self.assertEqual(result["card"]["card_type"], "birth_journey_plan_card")
        self.assertEqual(result["card"]["schema_version"], "1.0")
        card = result["card"]["card_json"]
        self.assertEqual(card["title"], "孕期计划")
        self.assertEqual(card["owner"]["current_week"], "孕25周")
        self.assertEqual(card["owner"]["estimated_due_date"], "2026/09/13")
        self.assertNotIn("planning_layers", card)
        self.assertEqual(card["todo_plan"]["current_week"], 25)
        self.assertIn("你现在孕 25 周", card["generation_context"]["personalization_basis"])
        current_rendered = json.dumps(_birth_journey_first_period_items(card), ensure_ascii=False)
        self.assertIn("把没做完的产检安排上", current_rendered)
        self.assertIn("做糖耐检查（OGTT）", current_rendered)
        self.assertIn("75g葡萄糖水", current_rendered)
        self.assertIn("按1小时/2小时", current_rendered)
        self.assertIn("剖宫产/分娩准备资料", current_rendered)
        self.assertIn("做完后", current_rendered)
        self.assertNotIn("给生活压力留缓冲", current_rendered)
        self.assertNotIn("补齐下次产检时间", current_rendered)
        self.assertNotIn("目的是", current_rendered)
        _assert_current_todo_items(self, card)
        self.assertGreaterEqual(len(card["todo_plan"]["periods"]), 2)
        _assert_birth_journey_item_text_lengths(self, card)
        _assert_birth_journey_todo_plan(self, card, cadence="monthly", first_period_title="孕 25-27 周")
        todo_rendered = json.dumps(card["todo_plan"], ensure_ascii=False)
        self.assertIn("按月计划", todo_rendered)
        self.assertIn("做糖耐检查（OGTT）", todo_rendered)
        ogtt_items = [
            item
            for period in card["todo_plan"]["periods"]
            for item in period.get("items") or []
            if isinstance(item, dict) and item.get("id") == "week_24_28_gtt"
        ]
        self.assertTrue(ogtt_items)
        self.assertEqual(len(ogtt_items[0].get("steps") or []), 5)
        self.assertIn("检查结束后进食并观察身体反应", json.dumps(ogtt_items[0].get("steps"), ensure_ascii=False))
        checkup_item = _find_birth_journey_todo_item(card, "condition_checkup_status")
        self.assertIsNotNone(checkup_item)
        self.assertIn("把已完成、未预约、等结果和需复查项目分成四类", json.dumps(checkup_item, ensure_ascii=False))
        self.assertIn("给未预约项目补上日期、地点和是否需要空腹", json.dumps(checkup_item, ensure_ascii=False))
        c_section_item = _find_birth_journey_todo_item(card, "condition_planned_c_section")
        self.assertIsNotNone(c_section_item)
        self.assertIn("准备术前沟通要点", json.dumps(c_section_item, ensure_ascii=False))
        _assert_no_legacy_birth_journey_fallback_items(self, card)
        _assert_birth_journey_item_titles_are_plain(self, card)

        phases = card["phases"]
        self.assertEqual([phase["title"] for phase in phases], ["孕中期", "孕晚期", "临产阶段", "住院分娩", "产后恢复"])
        self.assertEqual(sum(phase["status"] == "current" for phase in phases), 1)
        self.assertIn("约 2026/05/31", phases[0]["date_range"])
        self.assertTrue(all(phase.get("goal") and phase.get("watchouts") and phase.get("actions") for phase in phases))
        self.assertEqual(
                {phase["title"]: phase.get("comate_help") for phase in phases},
                {
                    "孕中期": [],
                    "孕晚期": ["制定个性化待产清单"],
                    "临产阶段": [],
                    "住院分娩": [],
                    "产后恢复": [],
                },
            )
        self.assertEqual(card["next_action"], {"label": "整理产检问题", "send_text": "帮我整理下次产检要问的 3-5 个问题"})
        self.assertNotIn("assistant_followup", result)

        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn("剖宫产", rendered)
        self.assertIn("母乳", rendered)
        self.assertNotIn("生成产检问题清单", rendered)
        self.assertNotIn("打开分娩沟通单", rendered)
        self.assertNotIn("生成夜间分工卡", rendered)
        self.assertNotIn("承接奶量管理计划", rendered)
        self.assertNotIn("| --- |", rendered)
        self.assertNotIn("<br>", rendered)
        _assert_birth_journey_item_reasons_use_current_contract(self, card)

    def test_birth_journey_todo_plan_uses_biweekly_cadence_in_late_pregnancy(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    fetus_count="单胎",
                    checkup_status="已完成糖耐，等待下次产检",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T09:00:00+08:00"},
        )

        card = result["card"]["card_json"]
        _assert_birth_journey_todo_plan(self, card, cadence="biweekly", first_period_title="孕 30-31 周")
        titles = [period["title"] for period in card["todo_plan"]["periods"]]
        self.assertEqual(
            titles,
            ["孕 30-31 周", "孕 32-33 周", "孕 34-35 周", "孕 36 周", "孕 37 周", "孕 38 周", "孕 39 周", "孕 40 周", "临产与住院生产"],
        )
        self.assertIn("双周计划", json.dumps(card["todo_plan"], ensure_ascii=False))

    def test_birth_journey_todo_plan_uses_weekly_cadence_after_week_36(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="36周",
                    birth_setting="深圳妇幼",
                    support_person="伴侣",
                    checkup_status="已预约本周产检",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T09:00:00+08:00"},
        )

        card = result["card"]["card_json"]
        _assert_birth_journey_todo_plan(self, card, cadence="weekly", first_period_title="孕 36 周")
        titles = [period["title"] for period in card["todo_plan"]["periods"]]
        self.assertEqual(titles, ["孕 36 周", "孕 37 周", "孕 38 周", "孕 39 周", "孕 40 周", "临产与住院生产"])
        self.assertIn("每周计划", json.dumps(card["todo_plan"], ensure_ascii=False))

    def test_birth_journey_plan_merges_safety_gate_into_current_focus(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    current_symptoms="今天有阴道流血和腹痛",
                    checkup_status="下周复查",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        current_items = _birth_journey_first_period_items(card)
        self.assertTrue(current_items)
        self.assertEqual(current_items[0]["title"], "做胎动与风险观察")
        self.assertIn("风险触发条件", current_items[0]["reason"])
        _assert_birth_journey_item_text_lengths(self, card)

    def test_birth_journey_plan_filters_future_periods_by_current_week(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="35周",
                    birth_hospital="深圳市妇幼",
                    feeding_intention="混合",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        rendered = json.dumps(card["todo_plan"], ensure_ascii=False)
        self.assertNotIn("28-32 周", rendered)
        self.assertIn("做临产入院准备与联系流程", rendered)
        self.assertIn("宫缩、破水、见红", rendered)
        self.assertNotIn("产后 0-42 天", rendered)

    def test_birth_journey_plan_uses_weekly_guide_for_nt_window(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(due_date_or_week="12周"),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        card = result["card"]["card_json"]
        rendered = json.dumps(card["todo_plan"], ensure_ascii=False)
        self.assertIn("做NT/早筛检查安排", rendered)
        self.assertIn("做唐筛/无创/羊穿决策", rendered)
        self.assertIn("确认NT检查时间在孕周窗口内", rendered)
        self.assertIn("保存NT数值及筛查结果", rendered)
        self.assertIn("如需羊穿，记录预约窗口及术前要求", rendered)
        self.assertNotIn("无花果", rendered)

    def test_birth_journey_future_period_reasons_use_period_not_current_week(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="15周",
                    age="36",
                    fetus_count="双胎",
                    first_birth="否",
                    checkup_status="跳过",
                    risk_factors="跳过",
                    current_symptoms="没有明显不舒服",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        target_period = next(
            period for period in _birth_journey_todo_periods(card) if period.get("title") == "孕 19-22 周"
        )
        target_items = [item for item in target_period.get("items") or [] if isinstance(item, dict)]
        rendered = json.dumps(target_items, ensure_ascii=False)
        self.assertNotIn("你现在孕 15 周", rendered)
        self.assertNotIn("考虑到孕", rendered)
        self.assertIn("做大排畸检查", rendered)
        self.assertIn("做大排畸结果复查确认", rendered)
        for item in target_items:
            self.assertTrue(item.get("hide_reason"))
            self.assertEqual(item.get("reason"), "")
            self.assertEqual(item.get("why_for_you"), "")
            self.assertIn("孕 19-22 周这个检查窗口里", str(item.get("plan_reason") or ""))

    def test_birth_journey_next_7_ignores_generic_intent_and_remote_preparation(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="12周",
                    entry_reason="用户希望制定孕期计划、饮食和运动建议",
                    initial_concerns=["用户希望制定孕期计划、饮食和运动建议"],
                    top_worries="用户希望制定孕期计划、饮食和运动建议",
                    risk_factors="有一些风险因素",
                    feeding_intention="混合",
                    feeding_ibclc_context="计划混合喂养",
                    lifestyle_context="跳过",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        card = result["card"]["card_json"]
        rendered = json.dumps(_birth_journey_first_period_items(card), ensure_ascii=False)
        self.assertIn("做NT/早筛检查安排", rendered)
        self.assertIn("做唐筛/无创/羊穿决策", rendered)
        self.assertNotIn("目的是", rendered)
        self.assertNotIn("【重要】", rendered)
        self.assertNotIn("【建议】", rendered)
        self.assertNotIn("把担心点整理成", rendered)
        self.assertNotIn("产后 48 小时喂养", rendered)
        self.assertNotIn("你提到的担心", card["generation_context"]["personalization_basis"])

    def test_birth_journey_plan_uses_weekly_guide_for_anomaly_scan_window(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(due_date_or_week="21周"),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        card = result["card"]["card_json"]
        rendered = json.dumps(card["todo_plan"], ensure_ascii=False)
        self.assertIn("做大排畸结果复查确认", rendered)
        self.assertIn("拍照保存报告并带给医生复看", rendered)
        self.assertIn("确认胎儿结构、胎盘、羊水及宫颈情况", rendered)
        self.assertIn("标记需复查或随访的项目", rendered)
        self.assertNotIn("小甜瓜", rendered)

    def test_birth_journey_plan_uses_weekly_guide_for_gbs_and_admission_window(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(due_date_or_week="35周"),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        card = result["card"]["card_json"]
        rendered = json.dumps(card["todo_plan"], ensure_ascii=False)
        self.assertIn("做GBS筛查与入院准备", rendered)
        self.assertIn("保存GBS结果并标记阴性/阳性", rendered)
        self.assertIn("确认入院入口及联系电话", rendered)
        self.assertIn("做临产入院准备与联系流程", rendered)
        self.assertNotIn("白兰瓜", rendered)

    def test_birth_journey_plan_safety_gate_includes_unexplained_itching(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    current_symptoms="最近皮肤瘙痒明显",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        card = result["card"]["card_json"]
        current_items = _birth_journey_first_period_items(card)
        self.assertTrue(current_items)
        self.assertIn("做胎动与风险观察", current_items[0]["title"])

    def test_birth_journey_plan_does_not_personalize_from_skipped_or_unknown_answers(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    checkup_status="未上传产检记录",
                    current_symptoms="没有明显不舒服",
                    risk_factors="不清楚",
                    lifestyle_context="跳过",
                    feeding_ibclc_context="跳过",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        rendered = json.dumps(card["todo_plan"], ensure_ascii=False)
        self.assertIn("做胎动与异常观察", rendered)
        self.assertIn("记录胎动明显增减变化", rendered)
        self.assertNotIn("补齐下次产检时间", rendered)
        self.assertNotIn("你已经提供产检信息", rendered)
        self.assertNotIn("把已做产检和待复查项整理成问题清单", rendered)
        self.assertNotIn("风险因素", rendered)
        self.assertNotIn("特殊情况", rendered)
        _assert_birth_journey_item_text_lengths(self, card)

    def test_birth_journey_plan_turns_advanced_maternal_age_into_specific_actions(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="25周",
                    age="36",
                    checkup_status="跳过",
                    current_symptoms="没有明显不舒服",
                    lifestyle_context="跳过",
                    risk_factors="跳过",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        rendered = json.dumps(_birth_journey_first_period_items(card), ensure_ascii=False)
        self.assertIn("高龄风险产检补充", rendered)
        self.assertIn("确认血压、血糖、尿蛋白复查频率", rendered)
        self.assertIn("设置胎儿生长与羊水复查计划", rendered)
        self.assertIn("你 36 岁属于高龄孕产妇管理范围", rendered)
        age_item = _find_birth_journey_todo_item(card, "condition_age_35_plus")
        self.assertIsNotNone(age_item)
        self.assertTrue(str(age_item.get("why_for_you") or "").startswith("考虑到你 36 岁属于高龄孕产妇管理范围"))
        self.assertFalse(age_item.get("hide_reason"))
        self.assertTrue(str(age_item.get("reason") or "").startswith("重要｜考虑到你 36 岁属于高龄孕产妇管理范围"))
        self.assertNotIn("确认高龄孕期关注重点", rendered)

    def test_birth_journey_plan_does_not_invent_generic_risk_item_from_age_or_twins(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="20周",
                    age="37",
                    fetus_count="双胎",
                    first_birth="是",
                    risk_factors="跳过",
                    checkup_status="跳过",
                    current_symptoms="没有明显不舒服",
                    lifestyle_context="跳过",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        rendered = json.dumps(card["todo_plan"], ensure_ascii=False)
        self.assertIsNotNone(_find_birth_journey_todo_item(card, "condition_age_35_plus"))
        self.assertIsNotNone(_find_birth_journey_todo_item(card, "condition_multiple"))
        self.assertIsNone(_find_birth_journey_todo_item(card, "condition_risk_factors"))
        self.assertIn("高龄风险产检补充", rendered)
        self.assertIn("多胎管理", rendered)
        self.assertNotIn("做胎动与风险观察", rendered)

    def test_birth_journey_plan_adds_risk_item_only_for_explicit_risk_context(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="20周",
                    age="37",
                    fetus_count="双胎",
                    risk_factors="胎盘低置需要复查",
                    checkup_status="跳过",
                    current_symptoms="没有明显不舒服",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        risk_item = _find_birth_journey_todo_item(card, "condition_risk_factors")
        self.assertIsNotNone(risk_item)
        rendered = json.dumps(risk_item, ensure_ascii=False)
        self.assertIn("做胎动与风险观察", rendered)
        self.assertIn("设置风险触发条件", rendered)

    def test_birth_journey_plan_keeps_all_items_but_limits_each_item_length(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    checkup_status="已做 NT、NIPT、大排畸，结果正常，还没预约糖耐",
                    current_symptoms="没有明显不舒服",
                    risk_factors="孕前 BMI 28",
                    lifestyle_context="久坐上班，晚上腿抽筋",
                    feeding_intention="母乳",
                    feeding_ibclc_context="计划母乳，想了解产后支持",
                    birth_path="剖宫产",
                    fetus_count="双胎",
                    birth_hospital="深圳市妇幼",
                    support_person="伴侣",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        periods = _birth_journey_todo_periods(card)
        self.assertGreaterEqual(len(_birth_journey_first_period_items(card)), 4)
        self.assertGreaterEqual(len(periods), 2)
        _assert_birth_journey_item_text_lengths(self, card)

    def test_birth_journey_plan_is_saved_as_care_plan_artifact_when_user_id_exists(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                result = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            birth_path="剖宫产",
                            support_person="伴侣",
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-08T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "card_created")
                self.assertIsNotNone(result.get("plan"))
                plans = data_store.list_care_plan_artifacts(user_id="app-user")
                self.assertEqual(len(plans), 1)
                self.assertEqual(plans[0]["plan_type"], "birth_journey")
                self.assertEqual(plans[0]["title"], "孕期计划")
                self.assertEqual(plans[0]["payload"]["owner"]["current_week"], "孕30周")
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_todo_completion_updates_saved_plan(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                created = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            checkup_status="未上传产检记录",
                            current_symptoms="没有明显不舒服",
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-08T09:00:00+08:00"},
                )
                plan_id = int(created["plan"]["plan_id"])

                updated = update_birth_journey_plan_todo_completion_for_user(
                    user_id="app-user",
                    plan_id=plan_id,
                    item_refs=["todo_01"],
                    completed=True,
                    source="app",
                )

                self.assertEqual(updated["status"], "todo_completion_updated")
                self.assertTrue(updated["side_effect_performed"])
                self.assertEqual(updated["updated_items"][0]["id"], "todo_01")
                self.assertTrue(updated["updated_items"][0]["completed"])
                self.assertIn(updated["updated_items"][0]["priority_type"], {"essential", "supportive"})
                self.assertNotIn("done_criteria", updated["updated_items"][0])
                self.assertTrue(updated["updated_items"][0]["completion_followup"])
                self.assertTrue(updated["completion_followups"])
                saved = data_store.get_care_plan_artifact(user_id="app-user", plan_id=plan_id)
                todo_items = saved["payload"]["todo_plan"]["periods"][0]["items"]
                self.assertTrue(todo_items[0]["completed"])
                self.assertEqual(todo_items[0]["completed_source"], "app")
                self.assertNotIn("done_criteria", todo_items[0])

                context = build_request_context(
                    {
                        "user_message": "第一项完成了",
                        "user_id": "app-user",
                        "service_domain": "birth_prep",
                        "message_sent_at": "2026-06-08T10:00:00+08:00",
                    }
                )
                self.assertIn("current_birth_journey_todos", context)
                self.assertIn("1. [done]", context)
                self.assertIn("next=", context)
                milk_context = build_request_context(
                    {
                        "user_message": "看一下今天奶量",
                        "user_id": "app-user",
                        "service_domain": "milk-management",
                        "message_sent_at": "2026-06-08T10:00:00+08:00",
                    }
                )
                self.assertIn("active_care_plan_context:", milk_context)
                self.assertNotIn("current_birth_journey_todos", milk_context)

                updated_by_source_id = update_birth_journey_plan_todo_completion_for_user(
                    user_id="app-user",
                    plan_id=plan_id,
                    item_refs=[todo_items[0]["id"]],
                    completed=False,
                    source="app",
                )
                self.assertEqual(updated_by_source_id["status"], "todo_completion_updated")
                saved_by_source_id = data_store.get_care_plan_artifact(user_id="app-user", plan_id=plan_id)
                self.assertFalse(saved_by_source_id["payload"]["todo_plan"]["periods"][0]["items"][0]["completed"])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_todo_tool_updates_by_item_number(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                created = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            checkup_status="未上传产检记录",
                            current_symptoms="没有明显不舒服",
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-08T09:00:00+08:00"},
                )
                plan_id = int(created["plan"]["plan_id"])

                result = update_birth_journey_plan_todo(
                    {"item_ids": None, "item_numbers": [2], "item_refs": None, "completed": True},
                    {"user_message": "第二项已经做完了", "user_id": "app-user"},
                )

                self.assertEqual(result["tool_name"], "birth_journey_plan_todo_update")
                self.assertEqual(result["status"], "todo_completion_updated")
                saved = data_store.get_care_plan_artifact(user_id="app-user", plan_id=plan_id)
                todo_items = saved["payload"]["todo_plan"]["periods"][0]["items"]
                self.assertFalse(todo_items[0]["completed"])
                self.assertTrue(todo_items[1]["completed"])
                compact = model_tool_output({"ok": True, "tool_name": "birth_journey_plan_todo_update", "result": result})
                self.assertIn("最终回复只简短说明已同步", compact["final_response_instruction"])
                self.assertIn("完成后的下一步帮助", compact["final_response_instruction"])
                self.assertTrue(compact["completion_followups"])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_existing_birth_journey_plan_is_reused_instead_of_recreated(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                first = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            birth_path="顺产",
                            support_person="伴侣",
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-08T09:00:00+08:00"},
                )
                first_plan_id = first["plan"]["plan_id"]

                reused = create_birth_journey_plan_card(
                    {"plan_context": {}, "scope": "full"},
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(reused["status"], "existing_plan_found")
                self.assertFalse(reused["side_effect_performed"])
                self.assertEqual(reused["plan"]["plan_id"], first_plan_id)
                self.assertEqual(reused["card"]["card_json"]["title"], "孕期计划")
                compact = model_tool_output({"ok": True, "tool_name": "birth_journey_plan_card_create", "result": reused})
                self.assertIn("不重复生成", compact["final_response_instruction"])
                plans = data_store.list_care_plan_artifacts(user_id="app-user")
                self.assertEqual(len(plans), 1)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_stale_birth_journey_plan_with_old_explanation_phrasing_is_not_reused(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                stale = data_store.save_care_plan_artifact(
                    user_id="app-user",
                    plan_type="birth_journey",
                    title="孕期计划",
                    summary="旧版孕期计划",
                    payload={
                        "title": "孕期计划",
                        "planning_layers": {
                            "next_7_days": {
                                "items": [
                                    {
                                        "id": "next7_01",
                                        "title": "把担心点整理成 3 个医生问题",
                                        "reason": "考虑到你提到生活或工作压力，目的是把担心点整理成医生问题。",
                                    }
                                ],
                            }
                        },
                    },
                    source_artifact_type="birth_journey_plan_card",
                )
                self.assertIsNotNone(stale)

                result = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(due_date_or_week="12周"),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "card_created")
                self.assertNotEqual(result["plan"]["plan_id"], stale["plan_id"])
                card = result["card"]["card_json"]
                self.assertNotIn("planning_layers", card)
                rendered = json.dumps(card["todo_plan"], ensure_ascii=False)
                self.assertNotIn("【重要】", rendered)
                self.assertNotIn("目的是", rendered)
                self.assertNotIn("考虑到孕", rendered)
                self.assertIn("孕 12-15 周这个检查窗口里", rendered)
                self.assertIn("做NT/早筛检查安排", rendered)
                self.assertEqual(len(data_store.list_care_plan_artifacts(user_id="app-user")), 2)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_stale_actionable_v2_todo_plan_is_not_reused(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                stale = data_store.save_care_plan_artifact(
                    user_id="app-user",
                    plan_type="birth_journey",
                    title="孕期计划",
                    summary="旧 actionable_v2 计划",
                    payload={
                        "title": "孕期计划",
                        "todo_engine_version": "actionable_v2",
                        "todo_plan": {
                            "periods": [
                                {
                                    "id": "period_01",
                                    "title": "孕 29-32 周",
                                    "items": [
                                        {
                                            "id": "old_01",
                                            "title": "建立胎动和异常联系机制",
                                            "reason": "重要｜考虑到你进入孕晚期，固定观察节奏很重要。",
                                            "priority_type": "essential",
                                            "priority_label": "重要",
                                            "steps": ["定观察时间"],
                                        }
                                    ],
                                }
                            ]
                        },
                    },
                    source_artifact_type="birth_journey_plan_card",
                )

                result = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(due_date_or_week="30周", checkup_status="未上传产检记录"),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "card_created")
                self.assertNotEqual(result["plan"]["plan_id"], stale["plan_id"])
                rendered = json.dumps(result["card"]["card_json"], ensure_ascii=False)
                self.assertNotIn("建立胎动和异常联系机制", rendered)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_delete_requires_confirmation(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                saved = data_store.save_care_plan_artifact(
                    user_id="app-user",
                    plan_type="birth_journey",
                    title="孕期计划",
                    summary="孕晚期生产准备",
                    payload={"title": "孕期计划", "phases": []},
                    source_artifact_type="birth_journey_plan_card",
                )
                self.assertIsNotNone(saved)

                result = delete_birth_journey_plan(
                    {"confirmed": False},
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "needs_delete_confirmation")
                self.assertFalse(result["side_effect_performed"])
                self.assertEqual(len(data_store.list_care_plan_artifacts(user_id="app-user", status="active")), 1)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_delete_soft_deletes_active_plan(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                saved = data_store.save_care_plan_artifact(
                    user_id="app-user",
                    plan_type="birth_journey",
                    title="孕期计划",
                    summary="孕晚期生产准备",
                    payload={"title": "孕期计划", "phases": []},
                    source_artifact_type="birth_journey_plan_card",
                )
                self.assertIsNotNone(saved)

                result = delete_birth_journey_plan(
                    {"confirmed": True},
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "plan_deleted")
                self.assertTrue(result["side_effect_performed"])
                self.assertEqual(result["plan_type"], "birth_journey")
                self.assertEqual(result["plan_id"], saved["plan_id"])
                self.assertEqual(data_store.list_care_plan_artifacts(user_id="app-user", status="active"), [])
                self.assertEqual(len(data_store.list_care_plan_artifacts(user_id="app-user", status="deleted")), 1)
                compact = model_tool_output({"ok": True, "tool_name": "birth_journey_plan_delete", "result": result})
                self.assertIn("已删除孕期计划", compact["final_response_instruction"])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_delete_handles_missing_plan(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                result = delete_birth_journey_plan(
                    {"confirmed": True},
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "plan_not_found")
                self.assertFalse(result["side_effect_performed"])
                compact = model_tool_output({"ok": True, "tool_name": "birth_journey_plan_delete", "result": result})
                self.assertIn("没有找到 active 孕期计划", compact["final_response_instruction"])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_request_context_includes_active_birth_journey_plan_memory(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                data_store.save_care_plan_artifact(
                    user_id="app-user",
                    plan_type="birth_journey",
                    title="孕期计划",
                    summary="从孕30周到产后 42 天的阶段路线图；当前阶段：孕晚期",
                    payload={
                        "title": "孕期计划",
                        "phases": [{"title": "孕晚期", "status": "current"}],
                    },
                    source_artifact_type="birth_journey_plan_card",
                )

                context = build_request_context(
                    {"user_message": "帮我制定孕期计划", "locale": "zh-CN", "user_id": "app-user"},
                    None,
                    ["birth-prep"],
                )

                self.assertIn("active_care_plan_context:", context)
                self.assertIn("birth_journey_plan: 已存在 active 孕期计划", context)
                self.assertIn("current_phase=孕晚期", context)
                self.assertIn("不要再次调用 birth_journey_plan_card_create 创建新计划", context)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_exposes_birth_journey_plan_tool(self) -> None:
        namespaces = {
            str(tool.get("name")): tool
            for tool in select_runtime_tools()
            if isinstance(tool, dict) and tool.get("type") == "namespace"
        }
        tool_names = [tool.get("name") for tool in namespaces["birth_prep"]["tools"]]

        self.assertIn("birth_journey_intake_manage", tool_names)
        self.assertIn("birth_journey_plan_card_create", tool_names)

    def test_birth_journey_plan_requires_core_intake_and_checkup_status(self) -> None:
        result = create_birth_journey_plan_card(
            {"plan_context": {"due_date_or_week": "26周"}, "scope": "full"},
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "needs_required_context")
        self.assertIn("basic_info", result["missing_fields"])
        self.assertIn("checkup_records", result["missing_fields"])
        self.assertNotIn("risk_factors", result["missing_fields"])
        self.assertNotIn("current_symptoms", result["missing_fields"])
        self.assertNotIn("lifestyle_context", result["missing_fields"])
        self.assertNotIn("feeding_ibclc_context", result["missing_fields"])
        self.assertNotIn("card", result)
        self.assertIn("只答知道的", result["data"]["confirmation_question"])
        self.assertIn("不清楚", result["data"]["confirmation_question"])
        self.assertIn("上传目前全部产检记录", result["data"]["confirmation_question"])

    def test_birth_journey_plan_merges_active_intake_state_when_model_passes_partial_context(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(current_week="30周", age="30")},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )
        skipped = manage_birth_journey_intake(
            {"action": "skip_checkup_records", "payload": {}},
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )

        result = create_birth_journey_plan_card(
            {
                "plan_context": {
                    "feeding_intention": "母乳",
                    "feeding_ibclc_context": "计划母乳喂养",
                },
                "scope": "full",
            },
            {**inputs, "_birth_journey_intake_state": skipped["intake_state"]},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        self.assertEqual(card["owner"]["due_date_or_week"], "30周")
        self.assertEqual(card["owner"]["current_week"], "孕30周")
        self.assertNotIn("missing_fields", result)

    def test_birth_journey_intake_flow_returns_plan_context_when_ready(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        self.assertEqual(started["status"], "in_progress")
        self.assertEqual(started["next_step"], "basic_info_form")
        self.assertEqual(started["form"]["id"], "birth_journey_basic_info_intake")

        state = started["intake_state"]
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(current_week="28周", age="32")},
            {**inputs, "_birth_journey_intake_state": state},
        )
        state = basic["intake_state"]
        uploaded = manage_birth_journey_intake(
            {"action": "mark_checkup_records_uploaded", "payload": {}},
            {**inputs, "_birth_journey_intake_state": state},
        )

        self.assertEqual(uploaded["status"], "in_progress")
        self.assertEqual(uploaded["next_step"], "final_plan_confirmation")
        self.assertIn("还有其他需要补充的信息吗", uploaded["data"]["confirmation_question"])

        ready = _confirm_birth_journey_ready_to_generate(inputs, uploaded)
        self.assertEqual(ready["status"], "ready_to_generate")
        self.assertEqual(ready["next_step"], "generate_plan")
        self.assertEqual(ready["plan_context"]["due_date_or_week"], "28周")
        self.assertEqual(ready["plan_context"]["checkup_records_uploaded"], "是")
        self.assertNotIn("lifestyle_context", ready["plan_context"])

        with_extra = _confirm_birth_journey_ready_to_generate(inputs, uploaded, additional_info="胎盘低置需要复查")
        self.assertEqual(with_extra["status"], "ready_to_generate")
        self.assertEqual(with_extra["plan_context"]["final_additional_info"], "胎盘低置需要复查")
        self.assertIn("胎盘低置需要复查", with_extra["plan_context"]["risk_factors"])

    def test_birth_journey_confirmed_basic_form_defaults_to_submit_basic_info(self) -> None:
        inputs = {
            "user_message": (
                "我已确认孕期计划基础信息。\n"
                "form_id: birth_journey_basic_info_intake\n"
                'confirmed_form_data:\n{"current_week":"20周","ivf":"不确定/暂不说","fetus_count":"单胎","age":"31","first_birth":"不确定/暂不说","birth_path":"还没确定"}'
            ),
            "message_sent_at": "2026-06-01T12:00:00+08:00",
            "_birth_journey_intake_state": {},
        }

        result = manage_birth_journey_intake({}, inputs)

        self.assertEqual(result["action"], "submit_basic_info")
        self.assertEqual(result["intake_state"]["basic_info"]["current_week"], "20周")
        self.assertEqual(result["next_step"], "checkup_records_upload")
        self.assertNotIn("待产包", json.dumps(result, ensure_ascii=False))

    def test_birth_journey_confirmed_basic_form_defaults_missing_non_week_fields(self) -> None:
        inputs = {
            "user_message": (
                "我已确认孕期计划基础信息。\n"
                "form_id: birth_journey_basic_info_intake\n"
                'confirmed_form_data:\n{"current_week":"20周","fetus_count":"单胎","age":"31"}'
            ),
            "message_sent_at": "2026-06-01T12:00:00+08:00",
            "_birth_journey_intake_state": {},
        }

        result = manage_birth_journey_intake({}, inputs)

        safe = safe_tool_result({"ok": True, "tool_name": "birth_journey_intake_manage", "result": result})
        self.assertIs(safe["result_ok"], True)
        self.assertEqual(result["action"], "submit_basic_info")
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["next_step"], "checkup_records_upload")
        self.assertNotIn("form", result)
        self.assertNotIn("missing_fields", result)
        self.assertEqual(result["intake_state"]["basic_info"]["ivf"], "不确定/暂不说")
        self.assertEqual(result["intake_state"]["basic_info"]["first_birth"], "不确定/暂不说")
        self.assertEqual(result["intake_state"]["basic_info"]["birth_path"], "还没确定")
        self.assertIn("basic_info", result["data"]["completed_groups"])

        compact = model_tool_output({"ok": True, "tool_name": "birth_journey_intake_manage", "result": result})
        self.assertEqual(compact["status"], "in_progress")
        self.assertNotIn("form", compact)
        self.assertEqual(compact["next_step"], "checkup_records_upload")

    def test_birth_journey_confirmed_basic_form_missing_week_asks_one_question_without_form(self) -> None:
        inputs = {
            "user_message": (
                "我已确认孕期计划基础信息。\n"
                "form_id: birth_journey_basic_info_intake\n"
                'confirmed_form_data:\n{"ivf":"否","fetus_count":"单胎","age":"31","first_birth":"是","birth_path":"还没确定"}'
            ),
            "message_sent_at": "2026-06-01T12:00:00+08:00",
            "_birth_journey_intake_state": {},
        }

        result = manage_birth_journey_intake({}, inputs)

        self.assertEqual(result["action"], "submit_basic_info")
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["next_step"], "current_week_question")
        self.assertNotIn("form", result)
        self.assertEqual(result["missing_fields"], ["current_week"])
        self.assertIn("大概孕几周", result["data"]["confirmation_question"])

        compact = model_tool_output({"ok": True, "tool_name": "birth_journey_intake_manage", "result": result})
        self.assertEqual(compact["next_step"], "current_week_question")
        self.assertNotIn("form", compact)
        self.assertIn("不要创建新表单", compact["final_response_instruction"])

    def test_birth_journey_confirmed_basic_form_ignores_trailing_payload_text(self) -> None:
        inputs = {
            "user_message": (
                "我已确认孕期计划基础信息。\n"
                "form_id: birth_journey_basic_info_intake\n"
                'confirmed_form_data:\n{"current_week":"20周","ivf":"不确定/暂不说","fetus_count":"单胎","age":"31","first_birth":"不确定/暂不说","birth_path":"还没确定"}\n'
                "display_text: 已提交：孕周与基本情况"
            ),
            "message_sent_at": "2026-06-01T12:00:00+08:00",
            "_birth_journey_intake_state": {},
        }

        result = manage_birth_journey_intake({"action": "start"}, inputs)

        self.assertEqual(result["action"], "submit_basic_info")
        self.assertEqual(result["intake_state"]["basic_info"]["current_week"], "20周")
        self.assertEqual(result["next_step"], "checkup_records_upload")
        self.assertNotIn("form", result)

    def test_birth_journey_confirmed_basic_form_with_high_age_prompts_personalized_followup_first(self) -> None:
        inputs = {
            "user_message": (
                "我已确认孕期计划基础信息。\n"
                "form_id: birth_journey_basic_info_intake\n"
                'confirmed_form_data:\n{"current_week":"20周","ivf":"不确定/暂不说","fetus_count":"单胎","age":"36","first_birth":"不确定/暂不说","birth_path":"还没确定"}'
            ),
            "message_sent_at": "2026-06-01T12:00:00+08:00",
            "_birth_journey_intake_state": {},
        }

        result = manage_birth_journey_intake({}, inputs)

        self.assertEqual(result["action"], "submit_basic_info")
        self.assertEqual(result["next_step"], "personalized_followup")
        topic = _birth_journey_suggested_topic(result, "age_35_plus_checkup_detail")
        self.assertEqual(topic["id"], "age_35_plus_checkup_detail")
        self.assertIn("我注意到你36岁", topic["observation"])
        self.assertIn("高龄孕产妇", topic["observation"])
        self.assertIn("产科管理", topic["key_points"][0])
        self.assertIn("高龄孕产妇", topic["key_points"][0])
        self.assertIn("血压/血糖", topic["followup_question"])
        self.assertIn("甲状腺/免疫或长期用药", topic["followup_question"])
        self.assertIn("有没有已经被提醒过或正在复查", topic["followup_question"])
        self.assertIn("暂无异常", topic["followup_question"])
        self.assertNotIn("目前最需要纳入计划", topic["followup_question"])
        self.assertNotIn("不代表一定有问题", topic["meaning"])
        self.assertNotIn("知道", topic["followup_question"])
        self.assertNotIn("最想先弄清", topic["followup_question"])
        self.assertNotIn("医生有没有", topic["question"])
        self.assertNotIn("personalized_followup", result["data"])
        self.assertNotIn("personalized_followup_queue", result["intake_state"])
        self.assertEqual([item["id"] for item in _birth_journey_suggested_topics(result)], ["age_35_plus_checkup_detail"])

    def test_birth_journey_prior_c_section_prompts_personalized_followup_and_satisfies_risk(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        basic = manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": _basic_info_payload(
                    first_birth="否",
                    prior_birth_history="既往剖宫产1次",
                ),
            },
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(basic["next_step"], "personalized_followup")
        topic = _birth_journey_suggested_topic(basic, "prior_c_section_birth_path_detail")
        self.assertEqual(topic["id"], "prior_c_section_birth_path_detail")
        self.assertIn("上次剖宫产的主要原因", topic["followup_question"])
        self.assertNotIn("有没有", topic["question"])

        answered = manage_birth_journey_intake(
            {
                "action": "submit_personalized_followup",
                "payload": {
                    "followup_id": "prior_c_section_birth_path_detail",
                    "answer": "上次因为臀位剖宫产，医生说这次下次产检再评估。",
                },
            },
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )
        answered = _answer_birth_journey_personalized_followups(inputs, answered)
        skipped = manage_birth_journey_intake(
            {"action": "skip_checkup_records", "payload": {}},
            {**inputs, "_birth_journey_intake_state": answered["intake_state"]},
        )

        self.assertEqual(skipped["status"], "in_progress")
        self.assertEqual(skipped["next_step"], "final_plan_confirmation")
        ready = _confirm_birth_journey_ready_to_generate(inputs, skipped)
        self.assertEqual(ready["status"], "ready_to_generate")
        self.assertEqual(ready["next_step"], "generate_plan")
        self.assertNotIn("risk_factors", skipped["intake_state"]["completed_groups"])
        self.assertNotIn("risk_factors", ready["plan_context"])
        self.assertNotIn("doctor_notes", ready["plan_context"])
        self.assertIn("臀位剖宫产", ready["plan_context"]["prior_birth_history"])
        self.assertEqual(ready["plan_context"]["personalized_followup_records"][0]["topic"], "prior_c_section_birth_path_detail")
        self.assertIn("臀位剖宫产", ready["plan_context"]["personalized_facts"])

        plan = create_birth_journey_plan_card(
            {"plan_context": ready["plan_context"], "scope": "full"},
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )
        rendered = json.dumps(plan["card"]["card_json"], ensure_ascii=False)
        self.assertNotIn("把医生提醒设成复查和观察提醒", rendered)

    def test_birth_journey_followup_question_keeps_next_personalized_source_context(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        basic = manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": _basic_info_payload(age="36", first_birth="否"),
            },
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(
            [item["id"] for item in _birth_journey_suggested_topics(basic)],
            ["prior_birth_history_detail", "age_35_plus_checkup_detail"],
        )

        next_followup = manage_birth_journey_intake(
            {
                "action": "submit_personalized_followup",
                "payload": {
                    "topic": "prior_birth_history_detail",
                    "answer": "上一胎是顺产，没有早产和产后出血问题",
                },
            },
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )

        self.assertEqual(next_followup["next_step"], "personalized_followup")
        topic = _birth_journey_suggested_topic(next_followup, "age_35_plus_checkup_detail")
        self.assertIn("你36岁", topic["observation"])
        self.assertIn("高龄孕产妇", topic["observation"])
        self.assertIn("血压/血糖", topic["followup_question"])
        self.assertEqual(next_followup["data"]["personalization_context"]["asked_followups"][0]["topic"], "prior_birth_history_detail")

    def test_birth_journey_personalized_followup_combines_age_and_multiple_context(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        basic = manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": _basic_info_payload(age="36", fetus_count="双胎", first_birth="否"),
            },
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )
        self.assertEqual(
            [item["id"] for item in _birth_journey_suggested_topics(basic)],
            ["age_35_plus_multiple_monitoring", "prior_birth_history_detail"],
        )
        topic = _birth_journey_suggested_topic(basic, "age_35_plus_multiple_monitoring")
        self.assertIn("36岁", topic["observation"])
        self.assertIn("高龄孕产妇", topic["observation"])
        self.assertIn("双胎", topic["observation"])
        self.assertIn("高龄孕产妇", topic["key_points"][0])
        self.assertIn("生长差异", topic["key_points"][1])
        self.assertIn("宫颈长度", topic["followup_question"])

        compact = model_tool_output({"ok": True, "tool_name": "birth_journey_intake_manage", "result": basic})

        self.assertEqual(compact["personalization_context"]["mode"], "model_driven_followup")
        self.assertEqual(compact["personalization_context"]["suggested_topics"][0]["id"], "age_35_plus_multiple_monitoring")
        self.assertIn("key_points", compact["personalization_context"]["suggested_topics"][0])
        self.assertIn("must_mention", compact["personalization_context"]["suggested_topics"][0])
        self.assertIn("response_contract", compact["personalization_context"])
        self.assertIn("用户信息 -> 孕期管理意义 -> 计划影响 -> 一个具体追问", compact["personalization_context"]["response_contract"])
        self.assertIn("高龄孕产妇/产科管理范围", compact["personalization_context"]["response_contract"])
        self.assertNotIn("initial_analysis", compact)
        self.assertNotIn("checkup_report_strategy", compact)
        self.assertNotIn("personalization_tags", compact)
        self.assertNotIn("multiple_pregnancy_monitoring", json.dumps(compact, ensure_ascii=False))

        next_followup = manage_birth_journey_intake(
            {
                "action": "submit_personalized_followup",
                "payload": {"topic": "age_35_plus_multiple_monitoring", "answer": "暂无异常"},
            },
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )
        self.assertEqual(_birth_journey_suggested_topics(next_followup)[0]["id"], "prior_birth_history_detail")

        combined_answered = manage_birth_journey_intake(
            {
                "action": "submit_personalized_followup",
                "payload": {
                    "topic": "prior_birth_history_detail",
                    "answer": "上一胎是顺产，没有早产和产后出血问题",
                },
            },
            {**inputs, "_birth_journey_intake_state": next_followup["intake_state"]},
        )
        self.assertEqual(combined_answered["next_step"], "checkup_records_upload")
        self.assertNotIn("personalized_followup", combined_answered["data"])

    def test_birth_journey_ignores_mismatched_personalized_followup_id(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        basic = manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": _basic_info_payload(age="36", fetus_count="双胎", first_birth="否"),
            },
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        mismatched = manage_birth_journey_intake(
            {
                "action": "submit_personalized_followup",
                "payload": {"followup_id": "multiple_pregnancy_monitoring", "answer": "还没确认"},
            },
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )

        self.assertEqual(mismatched["next_step"], "personalized_followup")
        self.assertIn("multiple_pregnancy_monitoring", mismatched["intake_state"].get("personalized_followups", {}))
        self.assertEqual(mismatched["intake_state"]["personalized_followup_records"][0]["topic"], "multiple_pregnancy_monitoring")
        self.assertNotIn("last_personalized_followup_mismatch", mismatched["intake_state"])

    def test_birth_journey_model_followup_can_finish_without_repeating_topics(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(age="36", fetus_count="双胎", first_birth="否")},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(basic["next_step"], "personalized_followup")
        self.assertEqual(_birth_journey_suggested_topics(basic)[0]["id"], "age_35_plus_multiple_monitoring")

        finished = manage_birth_journey_intake(
            {"action": "finish_personalized_followups", "payload": {"summary": "用户表示暂无异常，先按已知信息制定计划。"}},
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )

        self.assertEqual(finished["next_step"], "checkup_records_upload")
        self.assertTrue(finished["intake_state"]["personalized_followup_done"])
        self.assertEqual(finished["intake_state"]["personalized_followup_summary"], "用户表示暂无异常，先按已知信息制定计划。")
        self.assertNotIn("personalization_context", finished["data"])

    def test_birth_journey_personalized_followups_share_source_reason_question_contract(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        current = manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": _basic_info_payload(age="36", first_birth="否"),
            },
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        topics = _birth_journey_suggested_topics(current)
        seen_ids = [str(topic["id"]) for topic in topics]
        for followup in topics:
            self.assertTrue(followup["observation"])
            self.assertTrue(followup["meaning"])
            self.assertTrue(followup["followup_question"])
            if followup["id"] == "pregnancy_milestone_status_detail":
                self.assertIn("你36岁", followup["observation"])
                self.assertIn("这次不是第一胎", followup["observation"])

        self.assertEqual(
            seen_ids,
            [
                "prior_birth_history_detail",
                "age_35_plus_checkup_detail",
            ],
        )

    def test_birth_journey_first_birth_does_not_trigger_knowledge_followup(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        basic = manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": _basic_info_payload(first_birth="是"),
            },
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(basic["next_step"], "checkup_records_upload")
        self.assertNotIn("personalized_followup", basic["data"])

        skipped = manage_birth_journey_intake(
            {"action": "skip_checkup_records", "payload": {}},
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )

        self.assertEqual(skipped["status"], "in_progress")
        self.assertEqual(skipped["next_step"], "final_plan_confirmation")
        self.assertNotIn("risk_factors", skipped["intake_state"]["completed_groups"])

    def test_birth_journey_multiple_pregnancy_followup_is_guidance_not_doctor_question(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(fetus_count="双胎")},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(basic["next_step"], "personalized_followup")
        followup = _birth_journey_suggested_topic(basic, "multiple_pregnancy_monitoring")
        self.assertEqual(followup["id"], "multiple_pregnancy_monitoring")
        self.assertIn("双胎", followup["observation"])
        self.assertNotIn("双胎/多胎", followup["observation"])
        self.assertIn("生长差异", followup["key_points"][0])
        self.assertIn("目前产检记录里的双胎类型", followup["followup_question"])
        self.assertIn("单绒双羊、双绒双羊", followup["followup_question"])
        self.assertEqual(list(followup["reply_options"]), ["单绒双羊", "双绒双羊", "还没确认"])
        self.assertNotIn("医生有没有", followup["question"])
        self.assertNotIn("有没有", followup["question"])
        self.assertNotIn("知道", followup["question"])

    def test_birth_journey_personalized_followups_are_capped_at_five_core_items(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        current = manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": {
                    **_basic_info_payload(
                        current_week="20周",
                        age="38",
                        ivf="是",
                        fetus_count="双胎",
                        first_birth="否",
                        birth_path="剖宫产",
                    ),
                    "prior_birth_history": "上次剖宫产，早产，有流产和产后出血史",
                    "medical_notes": "高血压，妊娠糖尿病，甲状腺用药",
                    "doctor_notes": "胎盘低置，需要复查",
                },
            },
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        followup_ids: list[str] = []
        while current["next_step"] == "personalized_followup":
            followup = _birth_journey_suggested_topics(current)[0]
            followup_ids.append(followup["id"])
            current = manage_birth_journey_intake(
                {
                    "action": "submit_personalized_followup",
                    "payload": {"topic": followup["id"], "answer": "继续下一步"},
                },
                {**inputs, "_birth_journey_intake_state": current["intake_state"]},
            )

        self.assertEqual(
            followup_ids,
            [
                "doctor_special_notes_followup",
                "prior_c_section_birth_path_detail",
                "prior_preterm_monitoring_detail",
            ],
        )
        self.assertEqual(len(followup_ids), 3)
        self.assertEqual(current["next_step"], "checkup_records_upload")
        self.assertEqual(len(current["intake_state"]["personalized_followup_records"]), 3)
        self.assertNotIn("hypertension_or_preeclampsia_monitoring", followup_ids)
        self.assertNotIn("diabetes_or_gdm_monitoring", followup_ids)
        self.assertNotIn("chronic_medical_condition_coordination", followup_ids)
        self.assertNotIn("age_35_plus_checkup_detail", followup_ids)

    def test_birth_journey_personalized_followups_include_non_age_plan_factors(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        current = manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": {
                    **_basic_info_payload(ivf="是", birth_path="剖宫产"),
                    "medical_notes": "甲状腺长期用药，内分泌科定期复查",
                },
            },
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        followup_ids: list[str] = []
        questions: list[str] = []
        while current["next_step"] == "personalized_followup":
            followup = _birth_journey_suggested_topics(current)[0]
            followup_ids.append(followup["id"])
            questions.append(followup["followup_question"])
            current = manage_birth_journey_intake(
                {
                    "action": "submit_personalized_followup",
                    "payload": {"topic": followup["id"], "answer": "还不确定"},
                },
                {**inputs, "_birth_journey_intake_state": current["intake_state"]},
            )

        self.assertEqual(
            followup_ids,
            [
                "chronic_medical_condition_coordination",
                "ivf_week_confirmation",
                "planned_c_section_detail",
            ],
        )
        self.assertEqual(len(followup_ids), 3)
        self.assertTrue(any("长期吃药" in question for question in questions))
        self.assertTrue(any("移植日期/孕周口径" in question for question in questions))
        self.assertTrue(any("计划剖宫产主要是因为" in question for question in questions))
        self.assertFalse(any("最需要先纳入计划" in question or "目前最需要纳入计划" in question for question in questions))

    def test_birth_journey_entry_context_does_not_insert_extra_concern_step(self) -> None:
        inputs = {
            "user_message": "我不知道接下来要准备什么",
            "message_sent_at": "2026-06-01T12:00:00+08:00",
            "_birth_journey_intake_state": {},
        }
        started = manage_birth_journey_intake(
            {
                "action": "start",
                "payload": {
                    "entry_reason": "我不知道接下来要准备什么",
                    "initial_concerns": ["怕漏事"],
                    "known_values": {"current_week": "30周", "age": "43"},
                },
            },
            inputs,
        )

        self.assertEqual(started["next_step"], "basic_info_form")
        self.assertEqual(started["form"]["default_values"]["current_week"], "30周")
        self.assertEqual(started["form"]["default_values"]["age"], "43")

        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(current_week="30周", age="43")},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(basic["next_step"], "personalized_followup")
        self.assertIn("我注意到你43岁", _birth_journey_suggested_topic(basic, "age_35_plus_checkup_detail")["observation"])
        rendered_basic = json.dumps(basic, ensure_ascii=False)
        self.assertNotIn("entry_concern_question", rendered_basic)
        self.assertNotIn("你刚才提到有点焦虑", rendered_basic)

        personalized = manage_birth_journey_intake(
            {
                "action": "submit_personalized_followup",
                "payload": {
                    "topic": "age_35_plus_checkup_detail",
                    "answer": "暂时没有明确异常",
                },
            },
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )
        self.assertEqual(personalized["next_step"], "checkup_records_upload")
        self.assertNotIn("personalized_followup", personalized["data"])

        skipped = manage_birth_journey_intake(
            {"action": "skip_checkup_records", "payload": {}},
            {**inputs, "_birth_journey_intake_state": personalized["intake_state"]},
        )

        self.assertEqual(skipped["status"], "in_progress")
        self.assertEqual(skipped["next_step"], "final_plan_confirmation")
        ready = _confirm_birth_journey_ready_to_generate(inputs, skipped)
        self.assertEqual(ready["status"], "ready_to_generate")
        self.assertEqual(ready["plan_context"]["entry_reason"], "我不知道接下来要准备什么")
        self.assertEqual(ready["plan_context"]["initial_concerns"], ["怕漏事", "我不知道接下来要准备什么"])
        self.assertEqual(ready["plan_context"]["top_worries"], "怕漏事, 我不知道接下来要准备什么")
        self.assertIn("age_35_plus", ready["plan_context"]["personalization_tags"])
        self.assertNotIn("risk_factors", ready["plan_context"])

        plan = create_birth_journey_plan_card(
            {"plan_context": ready["plan_context"], "scope": "full"},
            {**inputs, "_birth_journey_intake_state": ready["intake_state"]},
        )

        self.assertEqual(plan["status"], "card_created")
        rendered_plan = json.dumps(plan["card"]["card_json"], ensure_ascii=False)
        self.assertNotIn("把焦虑", rendered_plan)
        self.assertNotIn("你提到焦虑", rendered_plan)
        self.assertNotIn("考虑到你提到焦虑", rendered_plan)

    def test_birth_journey_intake_early_stage_asks_checkup_done_before_upload(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(current_week="8周", age="30")},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(basic["next_step"], "checkup_done_question")
        self.assertEqual(basic["data"]["initial_analysis"]["stage"], "early")
        self.assertIn("有没有做过产检", basic["data"]["confirmation_question"])

        done = manage_birth_journey_intake(
            {"action": "confirm_checkup_done", "payload": {}},
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )
        self.assertEqual(done["next_step"], "checkup_records_upload")
        self.assertIn("报告不在手边", done["data"]["upload_panel"]["description"])

        not_done = manage_birth_journey_intake(
            {"action": "confirm_no_checkup_yet", "payload": {}},
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )
        self.assertEqual(not_done["status"], "in_progress")
        self.assertEqual(not_done["next_step"], "final_plan_confirmation")
        self.assertEqual(not_done["intake_state"]["checkup_status"], "还没做过产检")
        ready = _confirm_birth_journey_ready_to_generate(inputs, not_done)
        self.assertEqual(ready["status"], "ready_to_generate")
        self.assertEqual(ready["next_step"], "generate_plan")
        self.assertEqual(ready["plan_context"]["checkup_status"], "还没做过产检")

    def test_birth_journey_intake_mid_stage_suggests_report_upload_directly(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload()},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(basic["next_step"], "checkup_records_upload")
        self.assertEqual(basic["data"]["initial_analysis"]["stage"], "mid")
        self.assertEqual(basic["data"]["checkup_report_strategy"]["mode"], "suggest_upload_or_skip")

    def test_birth_journey_high_age_followup_compacts_for_natural_reply(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(current_week="16周", age="36")},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        self.assertEqual(basic["next_step"], "personalized_followup")
        topic = _birth_journey_suggested_topic(basic, "age_35_plus_checkup_detail")
        self.assertEqual(topic["id"], "age_35_plus_checkup_detail")
        self.assertIn("我注意到你36岁", topic["observation"])
        self.assertIn("需要认真放进计划", topic["observation"])
        self.assertIn("高龄孕产妇", topic["key_points"][0])
        self.assertIn("胎盘情况", topic["meaning"])
        self.assertIn("血压/血糖", topic["followup_question"])
        self.assertIn("甲状腺/免疫或长期用药", topic["followup_question"])
        self.assertIn("有没有已经被提醒过或正在复查", topic["followup_question"])
        self.assertNotIn("目前最需要纳入计划", topic["followup_question"])
        self.assertNotIn("不代表一定有问题", topic["meaning"])
        self.assertNotIn("知道", topic["followup_question"])
        self.assertEqual(list(topic["reply_options"]), ["血压/血糖", "甲状腺/用药", "暂无异常"])

        compact = model_tool_output({"ok": True, "tool_name": "birth_journey_intake_manage", "result": basic})

        self.assertEqual(compact["next_step"], "personalized_followup")
        self.assertIn("personalization_context", compact)
        self.assertEqual(compact["personalization_context"]["suggested_topics"][0]["id"], "age_35_plus_checkup_detail")
        self.assertIn("observation", compact["personalization_context"]["suggested_topics"][0])
        self.assertIn("followup_question", compact["personalization_context"]["suggested_topics"][0])
        self.assertIn("reply_guidance", compact["personalization_context"]["suggested_topics"][0])
        self.assertNotIn("initial_analysis", compact)
        self.assertNotIn("checkup_report_strategy", compact)
        self.assertNotIn("personalization_tags", compact)
        self.assertIn("最多两小段", compact["final_response_instruction"])
        self.assertIn("基于 personalization_context", compact["final_response_instruction"])
        self.assertIn("个性化追问规则", compact["final_response_instruction"])
        self.assertIn("用户信息 -> 孕期管理意义 -> 计划影响 -> 一个具体追问", compact["final_response_instruction"])
        self.assertIn("key_points 或 meaning", compact["final_response_instruction"])
        self.assertIn("年龄>=35", compact["final_response_instruction"])
        self.assertIn("高龄孕产妇/产科管理范围", compact["final_response_instruction"])
        self.assertIn("合并成同一个明确问题", compact["final_response_instruction"])
        self.assertIn("submit_personalized_followup", compact["final_response_instruction"])
        self.assertIn("finish_personalized_followups", compact["final_response_instruction"])
        self.assertNotIn("再只问 personalized_followup.question", compact["final_response_instruction"])
        self.assertNotIn("active_personalized_followup_id", compact["final_response_instruction"])

        answered = manage_birth_journey_intake(
            {
                "action": "submit_personalized_followup",
                "payload": {"topic": "age_35_plus_checkup_detail", "answer": "暂时没有明确异常"},
            },
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )

        self.assertEqual(answered["next_step"], "checkup_records_upload")
        self.assertNotIn("personalized_followup", answered["data"])

    def test_birth_journey_personalized_followup_uses_specific_quick_replies(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(current_week="16周", age="36")},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        replies = birth_journey_intake_quick_reply_guidance(basic["next_step"])

        self.assertEqual(replies, [{"text": "暂无异常"}, {"text": "还不确定"}, {"text": "我补充一下"}])

    def test_birth_journey_plan_still_acknowledges_explicit_anxiety(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    entry_reason="我很焦虑，不知道接下来怎么办",
                    initial_concerns=["焦虑", "不知道接下来怎么办"],
                    top_worries="焦虑，不知道接下来怎么办",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        rendered = json.dumps(result["card"]["card_json"], ensure_ascii=False)
        self.assertIn("焦虑", rendered)

    def test_birth_journey_plan_does_not_relabel_other_emotions_as_anxiety(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    entry_reason="我有点心里没底，不知道接下来怎么办",
                    initial_concerns=["心里没底", "不知道接下来怎么办"],
                    top_worries="心里没底，不知道接下来怎么办",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        rendered = json.dumps(result["card"]["card_json"], ensure_ascii=False)
        self.assertIn("心里没底", rendered)
        self.assertNotIn("你提到焦虑", rendered)
        self.assertNotIn("把焦虑", rendered)

    def test_birth_journey_intake_skip_checkup_records_does_not_mark_uploaded(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": _basic_info_payload(current_week="30周")},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        skipped = manage_birth_journey_intake(
            {"action": "skip_checkup_records", "payload": {}},
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )

        self.assertEqual(skipped["status"], "in_progress")
        self.assertEqual(skipped["next_step"], "final_plan_confirmation")
        self.assertFalse(skipped["intake_state"]["checkup_records_uploaded"])
        ready = _confirm_birth_journey_ready_to_generate(inputs, skipped)
        self.assertEqual(ready["status"], "ready_to_generate")
        self.assertEqual(ready["plan_context"]["checkup_status"], "未上传产检记录")
        self.assertNotIn("checkup_records_uploaded", ready["plan_context"])

    def test_birth_journey_basic_info_form_omits_deferred_fields_and_marks_core_required(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}

        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        fields = started["form"]["fields"]
        labels = [field["label"] for field in fields]
        ids = [field["id"] for field in fields]
        self.assertNotIn("末次月经", labels)
        self.assertNotIn("预产期", labels)
        self.assertNotIn("last_menstrual_period", ids)
        self.assertNotIn("due_date", ids)
        self.assertIn("是否 IVF（体外受精）", labels)
        self.assertIn("是否第一胎", labels)
        self.assertIn("既往孕产情况", labels)
        self.assertIn("计划分娩方式", labels)
        self.assertIn("所在城市/国家", labels)
        self.assertIn("建档/生产医院", labels)
        self.assertIn("基础疾病或长期用药", labels)
        self.assertIn("医生特殊提醒", labels)
        self.assertNotIn("双胎/多胎类型", labels)
        self.assertNotIn("既往生产方式", labels)
        self.assertNotIn("既往剖宫产次数", labels)
        self.assertNotIn("喂养意向", labels)
        self.assertNotIn("身高", labels)
        self.assertNotIn("孕前体重", labels)
        self.assertNotIn("当前体重", labels)
        self.assertNotIn("主要支持人", labels)
        required_by_id = {field["id"]: field["required"] for field in fields}
        self.assertTrue(required_by_id["current_week"])
        self.assertTrue(required_by_id["fetus_count"])
        self.assertTrue(required_by_id["age"])
        self.assertTrue(required_by_id["ivf"])
        self.assertTrue(required_by_id["first_birth"])
        self.assertTrue(required_by_id["birth_path"])
        self.assertFalse(required_by_id["city_or_country"])
        self.assertFalse(required_by_id["birth_hospital"])
        self.assertFalse(required_by_id["medical_notes"])
        options_by_id = {field["id"]: field.get("options") for field in fields}
        self.assertEqual(options_by_id["birth_path"], ["顺产", "剖宫产", "还没确定"])
        self.assertNotIn("不确定/暂不说", options_by_id["birth_path"])
        self.assertEqual(
            started["form"]["description"],
            "先填写几项基础信息，后面我会按你的孕周、身体情况和准备状态来整理更贴合你的孕期计划。",
        )
        self.assertEqual(started["form"]["default_values"]["city_or_country"], "深圳")

        with_pregnancy_week = manage_birth_journey_intake(
            {"action": "start", "payload": {}},
            {**inputs, "user_profile": {"birth_prep_due_date_or_week": "孕25周"}},
        )
        self.assertEqual(with_pregnancy_week["form"]["default_values"]["current_week"], "孕25周")

        with_profile_defaults = manage_birth_journey_intake(
            {"action": "start", "payload": {}},
            {
                **inputs,
                "user_profile": {
                    "age": 32,
                    "birth_prep_ivf": "是",
                    "birth_prep_fetus_count": "双胎",
                    "birth_prep_multiple_pregnancy_type": "双绒双羊",
                    "birth_prep_previous_birth_method": "顺产",
                    "birth_prep_prior_birth_history": "上次产后出血",
                    "birth_prep_medical_notes": "甲状腺用药",
                    "birth_prep_doctor_notes": "胎盘低置复查",
                    "birth_prep_birth_hospital": "深圳市妇幼",
                },
            },
        )
        profile_defaults = with_profile_defaults["form"]["default_values"]
        self.assertEqual(profile_defaults["age"], "32")
        self.assertEqual(profile_defaults["ivf"], "是")
        self.assertEqual(profile_defaults["fetus_count"], "双胎")
        self.assertEqual(profile_defaults["prior_birth_history"], "上次产后出血")
        self.assertEqual(profile_defaults["medical_notes"], "甲状腺用药")
        self.assertEqual(profile_defaults["doctor_notes"], "胎盘低置复查")
        self.assertEqual(profile_defaults["birth_hospital"], "深圳市妇幼")
        self.assertNotIn("multiple_pregnancy_type", profile_defaults)
        self.assertNotIn("previous_birth_method", profile_defaults)

    def test_birth_journey_plan_accepts_unknown_birth_path_and_no_support_person(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="26周",
                    birth_path="还没确定",
                    support_person="暂时没有",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        self.assertNotIn("birth_path", card["owner"])
        self.assertEqual(card["owner"]["support_person"], "暂时没有")

    def test_birth_journey_plan_does_not_treat_personalized_facts_as_doctor_notes(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    age="36",
                    personalized_facts="age_35_plus_checkup_detail / 暂无异常 / 高龄孕期要关注血压血糖和胎儿生长",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn("你 36 岁属于高龄孕产妇管理范围", card["generation_context"]["personalization_basis"])
        self.assertNotIn("你填了医生特殊提醒", card["generation_context"]["personalization_basis"])
        self.assertNotIn("把医生提醒设成复查和观察提醒", rendered)

    def test_birth_journey_plan_ignores_negative_doctor_notes(self) -> None:
        for doctor_notes in ("无", "没有特殊提醒", "医生没说特殊"):
            with self.subTest(doctor_notes=doctor_notes):
                result = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            doctor_notes=doctor_notes,
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
                )

                self.assertEqual(result["status"], "card_created")
                card = result["card"]["card_json"]
                rendered = json.dumps(card, ensure_ascii=False)
                self.assertNotIn("你填了医生特殊提醒", card["generation_context"]["personalization_basis"])
                self.assertNotIn("把医生提醒设成复查和观察提醒", rendered)

    def test_birth_journey_plan_uses_high_impact_history_and_medical_fields(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="20周",
                    fetus_count="双胎",
                    multiple_pregnancy_type="双绒双羊",
                    first_birth="否",
                    previous_birth_method="剖宫产",
                    previous_c_section_count="1",
                    prior_birth_history="上次因为臀位剖宫产",
                    medical_notes="甲状腺用药",
                    doctor_notes="胎盘低置需要复查",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        rendered = json.dumps(card, ensure_ascii=False)
        basis_titles = card["generation_context"]["personalization_basis"]
        self.assertIn("你有既往剖宫产相关信息", basis_titles)
        self.assertIn("你填了医生特殊提醒", basis_titles)
        self.assertIn("你填了基础疾病或长期用药", basis_titles)
        self.assertIn("你填的是双胎", basis_titles)
        self.assertIn("把医生提醒设成复查和观察提醒", rendered)
        self.assertIn("剖宫产/分娩准备资料", rendered)
        self.assertIn("基础病管理", rendered)
        self.assertIn("多胎管理", rendered)
        self.assertNotIn("确认双胎/多胎类型", rendered)
        self.assertIn("设置更密集产检与胎监计划", rendered)
        self.assertIn("把医生提醒原话写下来，并标出对应的检查或观察点", rendered)
        self.assertIn("记录基础病及用药情况", rendered)
        _assert_no_legacy_birth_journey_fallback_items(self, card)
        _assert_birth_journey_item_titles_are_plain(self, card)

    def test_birth_journey_late_pregnancy_wording_avoids_aiish_pile_up_phrase(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    birth_path="顺产",
                    support_person="伴侣",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn("距离生产越来越近，提前做好准备会让临产和住院过程更顺利。", rendered)
        self.assertNotIn("铺太多", rendered)

    def test_model_tool_output_keeps_low_loss_birth_journey_plan_brief(self) -> None:
        created = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="25周",
                    age="36",
                    first_birth="是",
                    checkup_status="大排畸已做，还没预约糖耐",
                    birth_path="剖宫产",
                    birth_hospital="深圳市妇幼",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        compact = model_tool_output(
            {
                "ok": True,
                "tool_name": "birth_journey_plan_card_create",
                "result": created,
            }
        )
        self.assertIn("我也会在每项计划对应的阶段到来前，提前提醒你。", compact.get("final_response_instruction") or "")
        plan_brief = compact.get("plan_brief")
        self.assertIsInstance(plan_brief, dict)
        self.assertEqual(plan_brief.get("todo_engine_version"), "actionable_steps_v2")
        self.assertEqual(plan_brief.get("cadence"), "monthly")
        self.assertIn("住院生产", plan_brief.get("route_summary") or "")
        self.assertIn("你 36 岁属于高龄孕产妇管理范围", plan_brief.get("personalization_basis") or [])
        periods = plan_brief.get("periods") if isinstance(plan_brief, dict) else []
        self.assertIsInstance(periods, list)
        self.assertTrue(periods)
        self.assertEqual(periods[0].get("display_mode"), "expanded")
        self.assertEqual(periods[0].get("status"), "current")
        self.assertEqual(periods[-1].get("display_mode"), "terminal")
        self.assertEqual(periods[-1].get("title"), "临产与住院生产")
        first_items = periods[0].get("items") if isinstance(periods[0], dict) else []
        self.assertIsInstance(first_items, list)
        self.assertTrue(first_items)
        rendered = json.dumps(first_items, ensure_ascii=False)
        self.assertIn("priority_label", rendered)
        self.assertIn("steps", rendered)
        self.assertNotIn("done_criteria", rendered)
        self.assertIn("考虑到", rendered)
        _assert_no_birth_journey_done_criteria(self, plan_brief)

    def test_model_tool_output_compacts_birth_journey_card(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "birth_journey_plan_card_create",
            "result": {
                "status": "card_created",
                "card": {
                    "card_type": "birth_journey_plan_card",
                    "schema_version": "1.0",
                    "card_json": {
                        "title": "孕期计划",
                        "phases": [
                            {
                                "title": "孕中期",
                                "status": "current",
                                "watchouts": ["这个阶段不用把生产准备一次做完。"],
                                "actions": ["列出下次产检最想确认的 3-5 个问题。"],
                                "comate_help": ["生成产检问题清单。"],
                            }
                        ],
                        "todo_plan": {
                            "route_summary": "从孕 25 周开始，先按月推进，孕晚期改成双周，36 周后按周收口到住院生产。",
                            "periods": [
                                {
                                    "id": "period_01",
                                    "title": "孕 25-27 周",
                                    "display_mode": "expanded",
                                    "status": "current",
                                    "items": [
                                        {
                                            "id": "week_checkup",
                                            "title": "把报告里要复查的事安排上",
                                            "reason": "重要｜考虑到你已经有产检状态，先把复查和未预约项目排清楚。",
                                            "why_for_you": "考虑到你已经有产检状态，先把复查和未预约项目排清楚。",
                                            "completed": False,
                                            "priority_type": "essential",
                                            "priority_label": "重要",
                                            "steps": ["找出报告里要复查或未预约的项目"],
                                            "done_criteria": "已形成 3 个下次产检可直接问医生的问题。",
                                            "after_done_value": "做完后，下次产检会更聚焦。",
                                        },
                                        {
                                            "id": "work_break",
                                            "title": "设置久坐后的起身提醒",
                                            "reason": "建议｜考虑到你久坐上班，先把起身提醒固定下来。",
                                            "why_for_you": "考虑到你久坐上班，先把起身提醒固定下来。",
                                            "completed": False,
                                            "priority_type": "supportive",
                                            "priority_label": "建议",
                                            "steps": ["选一个 45-60 分钟提醒间隔"],
                                            "done_criteria": "已设置提醒，并试运行至少 1 个工作日。",
                                            "after_done_value": "做完后，你会更容易发现久坐和不适之间的关系。",
                                        },
                                    ],
                                }
                            ],
                        },
                        "generation_context": {
                            "route_summary": "从孕 25 周开始，先按月推进，孕晚期改成双周，36 周后按周收口到住院生产。",
                            "personalization_basis": ["当前孕周", "产检状态", "生活执行压力"],
                        },
                        "next_action": {"label": "整理产检问题", "send_text": "帮我整理下次产检要问的 3-5 个问题"},
                    },
                },
            },
        }

        compact = model_tool_output(raw)

        self.assertEqual(compact["status"], "card_created")
        self.assertEqual(compact["card"], {"card_type": "birth_journey_plan_card", "schema_version": "1.0", "created": True})
        self.assertIn("最终回复请根据下面【结构化表达素材】重新组织自然语言", compact["final_response_instruction"])
        self.assertIn("保持先说重要事项、再说建议事项的顺序", compact["final_response_instruction"])
        self.assertIn("不要逐字照读", compact["final_response_instruction"])
        self.assertIn("【结构化表达素材】", compact["final_response_instruction"])
        self.assertIn("不要出现“【重要】”“【建议】”", compact["final_response_instruction"])
        self.assertIn("不要使用“先做”“做完后”“完成标准”", compact["final_response_instruction"])
        self.assertIn("final_response_text", compact)
        self.assertIn(
            "孕期计划已生成，我同步把它做成了待办事项清单放在了“宝宝和我”页面里，接下来你可以在“宝宝和我”页面管理你的孕期计划待办事项。",
            compact["final_response_instruction"],
        )
        self.assertIn("最终回复第一句话必须原样使用", compact["final_response_instruction"])
        self.assertNotIn("计划已生成，可以在宝宝和我页面查看", compact["final_response_instruction"])
        self.assertNotIn("接下来我会按照计划主动提醒你哦", compact["final_response_instruction"])
        self.assertIn("不要提本周重点、当前优先级或当前阶段总结", compact["final_response_instruction"])
        self.assertIn("不要补充外部资料、来源引用或引用编号", compact["final_response_instruction"])
        self.assertIn("不要使用“卡片”这类界面形式词", compact["final_response_instruction"])
        self.assertIn("不要再输出“我先帮你生成”或“我整理好了”", compact["final_response_instruction"])
        self.assertNotIn("孕期计划我整理好了", compact["final_response_instruction"])
        self.assertNotIn("当前优先级：整理产检问题", compact["final_response_instruction"])
        self.assertNotIn("因为把要问医生的问题先列出来", compact["final_response_instruction"])
        self.assertIn("当前孕周、产检状态和生活执行压力", compact["final_response_instruction"])
        self.assertIn("从孕 25 周开始，先按月推进", compact["final_response_instruction"])
        self.assertIn("当前先展开孕 25-27 周", compact["final_response_instruction"])
        self.assertIn("1. 把报告里要复查的事安排上", compact["final_response_instruction"])
        self.assertIn("找出报告里要复查或未预约的项目", compact["final_response_instruction"])
        self.assertIn("2. 做久坐/久站/疲劳管理", compact["final_response_instruction"])
        self.assertIn("休息和活动会变成固定节奏", compact["final_response_instruction"])
        self.assertNotIn("done_criteria", json.dumps(compact, ensure_ascii=False))
        self.assertNotIn("已形成 3 个下次产检可直接问医生的问题", compact["final_response_instruction"])
        self.assertNotIn("已设置提醒，并试运行至少 1 个工作日", compact["final_response_instruction"])
        self.assertNotIn("1. 【重要】从产检报告圈出 3 个待确认点", compact["final_response_text"])
        self.assertNotIn("从产检报告圈出 3 个待确认点", compact["final_response_instruction"])
        self.assertNotIn("2. 【建议】设置久坐后的起身提醒", compact["final_response_text"])
        self.assertNotIn("设置久坐后的起身提醒", compact["final_response_instruction"])
        self.assertNotIn("先做：", compact["final_response_text"])
        self.assertNotIn("做完后", compact["final_response_text"])
        self.assertNotIn("给生活压力留缓冲", compact["final_response_instruction"])
        self.assertIn("如果有已经完成的事项", compact["final_response_instruction"])
        self.assertIn("这里面如果有已经完成的事项", compact["final_response_text"])
        self.assertNotIn("接下来我可以先陪你整理产检问题", compact["final_response_instruction"])
        self.assertNotIn("当前阶段是", compact["final_response_instruction"])
        self.assertNotIn("重点先留意", compact["final_response_instruction"])
        self.assertNotIn("准备动作先从这件事开始", compact["final_response_instruction"])
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("phases", json.dumps(compact, ensure_ascii=False))

    def test_model_tool_output_for_birth_journey_missing_context_only_asks_question(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "birth_journey_plan_card_create",
            "result": {
                "status": "needs_required_context",
                "summary": "生成孕期计划前，需要先完成分层信息采集。",
                "missing_fields": ["due_date_or_week", "birth_path", "support_person"],
                "data": {
                    "confirmation_question": "我先确认 3 件事再生成计划。",
                },
            },
        }

        compact = model_tool_output(raw)

        self.assertEqual(compact["status"], "needs_required_context")
        self.assertEqual(compact["missing_fields"], ["due_date_or_week", "birth_path", "support_person"])
        self.assertIn("最终回复只向用户补问", compact["final_response_instruction"])
        self.assertNotIn("card", compact)


if __name__ == "__main__":
    unittest.main()
