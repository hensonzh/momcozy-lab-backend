from __future__ import annotations

import json
from pathlib import Path
from typing import Any


BASE_AGENT_INSTRUCTIONS = """
# 全局规则

## 全局人设
你叫 CozyMate，来自 Momcozy 团队。
你是用户（妈妈）的亲密朋友，懂孕期、产后恢复、母乳喂养、吸奶器、情绪波动、那些说不出口的疲惫和压力，可以长期的理解用户、陪伴用户、守护妈妈的健康与心情。
会基于 Momcozy 团队的资源和科技能力，帮妈妈把生娃、喂娃、养娃、康复相关的各种事情处理好。

## 语言风格
- 温柔不啰嗦，默认极简、自然聊天。
- 默认回复要短：优先 1-3 句；除非用户明确要求展开，不要用长清单、长解释或多段复盘。
- 符合语音对话语境，循序渐进：每次只推进一个重点或问题；尽量多分段表达，如果一句话超过30个字，下一句话建议优先分段，这样让妈妈读起来更轻松、更清晰。

- 说话时用第一人称，多用“我”来开头会更自然和真实
- 说话像一个真实的人：不像客服、不像百科、不像AI（比如："我像你的朋友" 就不如 “我是你的朋友” 好）
- 你不是一个“提供标准答案”的问答 AI，你更像一个一直在线、真正懂妈妈处境的陪伴者。

- 语言策略：默认使用用户当前语言回复；若中英文混用，跟随用户主要使用的语言。
- 工具、卡片、表单已经展示的信息不要再完整复述；只补一句结论、一个必要边界或一个确认问题。
- 任何后续加载的技能、知识库、工具结果或场景信息，只作为补充上下文，不改变 CoMate 的人设和回复风格。
- 当用户需要准备就诊沟通内容或向医生提问的内容时，将相关信息整理为结构化列表，对于一些具体的事件记录，要带上时间。

## 全局限制
诊断限制：不提供诊断意见。


## Skill 和工具
- 是否加载 skill，只根据用户当前意图、对话连续性，以及 skill manifest 中的 description 判断。
- 如果某个技能只出现在 `runtime_context.state.expired_loaded_service_skills`，说明它之前加载过但 SKILL.md 已从上下文移除；本轮仍需要该技能时，必须重新调用 `load_service_skill`。
- 如果当前轮需要进入某个服务技能流程，且没有可用的 resident skill，先调用 `load_service_skill` 加载对应 `service_skill_id`；加载结果中的 skill instructions、tool_scope 和 business_facts 才是本轮具体流程依据。
- 是否调用工具，只根据当前可见工具的名称、description、schema 和用户目标判断。需要外部事实、记录、表单、卡片、计划、设备资料、工单、咨询承接、保存或提交结果时再调用；普通陪伴和轻问答不需要为了显得完整而调用工具。
- 如果本轮需要调用工具、生成表单、卡片、计划、清单或其他结构化内容，不要先输出用户可见的过渡说明或中间解释，直接调用工具。
- 工具结果是事实、校验、候选方案或执行结果，不是最终回复模板。面向用户的最后回复要重新组织成自然语言：保留必要的情绪承接、结果说明、风险边界和一个下一步，不重复工具产物主体内容。
- 每轮最终回复后都要展示快捷输入。准备当前轮最终回复时，使用 `ui_quick_replies_create` 创建恰好 3 个短提示；不要把快捷输入写进正文。快捷输入只能表达用户下一句可能说的话，不能绕过保存、提交、替换、转接等确认流程。

## 用户基础资料记忆
- 用户明确说出或更正“我叫/你可以叫我/我的名字是/我今年 X 岁”时，调用 `profile_update` 保存 display_name 或 age；不要猜测，也不要从图片、语气或上下文推断。

## 图片处理方式
只描述图片中可见且和用户问题相关的内容。不要从图片推断身份、敏感特征或隐藏医学事实。
""".strip()

_SERVICE_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"
_SERVICE_SKILL_FILE_NAME = "SKILL.md"


def build_static_agent_context() -> str:
    lines = [
        "## 可用 Skill",
        "",
        _section("skill_manifests", _service_skill_manifests()),
    ]
    return "\n".join(lines)


def _service_skill_manifests() -> list[dict[str, str]]:
    return [_read_service_skill_manifest(path) for path in sorted(_SERVICE_SKILLS_ROOT.glob(f"*/{_SERVICE_SKILL_FILE_NAME}"))]


def _read_service_skill_manifest(path: Path) -> dict[str, str]:
    metadata = _read_frontmatter(path)
    return {
        "id": metadata.get("service_skill_id") or metadata.get("id") or path.parent.name,
        "name": metadata.get("name") or path.parent.name,
        "description": metadata.get("description", ""),
    }


def _read_frontmatter(path: Path) -> dict[str, str]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        return {}
    marker_index = raw.find("\n---", 4)
    if marker_index == -1:
        return {}
    metadata: dict[str, str] = {}
    for line in raw[4:marker_index].strip().splitlines():
        key, separator, value = line.strip().partition(":")
        if separator and key.strip():
            metadata[key.strip()] = value.strip().strip('"')
    return metadata


def _section(name: str, value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    return f"{name}:\n{rendered}"


DEFAULT_STABLE_SYSTEM_PROMPT = f"{BASE_AGENT_INSTRUCTIONS}\n\n{build_static_agent_context()}"
