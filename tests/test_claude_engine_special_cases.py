import inspect
import agent.background_review as br


def test_background_review_treats_claude_like_codex():
    src = inspect.getsource(br)
    assert "codex_app_server" in src
    assert "claude_code_cli" in src
