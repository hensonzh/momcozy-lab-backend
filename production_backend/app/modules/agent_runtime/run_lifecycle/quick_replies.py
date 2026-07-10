from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models import AgentMessage, AgentRun
from ..sdk import AgentModelRunner, SdkNodeRequest


QUICK_REPLY_FINALIZER_INSTRUCTIONS = """
你是 MomCozy App 的快捷回复生成器。
只根据输入的最近对话和 assistant_final_text，生成用户下一句可能会点击发送的快捷回复。
重点参考最后一轮用户消息和最终回复。

规则：
- 只返回 JSON，不要 Markdown，不要解释。
- JSON 格式必须是 {"replies":[{"text":"..."}]}。
- replies 必须恰好 3 条；不要返回 1 条、2 条或超过 3 条。
- 每条 text 必须像用户会说的话，简短自然，不能超过 32 个中文字符。
- 不要生成保存、提交、确认、取消、转接、替换等会绕过业务确认流程的动作。
- 不要重复助手正文，不要承诺已经执行动作。
- 如果无法生成 3 条明确、自然的下一句建议，返回 {"replies":[]}。
""".strip()


@dataclass(frozen=True)
class QuickReplyFinalizerConfig:
    max_dialogue_messages: int = 10
    max_reply_count: int = 3
    max_reply_text_chars: int = 32
    max_message_text_chars: int = 1000
    max_final_text_chars: int = 2000


class QuickReplyFinalizer:
    def __init__(
        self,
        *,
        sdk_runner: AgentModelRunner,
        config: QuickReplyFinalizerConfig | None = None,
    ) -> None:
        self.sdk_runner = sdk_runner
        self.config = config or QuickReplyFinalizerConfig()

    async def generate(
        self,
        *,
        run: AgentRun,
        messages: list[AgentMessage],
        current_message: AgentMessage,
        final_text: str,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_final_text = str(final_text or "").strip()
        if not normalized_final_text:
            return []
        payload = {
            "dialogue": _recent_dialogue_payload(
                messages=messages,
                current_message=current_message,
                max_messages=self.config.max_dialogue_messages,
                max_text_chars=self.config.max_message_text_chars,
            ),
            "assistant_final_text": normalized_final_text[: self.config.max_final_text_chars],
            "artifact_types": _artifact_types(artifacts or []),
        }
        result = await self.sdk_runner.run_reasoning(
            SdkNodeRequest(
                run_id=str(run.id),
                thread_id=str(run.thread_id),
                actor_user_id=str(run.actor_user_id),
                instructions=QUICK_REPLY_FINALIZER_INSTRUCTIONS,
                model_input=[
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    }
                ],
                prompt_version=run.prompt_version,
                trace_id=run.trace_id,
                service_skill_id=run.service_skill_id or "cozymate_service_agent",
            )
        )
        return _normalize_quick_replies(
            _json_object_from_text(result.final_text).get("replies"),
            max_count=self.config.max_reply_count,
            max_text_chars=self.config.max_reply_text_chars,
        )


def _recent_dialogue_payload(
    *,
    messages: list[AgentMessage],
    current_message: AgentMessage,
    max_messages: int,
    max_text_chars: int,
) -> list[dict[str, Any]]:
    candidates = [
        message
        for message in sorted(messages, key=lambda item: item.sequence)
        if message.sequence <= current_message.sequence and message.role in {"user", "assistant"}
    ]
    selected = candidates[-max(1, max_messages) :]
    return [
        {
            "role": message.role,
            "text": _message_text(message)[:max_text_chars],
        }
        for message in selected
        if _message_text(message)
    ]


def _message_text(message: AgentMessage) -> str:
    content = message.content if isinstance(message.content, dict) else {}
    return str(content.get("text") or "").strip()


def _artifact_types(artifacts: list[dict[str, Any]]) -> list[str]:
    artifact_types: list[str] = []
    for artifact in artifacts:
        artifact_type = str(artifact.get("artifact_type") or artifact.get("type") or "").strip()
        if artifact_type and artifact_type not in artifact_types:
            artifact_types.append(artifact_type)
    return artifact_types[:10]


def _json_object_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_quick_replies(value: Any, *, max_count: int, max_text_chars: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    replies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").strip().split())
        if not text:
            continue
        text = text[:max_text_chars]
        if text in seen:
            continue
        seen.add(text)
        replies.append({"id": f"qr_{len(replies) + 1}", "text": text})
        if len(replies) >= max_count:
            break
    return replies if len(replies) == max_count else []
