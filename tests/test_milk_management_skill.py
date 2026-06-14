from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MilkManagementSkillTests(unittest.TestCase):
    def test_single_skill_file_contains_core_sections(self) -> None:
        skill = (ROOT / "skills" / "milk-management" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("## 1. 奶量分析", skill)
        self.assertIn("## 2. 计划制定", skill)
        self.assertIn("## 3. 计划调整、记录和执行", skill)
        self.assertIn("## 4. 统一回复风格", skill)

    def test_core_flow_rules_remain_after_slimming(self) -> None:
        skill = (ROOT / "skills" / "milk-management" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("每轮奶量管理回复最后一句都要落到一个可执行下一步", skill)
        self.assertIn("用户补了亲喂/吸奶记录就问是否按新口径重新评估", skill)
        self.assertIn("不能停在“所以这次更像是……”这类结论句", skill)
        self.assertIn("不要把一次记录查询自动扩展成综合评估，也不要把一次分析自动扩展成计划", skill)
        self.assertIn("一轮尽量只问一个最影响下一步判断的问题", skill)
        self.assertIn("用户只回答了其中一部分", skill)
        self.assertIn("继续问未回答的那个判断点", skill)
        self.assertIn("每次回复最后一句都要给出明确下一步", skill)

    def test_reference_files_are_merged_into_skill(self) -> None:
        references_dir = ROOT / "skills" / "milk-management" / "references"

        self.assertFalse(any(references_dir.glob("*.md")))


if __name__ == "__main__":
    unittest.main()
