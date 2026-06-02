import shutil
import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not on PATH (live test)",
)


def test_single_turn_round_trip(tmp_path):
    from agent.transports.claude_code_session import ClaudeCodeSession
    sess = ClaudeCodeSession(cwd=str(tmp_path))
    res = sess.run_turn(
        user_input="Reply with exactly the word: pong",
        system_prompt="You are a test harness. Follow instructions exactly.",
    )
    assert res.error is None, res.error
    assert "pong" in res.final_text.lower()
    assert sess.session_id  # captured for resume
