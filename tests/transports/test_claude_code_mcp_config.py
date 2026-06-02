import json
from agent.transports.claude_code_spawn import build_mcp_config_file


def test_mcp_config_points_at_hermes_tools_server(tmp_path):
    path = build_mcp_config_file(tmp_dir=str(tmp_path))
    data = json.loads(open(path).read())
    server = data["mcpServers"]["hermes-tools"]
    assert server["command"]  # resolved python interpreter
    assert server["args"] == ["-m", "agent.transports.hermes_tools_mcp_server"]


def test_mcp_config_carries_per_turn_context_env(tmp_path):
    path = build_mcp_config_file(
        tmp_dir=str(tmp_path),
        context_env={"HERMES_ENABLED_TOOLSETS": "web,browser", "HERMES_SESSION_ID": "s1"},
    )
    data = json.loads(open(path).read())
    env = data["mcpServers"]["hermes-tools"]["env"]
    assert env["HERMES_ENABLED_TOOLSETS"] == "web,browser"
    assert env["HERMES_SESSION_ID"] == "s1"
