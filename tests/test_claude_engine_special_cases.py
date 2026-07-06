import inspect
import agent.background_review as br
import agent.agent_init as ai


def test_background_review_treats_claude_like_codex():
    src = inspect.getsource(br)
    assert "codex_app_server" in src
    assert "claude_code_cli" in src


def test_agent_init_skips_client_build_for_claude_code_cli():
    """The claude_code_cli engine hands the turn to a local `claude` subprocess
    and has no provider credentials, so agent_init must branch on it and skip
    the LLM-client construction path — otherwise the no-API-key guard rejects
    it before the engine ever dispatches (regression guard for the live E2E
    fix). Unlike codex_app_server, whose provider always resolves a client,
    claude_code_cli sets client=None with no key."""
    src = inspect.getsource(ai)
    assert 'agent.api_mode == "claude_code_cli"' in src
    # The branch must null the client and never require a key.
    branch = src.split('agent.api_mode == "claude_code_cli"', 1)[1][:1200]
    assert "agent.client = None" in branch
