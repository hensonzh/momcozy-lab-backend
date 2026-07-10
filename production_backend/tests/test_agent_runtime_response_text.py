from production_backend.app.modules.agent_runtime.response_text import sanitize_agent_response_text


def test_sanitize_agent_response_text_removes_tool_output_json() -> None:
    result = sanitize_agent_response_text('{"profile":{"display_name":"henson"}}')

    assert result.text == ""
    assert result.quick_replies == []


def test_sanitize_agent_response_text_removes_service_skill_json_after_text() -> None:
    result = sanitize_agent_response_text('我先帮你看一下。\n{"service_skill_id":"milk-management","status":"service_skill_loaded"}')

    assert result.text == "我先帮你看一下。"
    assert result.quick_replies == []


def test_sanitize_agent_response_text_removes_quick_replies_json_block_without_extracting_ui_state() -> None:
    result = sanitize_agent_response_text(
        """
已经整理好了。
```json
{"quick_replies":[{"text":"继续聊这个"},{"text":"给我更多细节"},{"text":"换个方向"}]}
```
"""
    )

    assert result.text == "已经整理好了。"
    assert result.quick_replies == []


def test_sanitize_agent_response_text_suppresses_partial_structured_json() -> None:
    result = sanitize_agent_response_text('{"quick_replies":[{"text":"继续')

    assert result.text == ""
    assert result.quick_replies == []


def test_sanitize_agent_response_text_keeps_non_tool_json_content() -> None:
    result = sanitize_agent_response_text('参考这个列表：["补水","休息"]')

    assert result.text == '参考这个列表：["补水","休息"]'
    assert result.quick_replies == []
