from agent.transports.claude_code_spawn import build_claude_argv
from agent.transports import claude_code_constants as c


def _base_kwargs(tmp_path):
    return dict(
        model="claude-opus-4-8",
        mcp_config_path=str(tmp_path / "mcp.json"),
        system_prompt_path=str(tmp_path / "sys.txt"),
        permission_mode="bypassPermissions",
    )


def test_fresh_turn_argv(tmp_path):
    argv = build_claude_argv(session_id="uuid-1", resume=False, **_base_kwargs(tmp_path))
    assert argv[0] == c.CLAUDE_BIN
    assert "-p" in argv
    assert "--output-format" in argv and "stream-json" in argv
    i = argv.index("--allowedTools")
    assert argv[i + 1] == "mcp__hermes-tools__*"
    assert "--strict-mcp-config" in argv
    assert "--permission-mode" in argv
    assert "--session-id" in argv and "uuid-1" in argv
    assert "--resume" not in argv
    assert "--include-partial-messages" not in argv


def test_resume_turn_uses_resume_not_session_id(tmp_path):
    argv = build_claude_argv(session_id="uuid-1", resume=True, **_base_kwargs(tmp_path))
    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "uuid-1"
    assert "--session-id" not in argv
    assert "--append-system-prompt-file" in argv
