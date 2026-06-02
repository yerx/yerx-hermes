import inspect
import run_agent


def test_aiagent_has_claude_helpers():
    for name in (
        "_run_claude_code_turn",
        "_claude_make_session",
        "_claude_build_system_prompt",
    ):
        assert hasattr(run_agent.AIAgent, name), f"missing {name}"


def test_forwarder_signature_matches_runtime():
    sig = inspect.signature(run_agent.AIAgent._run_claude_code_turn)
    params = set(sig.parameters)
    assert {"user_message", "original_user_message", "messages",
            "effective_task_id", "should_review_memory"} <= params


import types


def _ctx(agent, task_id=None):
    # Bind the unbound method to a lightweight stand-in carrying just the
    # attributes _claude_turn_context reads.
    return run_agent.AIAgent._claude_turn_context(agent, task_id=task_id)


def test_turn_context_includes_task_id_when_provided():
    """BL5: HERMES_TASK_ID must be threaded into the per-turn context env so
    MCP-bridged terminal/browser tools keep per-session isolation."""
    agent = types.SimpleNamespace(
        enabled_toolsets=None, disabled_toolsets=None, session_id="s1",
    )
    ctx = _ctx(agent, task_id="task-42")
    assert ctx.get("HERMES_TASK_ID") == "task-42"


def test_turn_context_omits_task_id_when_absent():
    agent = types.SimpleNamespace(
        enabled_toolsets=None, disabled_toolsets=None, session_id="s1",
    )
    ctx = _ctx(agent, task_id=None)
    assert "HERMES_TASK_ID" not in ctx


def test_make_session_threads_task_id_into_context_env():
    """_claude_make_session(task_id=...) must propagate HERMES_TASK_ID onto the
    created session's context_env."""

    class _Agent:
        enabled_toolsets = None
        disabled_toolsets = None
        session_id = "s1"
        session_cwd = None
        model = "anthropic/claude-sonnet-4.6"
        _claude_make_session = run_agent.AIAgent._claude_make_session
        _claude_turn_context = run_agent.AIAgent._claude_turn_context

    agent = _Agent()
    session = agent._claude_make_session(task_id="task-99")
    try:
        assert session._context_env.get("HERMES_TASK_ID") == "task-99"
    finally:
        try:
            session.close()
        except Exception:
            pass
