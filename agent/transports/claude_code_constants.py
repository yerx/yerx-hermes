"""Constants and pure mappings for the claude_code_cli engine.

Single source of truth for the MCP server name (so the allowedTools glob
can never drift from the FastMCP server name — see BL2), the auth-var
denylist (BL4), model aliases, and permission-mode resolution (BL6).
"""

from __future__ import annotations

from typing import Optional

# The FastMCP server name in hermes_tools_mcp_server.py is FastMCP("hermes-tools").
# Claude namespaces MCP tools as mcp__<server>__<tool>, so the allowlist glob
# MUST be derived from this exact name.
MCP_SERVER_NAME = "hermes-tools"
ALLOWED_TOOLS_GLOB = f"mcp__{MCP_SERVER_NAME}__*"

CLAUDE_BIN = "claude"
DEFAULT_MODEL = "claude-opus-4-8"

MODEL_ALIASES = {
    "opus": "opus",
    "sonnet": "sonnet",
    "haiku": "haiku",
    "opus-4.8": "claude-opus-4-8",
    "opus-4.6": "claude-opus-4-6",
    "sonnet-4.6": "claude-sonnet-4-6",
}

# Fields in the stream-json output that may carry Claude's session id.
SESSION_ID_FIELDS = ("session_id", "sessionId", "conversation_id", "conversationId")

# BL4: enumerated auth-var denylist, ported from OpenClaw cli-shared.ts:20.
# NEVER a wildcard — a blanket ANTHROPIC_*/CLAUDE_CODE_* clear would wipe the
# HERMES_* per-turn context env the MCP bridge reads (BL5).
CLEAR_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY_OLD",
    "ANTHROPIC_API_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_OAUTH_TOKEN",
    "ANTHROPIC_UNIX_SOCKET",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_REMOTE",
)

_VALID_PERMISSION_MODES = ("bypassPermissions", "acceptEdits", "plan", "default")

# Hermes tools.terminal.security_mode -> claude --permission-mode.
# Only bypassPermissions (run) and plan (read-only) work in headless -p mode.
_SECURITY_MODE_TO_PERMISSION = {
    "unrestricted": "bypassPermissions",
    "yolo": "bypassPermissions",
    "auto": "bypassPermissions",
    "approval-required": "plan",
}


def resolve_permission_mode(
    security_mode: Optional[str],
    *,
    explicit: Optional[str] = None,
) -> str:
    """Resolve the --permission-mode value. Default: bypassPermissions.

    Precedence: explicit (if a valid mode) > security_mode mapping > default.
    """
    if explicit in _VALID_PERMISSION_MODES:
        return explicit
    if security_mode:
        mapped = _SECURITY_MODE_TO_PERMISSION.get(security_mode.strip().lower())
        if mapped:
            return mapped
    return "bypassPermissions"
