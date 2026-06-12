from __future__ import annotations

import unittest
from pathlib import Path

from momcozy_agent.skills import list_skill_manifests, load_skill
from momcozy_agent.tool_schemas import FUNCTION_TOOLS


ROOT = Path(__file__).resolve().parents[1]


class HealthConsultationSkillTests(unittest.TestCase):
    def test_health_consultation_skill_is_registered_and_loadable(self) -> None:
        manifests = {str(skill["id"]): skill for skill in list_skill_manifests()}

        self.assertIn("health-consultation", manifests)
        description = str(manifests["health-consultation"]["description"])
        self.assertIn("覆盖所有孕期、产后、宝宝及哺乳相关健康问题", description)

        loaded = load_skill("health-consultation")
        self.assertEqual(loaded["id"], "health-consultation")
        self.assertIn("# 健康咨询", loaded["skill_md"])
        self.assertIn("## 智能体回复方式", loaded["skill_md"])
        self.assertIn("## 什么情况下建议找 IBCLC 顾问", loaded["skill_md"])
        self.assertIn("## 什么情况下建议找医生", loaded["skill_md"])

    def test_load_skill_schema_allows_health_consultation(self) -> None:
        enum = FUNCTION_TOOLS["load_skill"]["parameters"]["properties"]["skill_id"]["enum"]

        self.assertIn("health-consultation", enum)

    def test_existing_service_skills_delegate_health_consultation_boundaries(self) -> None:
        birth_prep = (ROOT / "skills" / "birth-prep" / "SKILL.md").read_text(encoding="utf-8")
        milk_management = (ROOT / "skills" / "milk-management" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("普通孕期身体不适", birth_prep)
        self.assertIn("优先加载 `health-consultation`", birth_prep)
        self.assertIn("不要在 birth-prep 里展开普通健康咨询", birth_prep)

        self.assertIn("健康判断，优先使用 health-consultation", milk_management)
        self.assertIn("优先加载 `health-consultation`", milk_management)
        self.assertIn("不要在 milk-management 里把医疗判断包装成奶量结论", milk_management)

    def test_health_consultation_keeps_medical_and_image_boundaries(self) -> None:
        skill = (ROOT / "skills" / "health-consultation" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("不下诊断", skill)
        self.assertIn("不根据图片直接判断感染、皮疹、伤口、乳房、乳头、恶露、宝宝肤色或精神状态", skill)
        self.assertIn("不调整处方、剂量或停药", skill)
        self.assertIn("不承诺妈妈或宝宝一定没事", skill)

    def test_breast_lump_and_pain_must_triage_before_advice(self) -> None:
        health_skill = (ROOT / "skills" / "health-consultation" / "SKILL.md").read_text(encoding="utf-8")
        milk_skill = (ROOT / "skills" / "milk-management" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("乳房硬块、疼痛、堵奶这类问题，先按这个节奏来", health_skill)
        self.assertIn("我先确认两个要紧的情况", health_skill)
        self.assertIn("乳房一片红、热、痛吗", health_skill)
        self.assertIn("硬块有没有变大或越来越痛", health_skill)
        self.assertIn("用户没回答前", health_skill)
        self.assertIn("先给她能立刻做的事", health_skill)
        self.assertIn("不要把 IBCLC 当成默认下一步", health_skill)

        self.assertIn("乳房硬块、硬块疼、乳房红肿、堵奶或吸奶痛这类问题，先确认几个要紧情况", milk_skill)
        self.assertIn("不要直接给冷敷、按摩、排乳、吸奶频率、资料引用或 IBCLC 入口推荐", milk_skill)

    def test_health_consultation_prioritizes_action_before_referral(self) -> None:
        health_skill = (ROOT / "skills" / "health-consultation" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("健康咨询的目标很简单", health_skill)
        self.assertIn("优先给低风险、可执行的观察、缓解和记录建议", health_skill)
        self.assertIn("持续多久、有没有变严重、已经试过什么", health_skill)
        self.assertIn("不要默认问年龄、既往病史、过敏史", health_skill)
        self.assertIn("给完建议后，要留一个跟进点", health_skill)
        self.assertIn("你晚点把变化告诉我，我再陪你一起看下一步", health_skill)
        self.assertIn("医生是用来处理身体状态明显不对", health_skill)
        self.assertIn("不要把医生当成普通健康咨询的默认下一步", health_skill)

    def test_ibclc_consultation_flow_has_warm_pre_and_post_trigger_rules(self) -> None:
        health_skill = (ROOT / "skills" / "health-consultation" / "SKILL.md").read_text(encoding="utf-8")
        milk_skill = (ROOT / "skills" / "milk-management" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("## 什么情况下建议找 IBCLC 顾问", health_skill)
        self.assertIn("IBCLC 是喂养和排乳细节支持", health_skill)
        self.assertIn("主动引导用户咨询 IBCLC", health_skill)
        self.assertIn("用户明确想找真人支持", health_skill)
        self.assertIn("问题和喂养动作有关", health_skill)
        self.assertIn("问题和排乳/泵奶节奏有关", health_skill)
        self.assertIn("问题和宝宝摄入细节有关", health_skill)
        self.assertIn("基础建议已经试过，但还是不清楚或反复出现", health_skill)
        self.assertIn("喂养动作、排乳方式、泵奶节奏或宝宝摄入细节", health_skill)
        self.assertIn("怎么提", health_skill)
        self.assertIn("先完成必要的基础问诊和当前建议", health_skill)
        self.assertIn("一旦命中上面的情况，就主动引导", health_skill)
        self.assertIn("更适合让 IBCLC 顾问接着看含乳、排乳和喂养节奏", health_skill)
        self.assertIn("用户明确同意后", health_skill)
        self.assertIn("卡片出现后", health_skill)
        self.assertIn("不用一个人反复猜", health_skill)
        self.assertIn("不承诺已经预约、已经接通、顾问正在处理", health_skill)

        self.assertIn("IBCLC 前后要有服务感", milk_skill)
        self.assertIn("先由 CoMate 完成基础问诊和可执行建议", milk_skill)
        self.assertIn("如果你想把含乳、排乳和宝宝摄入信号一起看细一点", milk_skill)
        self.assertIn("用户继续确认要进入时，再打开入口", milk_skill)
