from agent.transports.claude_code_stream import parse_claude_stream


def _lines(*objs):
    import json
    return [json.dumps(o) for o in objs]


def test_parses_final_text_session_id_and_usage():
    out = parse_claude_stream(_lines(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
        {"type": "result", "result": "hello world", "session_id": "abc",
         "usage": {"input_tokens": 10, "output_tokens": 3}},
    ))
    assert out.final_text == "hello world"
    assert out.session_id == "abc"
    assert out.usage == {"input_tokens": 10, "output_tokens": 3}
    assert out.error is None


def test_empty_result_after_tools_is_not_failure_and_keeps_session_id():
    out = parse_claude_stream(_lines(
        {"type": "result", "result": "", "session_id": "xyz"},
    ))
    assert out.final_text == ""
    assert out.session_id == "xyz"
    assert out.error is None


def test_error_event_sets_error():
    out = parse_claude_stream(_lines(
        {"type": "result", "subtype": "error_during_execution", "is_error": True,
         "result": "boom", "session_id": "s"},
    ))
    assert out.error is not None
    assert out.session_id == "s"


def test_tolerates_malformed_lines():
    out = parse_claude_stream(["{not json", '{"type":"result","result":"ok","session_id":"s"}'])
    assert out.final_text == "ok"


def test_projects_tool_use_rows():
    out = parse_claude_stream(_lines(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__hermes-tools__web_search",
             "input": {"q": "x"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "results"}]}},
        {"type": "result", "result": "done", "session_id": "s"},
    ))
    roles = [m["role"] for m in out.projected_messages]
    assert "assistant" in roles and "tool" in roles


def test_parse_cap_truncation_sets_error():
    big = '{"type":"assistant","message":{"content":[{"type":"text","text":"%s"}]}}' % ("x" * 60000)
    lines = [big] * 25  # > 1 MB total, no result line
    out = parse_claude_stream(lines)
    assert out.error is not None
