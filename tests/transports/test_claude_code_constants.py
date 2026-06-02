import agent.transports.claude_code_constants as c


def test_mcp_server_name_matches_fastmcp_server():
    from agent.transports import hermes_tools_mcp_server as bridge  # noqa: F401
    assert c.MCP_SERVER_NAME == "hermes-tools"
    assert c.ALLOWED_TOOLS_GLOB == "mcp__hermes-tools__*"
    assert c.ALLOWED_TOOLS_GLOB == f"mcp__{c.MCP_SERVER_NAME}__*"


def test_clear_env_is_enumerated_denylist_not_wildcard():
    assert "ANTHROPIC_API_KEY" in c.CLEAR_ENV
    assert "ANTHROPIC_AUTH_TOKEN" in c.CLEAR_ENV
    assert "CLAUDE_CODE_OAUTH_TOKEN" in c.CLEAR_ENV
    assert "CLAUDE_CONFIG_DIR" in c.CLEAR_ENV
    assert all(not name.endswith("*") for name in c.CLEAR_ENV)
    assert not any(name.startswith("HERMES_") for name in c.CLEAR_ENV)


def test_permission_mode_default_is_bypass():
    assert c.resolve_permission_mode(None) == "bypassPermissions"
    assert c.resolve_permission_mode("unrestricted") == "bypassPermissions"
    assert c.resolve_permission_mode("auto") == "bypassPermissions"
    assert c.resolve_permission_mode("approval-required") == "plan"


def test_permission_mode_explicit_override_wins():
    assert c.resolve_permission_mode("auto", explicit="plan") == "plan"
    assert c.resolve_permission_mode("auto", explicit="garbage") == "bypassPermissions"


def test_session_id_fields_order():
    assert c.SESSION_ID_FIELDS[0] == "session_id"
    assert "conversation_id" in c.SESSION_ID_FIELDS
