import types
import agent.conversation_loop as cl


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
