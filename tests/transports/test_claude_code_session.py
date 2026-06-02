import json
from agent.transports.claude_code_session import ClaudeCodeSession, TurnResult


def _fake_runner(lines, exit_code=0):
    def _run(argv, env, stdin_text, timeout=None, no_output_timeout=None):
        return (list(lines), exit_code)
    return _run


def test_fresh_turn_returns_final_text_and_captures_session(tmp_path):
    lines = [json.dumps({"type": "result", "result": "hello", "session_id": "sess-1"})]
    sess = ClaudeCodeSession(cwd=str(tmp_path), spawn_fn=_fake_runner(lines))
    res = sess.run_turn(user_input="hi", system_prompt="be nice")
    assert isinstance(res, TurnResult)
    assert res.final_text == "hello"
    assert sess.session_id == "sess-1"
    assert res.error is None


def test_second_turn_resumes_captured_session(tmp_path):
    captured_argv = {}

    def _run(argv, env, stdin_text, timeout=None, no_output_timeout=None):
        captured_argv["argv"] = argv
        return ([json.dumps({"type": "result", "result": "ok", "session_id": "sess-1"})], 0)

    sess = ClaudeCodeSession(cwd=str(tmp_path), spawn_fn=_run)
    sess.run_turn(user_input="one", system_prompt="s")
    sess.run_turn(user_input="two", system_prompt="s")
    assert "--resume" in captured_argv["argv"]
    assert "sess-1" in captured_argv["argv"]


def test_nonzero_exit_marks_error_and_retire(tmp_path):
    sess = ClaudeCodeSession(cwd=str(tmp_path), spawn_fn=_fake_runner([], exit_code=1))
    res = sess.run_turn(user_input="hi", system_prompt="s")
    assert res.error is not None
    assert res.should_retire is True
