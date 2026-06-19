from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MilkManagementSkillTests(unittest.TestCase):
    def test_single_skill_file_contains_core_sections(self) -> None:
        skill = (ROOT / "skills" / "milk-management" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("# 全局回复规则", skill)
        self.assertIn("# 服务1： 奶量分析", skill)
        self.assertIn("# 服务2：制定奶量计划", skill)
        self.assertIn("# 服务3： 计划调整、记录和执行", skill)

    def test_core_flow_rules_remain_after_slimming(self) -> None:
        skill = (ROOT / "skills" / "milk-management" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("奶量分析到计划制定采用分段工具推进", skill)
        self.assertIn("`milk_analysis_intake_manage` 维护信息采集表", skill)
        self.assertIn("自动读取/复用过去 7 天原始奶量记录和日级汇总", skill)
        self.assertIn("完整奶量分析或回答上一轮奶量分析追问时，先调用 `milk_analysis_intake_manage`", skill)
        self.assertIn("返回 `analysis_context` 后，下一步直接调用 `milk_analysis_evaluate`", skill)
        self.assertIn("不要绕过 `milk_analysis_intake_manage` 和 `milk_analysis_evaluate` 手写完整综合结论", skill)
        self.assertIn("用户已经同意开始制定计划、给出每天多/少多少 ml、做到多少 ml 或要求生成计划时，调用 `milk_plan_preview_create`", skill)
        self.assertIn("当前正在采用的奶量计划", skill)
        self.assertIn("milk_calendar_query(query_mode=\"current_plan\")", skill)
        self.assertIn("`plan_context` 为准", skill)
        self.assertIn("本轮只追问最影响判断的 1 个问题", skill)
        self.assertIn("用户只回答部分问题时", skill)
        self.assertIn("继续问未回答且仍影响判断的点", skill)
        self.assertIn("回复结尾必须给出明确的下一步行动", skill)

    def test_reference_files_are_merged_into_skill(self) -> None:
        references_dir = ROOT / "skills" / "milk-management" / "references"

        self.assertFalse(any(references_dir.glob("*.md")))


if __name__ == "__main__":
    unittest.main()
