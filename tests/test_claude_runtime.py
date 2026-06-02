import types
from agent.claude_runtime import run_claude_code_turn
from agent.transports.claude_code_session import TurnResult


class _FakeSession:
    def __init__(self):
        self.session_id = "s1"
    def run_turn(self, *, user_input, system_prompt):
        r = TurnResult(final_text="answer", tool_iterations=2)
        r.projected_messages = [{"role": "assistant", "content": "answer"}]
        return r
    def close(self):
        pass


def _fake_agent():
    a = types.SimpleNamespace()
    a._claude_session = None
    a.session_cwd = "/tmp"
    a._iters_since_skill = 0
    a._skill_nudge_interval = 0
    a.valid_tool_names = set()
    a._sync_external_memory_for_turn = lambda **k: None
    a._spawn_background_review = lambda **k: None
    a._claude_build_system_prompt = lambda: "system"
    a._claude_make_session = lambda: _FakeSession()
    a._claude_turn_context = {}
    return a


def test_returns_dict_contract_and_splices_messages():
    agent = _fake_agent()
    messages = [{"role": "user", "content": "q"}]
    out = run_claude_code_turn(
        agent, user_message="q", original_user_message="q",
        messages=messages, effective_task_id="t",
    )
    assert out["final_response"] == "answer"
    assert out["completed"] is True
    assert out["api_calls"] == 1
    assert {"role": "assistant", "content": "answer"} in out["messages"]
