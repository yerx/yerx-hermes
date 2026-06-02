import agent.transports.hermes_tools_mcp_server as bridge


def test_dispatch_passes_context_from_env(monkeypatch):
    monkeypatch.setenv("HERMES_ENABLED_TOOLSETS", "web,browser")
    monkeypatch.setenv("HERMES_SESSION_ID", "s1")
    monkeypatch.setenv("HERMES_TASK_ID", "t1")

    ctx = bridge._read_context_from_env()
    assert ctx["enabled_toolsets"] == ["web", "browser"]
    assert ctx["session_id"] == "s1"
    assert ctx["task_id"] == "t1"


def test_read_context_empty_when_unset(monkeypatch):
    for var in ("HERMES_ENABLED_TOOLSETS", "HERMES_DISABLED_TOOLSETS",
                "HERMES_TASK_ID", "HERMES_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    ctx = bridge._read_context_from_env()
    assert ctx["enabled_toolsets"] is None
    assert ctx["task_id"] is None
    assert ctx["session_id"] is None
