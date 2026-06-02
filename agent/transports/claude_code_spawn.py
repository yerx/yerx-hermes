"""Spawn-time helpers for the claude_code_cli engine: trust gate, MCP config
file generation, spawn-env construction, and argv building."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Mapping, Optional

from agent.transports import claude_code_constants as c


class UntrustedContextError(RuntimeError):
    """Raised when the engine is invoked from an untrusted-carrying context.

    BL6: the engine grants Claude its full native tool surface under
    bypassPermissions; it is gated to trusted local-operator use only.
    """


# Context markers that indicate the turn may carry untrusted-derived input.
_UNTRUSTED_SOURCES = {"channel", "gateway", "remote_trigger", "webhook", "cron"}


def assert_trusted_context(context: Optional[Mapping[str, Any]]) -> None:
    """Raise UntrustedContextError if the execution context is not a trusted
    local operator. Conservative: an unknown/empty context is treated as
    local-interactive (the engine is opt-in and only reachable when the user
    selected it), but explicit untrusted markers refuse."""
    if not context:
        return
    if context.get("delegated"):
        raise UntrustedContextError(
            "claude_code_cli engine refuses delegated (delegate_task) contexts"
        )
    source = str(context.get("source") or "").strip().lower()
    if source in _UNTRUSTED_SOURCES:
        raise UntrustedContextError(
            f"claude_code_cli engine refuses untrusted context source={source!r}; "
            "this engine is gated to trusted local-operator use (it grants "
            "Claude unsandboxed native tools under bypassPermissions)."
        )


def build_mcp_config_file(
    *,
    tmp_dir: Optional[str] = None,
    context_env: Optional[Mapping[str, str]] = None,
) -> str:
    """Write a temp --mcp-config JSON pointing at the hermes-tools stdio MCP
    server, and return its path. The spawned server inherits the per-turn
    context env so the bridge can scope tool dispatch (BL5)."""
    server: dict[str, Any] = {
        "command": sys.executable or "python3",
        "args": ["-m", "agent.transports.hermes_tools_mcp_server"],
    }
    if context_env:
        server["env"] = dict(context_env)
    payload = {"mcpServers": {c.MCP_SERVER_NAME: server}}
    fd, path = tempfile.mkstemp(
        prefix="hermes-claude-mcp-", suffix=".json", dir=tmp_dir
    )
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
    return path


def build_spawn_env(
    *,
    base: Optional[Mapping[str, str]] = None,
    context_env: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Copy the base environment, remove the enumerated auth denylist, then
    overlay per-turn HERMES_* context. Never wildcard-clears (BL4)."""
    env = dict(base if base is not None else os.environ)
    for name in c.CLEAR_ENV:
        env.pop(name, None)
    if context_env:
        env.update(context_env)
    return env


def build_claude_argv(
    *,
    model: str,
    mcp_config_path: str,
    system_prompt_path: str,
    permission_mode: str,
    session_id: str,
    resume: bool,
) -> list[str]:
    """Build the `claude` argv for a fresh or resume turn.

    systemPromptWhen=always: the system prompt file is passed on every turn
    (helpers.ts:383). --include-partial-messages is intentionally omitted in
    v1 (no streaming-delta consumption yet)."""
    argv = [
        c.CLAUDE_BIN,
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--setting-sources", "user",
        "--permission-mode", permission_mode,
        "--mcp-config", mcp_config_path,
        "--strict-mcp-config",
        "--allowedTools", c.ALLOWED_TOOLS_GLOB,
        "--append-system-prompt-file", system_prompt_path,
        "--model", model,
    ]
    if resume:
        argv += ["--resume", session_id]
    else:
        argv += ["--session-id", session_id]
    return argv
