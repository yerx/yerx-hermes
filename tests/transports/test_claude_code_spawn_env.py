from agent.transports.claude_code_spawn import build_spawn_env


def test_clears_auth_vars_but_preserves_hermes_context():
    base = {
        "ANTHROPIC_API_KEY": "sk-secret",
        "CLAUDE_CODE_OAUTH_TOKEN": "tok",
        "CLAUDE_CONFIG_DIR": "/x",
        "HERMES_SESSION_ID": "s1",
        "PATH": "/usr/bin",
    }
    env = build_spawn_env(
        base=base,
        context_env={"HERMES_ENABLED_TOOLSETS": "web"},
    )
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "CLAUDE_CONFIG_DIR" not in env
    assert env["HERMES_SESSION_ID"] == "s1"   # preserved
    assert env["PATH"] == "/usr/bin"          # preserved
    assert env["HERMES_ENABLED_TOOLSETS"] == "web"  # injected
