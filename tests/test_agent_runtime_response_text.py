from app.modules.agent_runtime.response_text import AppendOnlyAgentResponseProjector, sanitize_agent_response_text


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


def test_sanitize_agent_response_text_removes_raw_web_search_citation_markers() -> None:
    result = sanitize_agent_response_text("参考 \ue200cite\ue202turn0search0\ue201 专业资料【1†source】，先联系医生。")

    assert result.text == "参考 专业资料，先联系医生。"
    assert result.quick_replies == []


def test_append_only_projector_never_emits_partial_web_search_citation_marker() -> None:
    projector = AppendOnlyAgentResponseProjector()

    assert projector.push("先观察。\ue200cite\ue202turn0") == "先观察。"
    assert projector.push("search0\ue201 再联系医生。") == " 再联系医生。"
    assert projector.text == "先观察。 再联系医生。"


def test_append_only_projector_hides_partial_tool_json_after_visible_text() -> None:
    projector = AppendOnlyAgentResponseProjector()

    assert projector.push('我先帮你看一下。\n{"service_skill_id":') == "我先帮你看一下。"
    assert projector.push('"milk-management","status":"service_skill_loaded"}') == ""
    assert projector.finalize() == ""
    assert projector.text == "我先帮你看一下。"


def test_append_only_projector_releases_completed_non_tool_json_without_rewriting_prefix() -> None:
    projector = AppendOnlyAgentResponseProjector()

    assert projector.push('参考这个列表：["补') == "参考这个列表："
    assert projector.push('水","休息"]') == '["补水","休息"]'
    assert projector.finalize() == ""
    assert projector.text == '参考这个列表：["补水","休息"]'


def test_append_only_projector_ignores_brackets_inside_json_strings() -> None:
    projector = AppendOnlyAgentResponseProjector()

    assert projector.push('说明：{"message":"[literal') == "说明："
    assert projector.push(']"}') == '{"message":"[literal]"}'
    assert projector.text == '说明：{"message":"[literal]"}'


def test_append_only_projector_drops_unclosed_structured_tail_when_finalized() -> None:
    projector = AppendOnlyAgentResponseProjector()

    assert projector.push('我先帮你看一下。\n{"service_skill_id":') == "我先帮你看一下。"
    assert projector.finalize() == ""
    assert projector.text == "我先帮你看一下。"


def test_append_only_projector_suppresses_chunked_thinking_before_visible_text() -> None:
    projector = AppendOnlyAgentResponseProjector()

    assert projector.push("<thi") == ""
    assert projector.push("nk>hidden") == ""
    assert projector.push("</think>\n\n可") == "可"
    assert projector.push("见回复") == "见回复"
    assert projector.finalize() == ""
    assert projector.text == "可见回复"
