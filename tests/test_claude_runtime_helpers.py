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
