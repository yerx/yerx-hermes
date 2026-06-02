import types

import pytest

import agent.conversation_loop as cl
from agent.transports.claude_code_spawn import UntrustedContextError


def test_claude_code_cli_is_a_valid_api_mode():
    from agent import agent_init
    import inspect
    src = inspect.getsource(agent_init)
    assert '"claude_code_cli"' in src


def test_dispatch_calls_run_claude_code_turn():
    called = {}

    agent = types.SimpleNamespace()
    agent.api_mode = "claude_code_cli"
    agent._execution_context = None

    def fake_turn(**kwargs):
        called.update(kwargs)
        return {"final_response": "ok", "messages": kwargs["messages"],
                "api_calls": 1, "completed": True, "partial": False, "error": None}

    agent._run_claude_code_turn = fake_turn
    agent._run_codex_app_server_turn = lambda **k: {}

    out = cl._maybe_dispatch_cli_runtime(
        agent, user_message="q", original_user_message="q",
        messages=[{"role": "user", "content": "q"}],
        effective_task_id="t", should_review_memory=False,
    )
    assert out is not None
    assert out["final_response"] == "ok"
    assert called["user_message"] == "q"


def test_dispatch_returns_none_for_normal_api_mode():
    agent = types.SimpleNamespace()
    agent.api_mode = "chat_completions"
    out = cl._maybe_dispatch_cli_runtime(
        agent, user_message="q", original_user_message="q",
        messages=[], effective_task_id="t", should_review_memory=False,
    )
    assert out is None


def _dispatch(agent):
    return cl._maybe_dispatch_cli_runtime(
        agent, user_message="q", original_user_message="q",
        messages=[{"role": "user", "content": "q"}],
        effective_task_id="t", should_review_memory=False,
    )


def test_dispatch_refuses_delegated_subagent():
    """BL6: a delegate_task child carries _parent_session_id and must be
    refused by the trusted-context gate."""
    agent = types.SimpleNamespace()
    agent.api_mode = "claude_code_cli"
    agent._parent_session_id = "p1"
    agent._run_claude_code_turn = lambda **k: {"final_response": "ok"}
    agent._run_codex_app_server_turn = lambda **k: {}
    with pytest.raises(UntrustedContextError):
        _dispatch(agent)


def test_dispatch_refuses_subagent_id_marker():
    """A delegate child also carries _subagent_id; refuse on that too."""
    agent = types.SimpleNamespace()
    agent.api_mode = "claude_code_cli"
    agent._subagent_id = "sa-0-abc"
    agent._run_claude_code_turn = lambda **k: {"final_response": "ok"}
    agent._run_codex_app_server_turn = lambda **k: {}
    with pytest.raises(UntrustedContextError):
        _dispatch(agent)


def test_dispatch_refuses_gateway_sourced_turn():
    """BL6: a gateway/channel-driven turn carries _gateway_session_key and a
    non-cli platform; must be refused."""
    agent = types.SimpleNamespace()
    agent.api_mode = "claude_code_cli"
    agent._gateway_session_key = "agent:main:telegram:dm:123"
    agent.platform = "telegram"
    agent._run_claude_code_turn = lambda **k: {"final_response": "ok"}
    agent._run_codex_app_server_turn = lambda **k: {}
    with pytest.raises(UntrustedContextError):
        _dispatch(agent)


def test_dispatch_refuses_channel_platform_without_session_key():
    """A non-cli platform alone signals a channel turn."""
    agent = types.SimpleNamespace()
    agent.api_mode = "claude_code_cli"
    agent.platform = "discord"
    agent._run_claude_code_turn = lambda **k: {"final_response": "ok"}
    agent._run_codex_app_server_turn = lambda **k: {}
    with pytest.raises(UntrustedContextError):
        _dispatch(agent)


def test_dispatch_allows_local_cli_platform():
    """A genuine local interactive CLI session (platform='cli', no parent /
    gateway markers) must still be allowed."""
    called = {}
    agent = types.SimpleNamespace()
    agent.api_mode = "claude_code_cli"
    agent.platform = "cli"

    def fake_turn(**kwargs):
        called.update(kwargs)
        return {"final_response": "ok"}

    agent._run_claude_code_turn = fake_turn
    agent._run_codex_app_server_turn = lambda **k: {}
    out = _dispatch(agent)
    assert out["final_response"] == "ok"
    assert called["user_message"] == "q"


def test_dispatch_explicit_execution_context_overrides():
    """An explicit agent._execution_context is honoured (merged) so future
    callers can force-refuse or force-allow."""
    agent = types.SimpleNamespace()
    agent.api_mode = "claude_code_cli"
    agent.platform = "cli"
    agent._execution_context = {"source": "remote_trigger"}
    agent._run_claude_code_turn = lambda **k: {"final_response": "ok"}
    agent._run_codex_app_server_turn = lambda **k: {}
    with pytest.raises(UntrustedContextError):
        _dispatch(agent)
