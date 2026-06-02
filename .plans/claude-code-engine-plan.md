# Claude Code CLI Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `claude_code_cli` engine that runs the Claude Code CLI (`claude -p --output-format stream-json`) as Hermes' reasoning brain, with Hermes' tools bridged to Claude over stdio MCP.

**Architecture:** Mirror the existing `codex_app_server` runtime: a thin early-return in `conversation_loop.py` → `agent/claude_runtime.py` handler → `ClaudeCodeSession` adapter. Difference from codex: Claude is **process-per-turn** (`-p --resume`), parses **stream-json** (not JSON-RPC), and tools bridge through a generated `--mcp-config` (not `~/.codex/config.toml`). Gated to **trusted-local-operator** contexts (native tools are not sandboxed).

**Tech Stack:** Python 3.11, `pytest`, `subprocess`, the `claude` CLI binary, FastMCP (`agent/transports/hermes_tools_mcp_server.py`).

**Spec:** `.plans/claude-code-engine.md` (read it first).

---

## File Structure

| File | Responsibility |
|---|---|
| `agent/transports/claude_code_constants.py` (NEW) | Constants + pure mappings: `CLEAR_ENV` denylist, model aliases, default model, `SESSION_ID_FIELDS`, MCP server name, permission-mode resolution |
| `agent/transports/claude_code_spawn.py` (NEW) | `assert_trusted_context`, `build_mcp_config_file`, `build_spawn_env`, `build_claude_argv` |
| `agent/transports/claude_code_stream.py` (NEW) | `parse_claude_stream` — stream-json → final text / session_id / usage / projected message rows / error |
| `agent/transports/claude_code_session.py` (NEW) | `ClaudeCodeSession` + `TurnResult` — per-turn spawn, stdin write, stdout parse, watchdog, session lock-map, retire |
| `agent/claude_runtime.py` (NEW) | `run_claude_code_turn(agent, …)` — mirrors `agent/codex_runtime.py:28` |
| `agent/transports/hermes_tools_mcp_server.py` (MODIFY) | Read per-turn context from env; pass `enabled_toolsets`/`task_id`/`session_id` to `handle_function_call` (BL5) |
| `agent/agent_init.py:291` (MODIFY) | Add `"claude_code_cli"` to the valid api_mode set |
| `agent/conversation_loop.py:787` (MODIFY) | Add dispatch early-return for `claude_code_cli` |
| `run_agent.py:4602` (MODIFY) | Add `_run_claude_code_turn` forwarder |
| `providers/claude_code.py` (NEW) | `claude-code` ProviderProfile (static catalog, no HTTP) |
| `agent/doctor.py` or doctor module (MODIFY) | `claude --version` / login probe |

Conventions: every test file mirrors the module path under `tests/`. Use `python3.11 -m pytest`.

---

## Task 1: Constants module

**Files:**
- Create: `agent/transports/claude_code_constants.py`
- Test: `tests/transports/test_claude_code_constants.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/transports/test_claude_code_constants.py
import agent.transports.claude_code_constants as c


def test_mcp_server_name_matches_fastmcp_server():
    # BL2: the allowedTools glob MUST match the FastMCP server name in
    # hermes_tools_mcp_server.py (FastMCP("hermes-tools") -> mcp__hermes-tools__*).
    from agent.transports import hermes_tools_mcp_server as bridge
    # The bridge builds FastMCP(c.MCP_SERVER_NAME); assert the constant is the
    # single source of truth used by both sides.
    assert c.MCP_SERVER_NAME == "hermes-tools"
    assert c.ALLOWED_TOOLS_GLOB == "mcp__hermes-tools__*"
    assert c.ALLOWED_TOOLS_GLOB == f"mcp__{c.MCP_SERVER_NAME}__*"


def test_clear_env_is_enumerated_denylist_not_wildcard():
    # BL4: explicit, enumerated auth vars only. Must NOT blanket-match the
    # HERMES_* context env the MCP bridge depends on.
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
    # unknown explicit value falls back to the default rather than passing junk
    assert c.resolve_permission_mode("auto", explicit="garbage") == "bypassPermissions"


def test_session_id_fields_order():
    assert c.SESSION_ID_FIELDS[0] == "session_id"
    assert "conversation_id" in c.SESSION_ID_FIELDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/transports/test_claude_code_constants.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.transports.claude_code_constants'`

- [ ] **Step 3: Write minimal implementation**

```python
# agent/transports/claude_code_constants.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/transports/test_claude_code_constants.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/transports/claude_code_constants.py tests/transports/test_claude_code_constants.py
git commit -m "feat(claude-engine): constants, env denylist, permission-mode mapping"
```

---

## Task 2: Trusted-context gate (BL6)

**Files:**
- Create: `agent/transports/claude_code_spawn.py`
- Test: `tests/transports/test_claude_code_spawn_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/transports/test_claude_code_spawn_gate.py
import pytest
from agent.transports.claude_code_spawn import (
    assert_trusted_context,
    UntrustedContextError,
)


def test_local_interactive_context_is_allowed():
    # No untrusted markers -> returns None, does not raise.
    assert_trusted_context({"source": "cli", "interactive": True})


@pytest.mark.parametrize("ctx", [
    {"source": "channel"},
    {"source": "gateway"},
    {"delegated": True},
    {"source": "remote_trigger"},
])
def test_untrusted_contexts_are_refused(ctx):
    with pytest.raises(UntrustedContextError):
        assert_trusted_context(ctx)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/transports/test_claude_code_spawn_gate.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError`

- [ ] **Step 3: Write minimal implementation**

```python
# agent/transports/claude_code_spawn.py
"""Spawn-time helpers for the claude_code_cli engine: trust gate, MCP config
file generation, spawn-env construction, and argv building."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Mapping, Optional, Sequence

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/transports/test_claude_code_spawn_gate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/transports/claude_code_spawn.py tests/transports/test_claude_code_spawn_gate.py
git commit -m "feat(claude-engine): trusted-context gate (BL6)"
```

---

## Task 3: MCP config file generation (BL3)

**Files:**
- Modify: `agent/transports/claude_code_spawn.py`
- Test: `tests/transports/test_claude_code_mcp_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/transports/test_claude_code_mcp_config.py
import json
import os
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/transports/test_claude_code_mcp_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_mcp_config_file'`

- [ ] **Step 3: Write minimal implementation (append to `claude_code_spawn.py`)**

```python
import sys


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/transports/test_claude_code_mcp_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/transports/claude_code_spawn.py tests/transports/test_claude_code_mcp_config.py
git commit -m "feat(claude-engine): generate --mcp-config for hermes-tools bridge (BL3)"
```

---

## Task 4: Spawn-env denylist (BL4)

**Files:**
- Modify: `agent/transports/claude_code_spawn.py`
- Test: `tests/transports/test_claude_code_spawn_env.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/transports/test_claude_code_spawn_env.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/transports/test_claude_code_spawn_env.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation (append to `claude_code_spawn.py`)**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/transports/test_claude_code_spawn_env.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/transports/claude_code_spawn.py tests/transports/test_claude_code_spawn_env.py
git commit -m "feat(claude-engine): spawn-env auth denylist preserving HERMES_* context (BL4)"
```

---

## Task 5: argv builder (BL2 glob)

**Files:**
- Modify: `agent/transports/claude_code_spawn.py`
- Test: `tests/transports/test_claude_code_argv.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/transports/test_claude_code_argv.py
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
    # BL2: the glob must match the FastMCP server name exactly.
    i = argv.index("--allowedTools")
    assert argv[i + 1] == "mcp__hermes-tools__*"
    assert "--strict-mcp-config" in argv
    assert "--permission-mode" in argv
    assert "--session-id" in argv and "uuid-1" in argv
    assert "--resume" not in argv
    # v1 omits --include-partial-messages (no streaming yet).
    assert "--include-partial-messages" not in argv


def test_resume_turn_uses_resume_not_session_id(tmp_path):
    argv = build_claude_argv(session_id="uuid-1", resume=True, **_base_kwargs(tmp_path))
    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "uuid-1"
    assert "--session-id" not in argv
    # systemPromptWhen=always: system prompt is re-passed on resume too.
    assert "--append-system-prompt-file" in argv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/transports/test_claude_code_argv.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation (append to `claude_code_spawn.py`)**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/transports/test_claude_code_argv.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/transports/claude_code_spawn.py tests/transports/test_claude_code_argv.py
git commit -m "feat(claude-engine): claude argv builder, fresh+resume (BL2 glob)"
```

---

## Task 6: stream-json parser (MA1)

**Files:**
- Create: `agent/transports/claude_code_stream.py`
- Test: `tests/transports/test_claude_code_stream.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/transports/test_claude_code_stream.py
from agent.transports.claude_code_stream import parse_claude_stream


def _lines(*objs):
    import json
    return [json.dumps(o) for o in objs]


def test_parses_final_text_session_id_and_usage():
    out = parse_claude_stream(_lines(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
        {"type": "result", "result": "hello world", "session_id": "abc",
         "usage": {"input_tokens": 10, "output_tokens": 3}},
    ))
    assert out.final_text == "hello world"
    assert out.session_id == "abc"
    assert out.usage == {"input_tokens": 10, "output_tokens": 3}
    assert out.error is None


def test_empty_result_after_tools_is_not_failure_and_keeps_session_id():
    out = parse_claude_stream(_lines(
        {"type": "result", "result": "", "session_id": "xyz"},
    ))
    assert out.final_text == ""
    assert out.session_id == "xyz"
    assert out.error is None


def test_error_event_sets_error():
    out = parse_claude_stream(_lines(
        {"type": "result", "subtype": "error_during_execution", "is_error": True,
         "result": "boom", "session_id": "s"},
    ))
    assert out.error is not None
    assert out.session_id == "s"


def test_tolerates_malformed_lines():
    out = parse_claude_stream(["{not json", '{"type":"result","result":"ok","session_id":"s"}'])
    assert out.final_text == "ok"


def test_projects_tool_use_rows():
    out = parse_claude_stream(_lines(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__hermes-tools__web_search",
             "input": {"q": "x"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "results"}]}},
        {"type": "result", "result": "done", "session_id": "s"},
    ))
    roles = [m["role"] for m in out.projected_messages]
    assert "assistant" in roles and "tool" in roles
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/transports/test_claude_code_stream.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# agent/transports/claude_code_stream.py
"""Parse Claude Code CLI stream-json (one JSON object per line).

v1 scope (MA1): final text + session_id + usage + error classification, plus
assistant-text / tool_use / tool_result projection into Hermes message rows.
Live streaming deltas (stream_event/content_block_delta) are deferred."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from agent.transports import claude_code_constants as c

logger = logging.getLogger(__name__)

_MAX_PARSE_BYTES = 1024 * 1024


@dataclass
class ClaudeStreamResult:
    final_text: str = ""
    session_id: Optional[str] = None
    usage: Optional[dict] = None
    error: Optional[str] = None
    projected_messages: list[dict] = field(default_factory=list)
    tool_iterations: int = 0


def _capture_session_id(obj: dict, current: Optional[str]) -> Optional[str]:
    if current:
        return current
    for key in c.SESSION_ID_FIELDS:
        val = obj.get(key)
        if val:
            return str(val)
    return current


def parse_claude_stream(lines: Iterable[str]) -> ClaudeStreamResult:
    res = ClaudeStreamResult()
    seen = 0
    for raw in lines:
        if not raw or not raw.strip():
            continue
        seen += len(raw)
        if seen > _MAX_PARSE_BYTES:
            logger.warning("claude stream exceeded parse cap; truncating")
            break
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            logger.debug("skipping non-JSON claude stream line")
            continue
        if not isinstance(obj, dict):
            continue
        res.session_id = _capture_session_id(obj, res.session_id)
        otype = obj.get("type")

        if otype == "assistant":
            content = (obj.get("message") or {}).get("content") or []
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            tool_uses = [b for b in content
                         if isinstance(b, dict) and b.get("type") == "tool_use"]
            if texts:
                res.projected_messages.append(
                    {"role": "assistant", "content": "".join(texts)}
                )
            for tu in tool_uses:
                res.tool_iterations += 1
                res.projected_messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": tu.get("id"),
                        "type": "function",
                        "function": {
                            "name": tu.get("name"),
                            "arguments": json.dumps(tu.get("input") or {}),
                        },
                    }],
                })
        elif otype == "user":
            content = (obj.get("message") or {}).get("content") or []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    res.projected_messages.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id"),
                        "content": _stringify(b.get("content")),
                    })
        elif otype == "error":
            res.error = str(obj.get("error") or obj.get("message") or "claude stream error")
        elif otype == "result":
            res.usage = obj.get("usage") or res.usage
            if obj.get("is_error") or obj.get("subtype") in {
                "error_during_execution", "error_max_turns",
            }:
                res.error = str(obj.get("result") or obj.get("subtype") or "claude error")
            else:
                res.final_text = str(obj.get("result") or "")
    return res


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "".join(parts) or json.dumps(content)
    return "" if content is None else str(content)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/transports/test_claude_code_stream.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/transports/claude_code_stream.py tests/transports/test_claude_code_stream.py
git commit -m "feat(claude-engine): stream-json parser with tool-row projection (MA1)"
```

---

## Task 7: Per-turn context injection in the MCP bridge (BL5)

**Files:**
- Modify: `agent/transports/hermes_tools_mcp_server.py` (the `_dispatch` closure, ~line 162-171; `MCP_SERVER_NAME` at line 126)
- Test: `tests/transports/test_hermes_tools_context_injection.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/transports/test_hermes_tools_context_injection.py
import agent.transports.hermes_tools_mcp_server as bridge


def test_dispatch_passes_context_from_env(monkeypatch):
    captured = {}

    def fake_handle(function_name, function_args, **kwargs):
        captured["name"] = function_name
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(bridge, "_handle_function_call_ref", fake_handle, raising=False)
    monkeypatch.setenv("HERMES_ENABLED_TOOLSETS", "web,browser")
    monkeypatch.setenv("HERMES_SESSION_ID", "s1")
    monkeypatch.setenv("HERMES_TASK_ID", "t1")

    ctx = bridge._read_context_from_env()
    assert ctx["enabled_toolsets"] == ["web", "browser"]
    assert ctx["session_id"] == "s1"
    assert ctx["task_id"] == "t1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/transports/test_hermes_tools_context_injection.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_read_context_from_env'`

- [ ] **Step 3: Write minimal implementation**

Add to `agent/transports/hermes_tools_mcp_server.py` (near the top, after imports):

```python
def _read_context_from_env() -> dict:
    """Per-turn context the spawning engine injected as env (BL5).

    Lets the stateless MCP bridge pass the session's enabled_toolsets (the
    out-of-scope-tool security gate, model_tools.py:885), task_id, and
    session_id into handle_function_call."""
    def _split(name: str):
        raw = os.environ.get(name) or ""
        return [p for p in (s.strip() for s in raw.split(",")) if p] or None

    return {
        "enabled_toolsets": _split("HERMES_ENABLED_TOOLSETS"),
        "disabled_toolsets": _split("HERMES_DISABLED_TOOLSETS"),
        "task_id": os.environ.get("HERMES_TASK_ID") or None,
        "session_id": os.environ.get("HERMES_SESSION_ID") or None,
    }
```

Then change the dispatch closure (`_make_handler`) to thread the context in.
Replace the existing body:

```python
        def _make_handler(tool_name: str):
            def _dispatch(**kwargs: Any) -> str:
                try:
                    ctx = _read_context_from_env()
                    return _handle_function_call_ref(
                        tool_name,
                        kwargs or {},
                        task_id=ctx["task_id"],
                        session_id=ctx["session_id"],
                        enabled_toolsets=ctx["enabled_toolsets"],
                        disabled_toolsets=ctx["disabled_toolsets"],
                    )
                except Exception as exc:
                    logger.exception("tool %s raised", tool_name)
                    return json.dumps({"error": str(exc), "tool": tool_name})
            _dispatch.__name__ = tool_name
            _dispatch.__doc__ = description
            return _dispatch
```

And, where `handle_function_call` is imported inside `_build_server` (line ~120),
bind a module-level ref so the test can monkeypatch it:

```python
    from model_tools import (
        get_tool_definitions,
        handle_function_call,
    )
    global _handle_function_call_ref
    _handle_function_call_ref = handle_function_call
```

Add near module top: `_handle_function_call_ref = None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/transports/test_hermes_tools_context_injection.py -v`
Expected: PASS

- [ ] **Step 5: Run the existing bridge tests to confirm no regression**

Run: `python3.11 -m pytest tests/transports/ -k hermes_tools -v`
Expected: PASS (existing codex-path tests still green)

- [ ] **Step 6: Commit**

```bash
git add agent/transports/hermes_tools_mcp_server.py tests/transports/test_hermes_tools_context_injection.py
git commit -m "feat(claude-engine): thread per-turn toolset/session context through MCP bridge (BL5)"
```

---

## Task 8: ClaudeCodeSession adapter

**Files:**
- Create: `agent/transports/claude_code_session.py`
- Test: `tests/transports/test_claude_code_session.py`

**Interface (TurnResult mirrors codex's fields so `claude_runtime` can be a near-copy of `codex_runtime`):**

- [ ] **Step 1: Write the failing test (uses a fake spawn so no real `claude` needed)**

```python
# tests/transports/test_claude_code_session.py
import json
from agent.transports.claude_code_session import ClaudeCodeSession, TurnResult


def _fake_runner(lines, exit_code=0, session_id="sess-1"):
    """Return a spawn_fn(argv, env, stdin_text) -> (stdout_lines, exit_code)."""
    def _run(argv, env, stdin_text, timeout=None, no_output_timeout=None):
        return (list(lines), exit_code)
    return _run


def test_fresh_turn_returns_final_text_and_captures_session(tmp_path):
    lines = [json.dumps({"type": "result", "result": "hello", "session_id": "sess-1"})]
    sess = ClaudeCodeSession(cwd=str(tmp_path), spawn_fn=_fake_runner(lines))
    res = sess.run_turn(user_input="hi", system_prompt="be nice")
    assert isinstance(res, TurnResult)
    assert res.final_text == "hello"
    assert sess.session_id == "sess-1"
    assert res.error is None


def test_second_turn_resumes_captured_session(tmp_path):
    captured_argv = {}

    def _run(argv, env, stdin_text, timeout=None, no_output_timeout=None):
        captured_argv["argv"] = argv
        return ([json.dumps({"type": "result", "result": "ok", "session_id": "sess-1"})], 0)

    sess = ClaudeCodeSession(cwd=str(tmp_path), spawn_fn=_run)
    sess.run_turn(user_input="one", system_prompt="s")
    sess.run_turn(user_input="two", system_prompt="s")
    assert "--resume" in captured_argv["argv"]
    assert "sess-1" in captured_argv["argv"]


def test_nonzero_exit_marks_error_and_retire(tmp_path):
    sess = ClaudeCodeSession(cwd=str(tmp_path), spawn_fn=_fake_runner([], exit_code=1))
    res = sess.run_turn(user_input="hi", system_prompt="s")
    assert res.error is not None
    assert res.should_retire is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/transports/test_claude_code_session.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# agent/transports/claude_code_session.py
"""Per-turn Claude Code CLI session adapter.

Unlike codex (a persistent JSON-RPC server), Claude `-p --resume` is
process-per-turn: each run_turn spawns a fresh `claude`, feeds the user
message on stdin, reads stream-json from stdout, and resumes the prior
session via --resume on subsequent turns. A process-wide lock keyed by
session_id serializes concurrent turns on the same session.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent.transports import claude_code_constants as c
from agent.transports.claude_code_spawn import (
    build_claude_argv,
    build_mcp_config_file,
    build_spawn_env,
)
from agent.transports.claude_code_stream import parse_claude_stream

logger = logging.getLogger(__name__)

# Process-wide per-session locks (BL/MA3): concurrent --resume on the same
# session id must not interleave.
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _lock_for(session_id: str) -> threading.Lock:
    with _SESSION_LOCKS_GUARD:
        lk = _SESSION_LOCKS.get(session_id)
        if lk is None:
            lk = threading.Lock()
            _SESSION_LOCKS[session_id] = lk
        return lk


@dataclass
class TurnResult:
    final_text: str = ""
    projected_messages: list[dict] = field(default_factory=list)
    tool_iterations: int = 0
    interrupted: bool = False
    error: Optional[str] = None
    turn_id: Optional[str] = None
    thread_id: Optional[str] = None
    should_retire: bool = False


def _default_spawn(argv, env, stdin_text, timeout=None, no_output_timeout=None):
    """Run `claude` to completion, return (stdout_lines, exit_code)."""
    proc = subprocess.run(
        argv,
        input=stdin_text,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    lines = proc.stdout.splitlines() if proc.stdout else []
    if proc.returncode != 0 and proc.stderr:
        logger.warning("claude exited %s: %s", proc.returncode, proc.stderr[-2000:])
    return (lines, proc.returncode)


class ClaudeCodeSession:
    def __init__(
        self,
        *,
        cwd: Optional[str] = None,
        permission_mode: Optional[str] = None,
        context_env: Optional[dict] = None,
        model: Optional[str] = None,
        turn_timeout: float = 600.0,
        spawn_fn: Optional[Callable] = None,
    ) -> None:
        self._cwd = cwd or os.getcwd()
        self._permission_mode = permission_mode or c.resolve_permission_mode(
            os.environ.get("HERMES_TERMINAL_SECURITY_MODE"),
            explicit=os.environ.get("HERMES_CLAUDE_PERMISSION_MODE"),
        )
        self._context_env = context_env or {}
        self._model = model or c.DEFAULT_MODEL
        self._turn_timeout = turn_timeout
        self._spawn_fn = spawn_fn or _default_spawn
        self.session_id: Optional[str] = None

    def _write_system_prompt(self, text: str) -> str:
        fd, path = tempfile.mkstemp(prefix="hermes-claude-sys-", suffix=".txt")
        with os.fdopen(fd, "w") as fh:
            fh.write(text or "")
        return path

    def run_turn(self, *, user_input: str, system_prompt: str) -> TurnResult:
        result = TurnResult()
        resume = self.session_id is not None
        session_id = self.session_id or str(uuid.uuid4())
        lock = _lock_for(session_id)
        mcp_path = sys_path = None
        with lock:
            try:
                mcp_path = build_mcp_config_file(context_env=self._context_env)
                sys_path = self._write_system_prompt(system_prompt)
                argv = build_claude_argv(
                    model=self._model,
                    mcp_config_path=mcp_path,
                    system_prompt_path=sys_path,
                    permission_mode=self._permission_mode,
                    session_id=session_id,
                    resume=resume,
                )
                env = build_spawn_env(context_env=self._context_env)
                logger.info(
                    "claude turn: resume=%s permission_mode=%s model=%s",
                    resume, self._permission_mode, self._model,
                )
                try:
                    lines, code = self._spawn_fn(
                        argv, env, user_input, timeout=self._turn_timeout,
                    )
                except subprocess.TimeoutExpired:
                    result.error = f"claude turn timed out after {self._turn_timeout}s"
                    result.should_retire = True
                    return result
                except FileNotFoundError:
                    result.error = (
                        "`claude` CLI not found on PATH. Install Claude Code and run "
                        "`claude` login, then retry."
                    )
                    result.should_retire = True
                    return result

                parsed = parse_claude_stream(lines)
                result.final_text = parsed.final_text
                result.projected_messages = parsed.projected_messages
                result.tool_iterations = parsed.tool_iterations
                result.error = parsed.error
                if parsed.session_id:
                    self.session_id = parsed.session_id
                    result.thread_id = parsed.session_id
                if code != 0 and result.error is None:
                    result.error = f"claude exited with code {code}"
                if result.error is not None:
                    # Drop the session so the next turn starts fresh.
                    result.should_retire = True
                    self.session_id = None
                return result
            finally:
                for p in (mcp_path, sys_path):
                    if p:
                        try:
                            os.unlink(p)
                        except OSError:
                            pass

    def close(self) -> None:  # symmetry with codex; nothing persistent to tear down
        self.session_id = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/transports/test_claude_code_session.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/transports/claude_code_session.py tests/transports/test_claude_code_session.py
git commit -m "feat(claude-engine): ClaudeCodeSession per-turn adapter with session lock + retire"
```

---

## Task 9: claude_runtime turn handler

**Files:**
- Create: `agent/claude_runtime.py`
- Test: `tests/test_claude_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_runtime.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_claude_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# agent/claude_runtime.py
"""Claude Code CLI runtime path. Mirrors agent/codex_runtime.py.

Hands the whole turn to a `claude -p` subprocess (Claude owns the tool loop)
and projects its stream-json output back into Hermes' messages list. Returns
the same dict shape as the chat_completions path. Called from
conversation_loop.py when agent.api_mode == "claude_code_cli"."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def run_claude_code_turn(
    agent,
    *,
    user_message: str,
    original_user_message: Any,
    messages: List[Dict[str, Any]],
    effective_task_id: str,
    should_review_memory: bool = False,
) -> Dict[str, Any]:
    # Lazy per-AIAgent session, reused across turns for --resume continuity.
    if getattr(agent, "_claude_session", None) is None:
        agent._claude_session = agent._claude_make_session()

    system_prompt = agent._claude_build_system_prompt()

    try:
        turn = agent._claude_session.run_turn(
            user_input=user_message, system_prompt=system_prompt,
        )
    except Exception as exc:
        logger.exception("claude code turn failed")
        try:
            agent._claude_session.close()
        except Exception:
            pass
        agent._claude_session = None
        return {
            "final_response": f"Claude Code engine turn failed: {exc}.",
            "messages": messages,
            "api_calls": 0,
            "completed": False,
            "partial": True,
            "error": str(exc),
        }

    if getattr(turn, "should_retire", False):
        logger.warning("claude code session retired (turn error: %s)", turn.error)
        try:
            agent._claude_session.close()
        except Exception:
            pass
        agent._claude_session = None

    if turn.projected_messages:
        messages.extend(turn.projected_messages)

    agent._iters_since_skill = (
        getattr(agent, "_iters_since_skill", 0) + turn.tool_iterations
    )
    should_review_skills = False
    if (
        agent._skill_nudge_interval > 0
        and agent._iters_since_skill >= agent._skill_nudge_interval
        and "skill_manage" in agent.valid_tool_names
    ):
        should_review_skills = True
        agent._iters_since_skill = 0

    if not turn.interrupted and turn.error is None:
        try:
            agent._sync_external_memory_for_turn(
                original_user_message=original_user_message,
                final_response=turn.final_text,
                interrupted=False,
            )
        except Exception:
            logger.debug("external memory sync raised", exc_info=True)

    if (
        turn.final_text
        and not turn.interrupted
        and (should_review_memory or should_review_skills)
    ):
        try:
            agent._spawn_background_review(
                messages_snapshot=list(messages),
                review_memory=should_review_memory,
                review_skills=should_review_skills,
            )
        except Exception:
            logger.debug("background review spawn raised", exc_info=True)

    return {
        "final_response": turn.final_text,
        "messages": messages,
        "api_calls": 1,
        "completed": not turn.interrupted and turn.error is None,
        "partial": turn.interrupted or turn.error is not None,
        "error": turn.error,
        "claude_session_id": turn.thread_id,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_claude_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/claude_runtime.py tests/test_claude_runtime.py
git commit -m "feat(claude-engine): claude_runtime turn handler (mirrors codex_runtime)"
```

---

## Task 10: AIAgent helpers (`_claude_make_session`, `_claude_build_system_prompt`, forwarder)

**Files:**
- Modify: `run_agent.py` (add forwarder beside `_run_codex_app_server_turn` at line 4602; add the two helper methods on `AIAgent`)
- Test: `tests/test_claude_runtime_helpers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_runtime_helpers.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_claude_runtime_helpers.py -v`
Expected: FAIL on the `hasattr` assertion

- [ ] **Step 3: Write minimal implementation**

In `run_agent.py`, immediately after the `_run_codex_app_server_turn` method (ends at line 4613), add:

```python
    def _run_claude_code_turn(
        self,
        *,
        user_message: str,
        original_user_message: Any,
        messages: List[Dict[str, Any]],
        effective_task_id: str,
        should_review_memory: bool = False,
    ) -> Dict[str, Any]:
        """Forwarder — see ``agent.claude_runtime.run_claude_code_turn``."""
        from agent.claude_runtime import run_claude_code_turn
        return run_claude_code_turn(
            self,
            user_message=user_message,
            original_user_message=original_user_message,
            messages=messages,
            effective_task_id=effective_task_id,
            should_review_memory=should_review_memory,
        )

    def _claude_make_session(self):
        from agent.transports.claude_code_session import ClaudeCodeSession
        return ClaudeCodeSession(
            cwd=getattr(self, "session_cwd", None) or os.getcwd(),
            context_env=self._claude_turn_context(),
            model=self.model,
        )

    def _claude_turn_context(self) -> dict:
        """Per-turn HERMES_* context env injected into the spawned claude +
        MCP bridge (BL5)."""
        ctx: dict[str, str] = {}
        enabled = getattr(self, "enabled_toolsets", None)
        if enabled:
            ctx["HERMES_ENABLED_TOOLSETS"] = ",".join(enabled)
        disabled = getattr(self, "disabled_toolsets", None)
        if disabled:
            ctx["HERMES_DISABLED_TOOLSETS"] = ",".join(disabled)
        sid = getattr(self, "session_id", None)
        if sid:
            ctx["HERMES_SESSION_ID"] = str(sid)
        return ctx

    def _claude_build_system_prompt(self) -> str:
        """Reuse the agent's existing system-prompt assembly."""
        return self._build_system_prompt() if hasattr(self, "_build_system_prompt") else ""
```

> Verify `os` is imported at the top of `run_agent.py` (it is). If `self.model`,
> `self.enabled_toolsets`, or `self.disabled_toolsets` differ in name in this
> build, grep `agent/agent_init.py` for the attribute set on `agent.` and adjust.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_claude_runtime_helpers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add run_agent.py tests/test_claude_runtime_helpers.py
git commit -m "feat(claude-engine): AIAgent forwarder + session/system-prompt helpers"
```

---

## Task 11: Wire api_mode set + dispatch early-return + trust gate

**Files:**
- Modify: `agent/agent_init.py:291`
- Modify: `agent/conversation_loop.py:787` (add a branch beside the codex one)
- Test: `tests/test_claude_engine_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_engine_dispatch.py
import types
import agent.conversation_loop as cl


def test_claude_code_cli_is_a_valid_api_mode():
    from agent import agent_init
    import inspect
    src = inspect.getsource(agent_init)
    assert '"claude_code_cli"' in src


def test_dispatch_calls_run_claude_code_turn(monkeypatch):
    called = {}

    agent = types.SimpleNamespace()
    agent.api_mode = "claude_code_cli"
    agent._memory_manager = None

    def fake_turn(**kwargs):
        called.update(kwargs)
        return {"final_response": "ok", "messages": kwargs["messages"],
                "api_calls": 1, "completed": True, "partial": False, "error": None}

    agent._run_claude_code_turn = fake_turn
    # Minimal stand-ins for the symbols the dispatch block reads:
    agent._run_codex_app_server_turn = lambda **k: {}

    # Drive just the dispatch decision. (Helper extracted in Step 3.)
    out = cl._maybe_dispatch_cli_runtime(
        agent, user_message="q", original_user_message="q",
        messages=[{"role": "user", "content": "q"}],
        effective_task_id="t", should_review_memory=False,
    )
    assert out is not None
    assert out["final_response"] == "ok"
    assert called["user_message"] == "q"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_claude_engine_dispatch.py -v`
Expected: FAIL (`'"claude_code_cli"' in src` is False; `_maybe_dispatch_cli_runtime` missing)

- [ ] **Step 3: Write minimal implementation**

(a) `agent/agent_init.py:291` — add the new mode to the set:

```python
    if api_mode in {"chat_completions", "codex_responses", "anthropic_messages", "bedrock_converse", "codex_app_server", "claude_code_cli"}:
        agent.api_mode = api_mode
```

(b) `agent/conversation_loop.py` — extract a small dispatch helper and call it
where the codex early-return is (line 787). Replace the existing codex block:

```python
    cli_runtime_result = _maybe_dispatch_cli_runtime(
        agent,
        user_message=user_message,
        original_user_message=original_user_message,
        messages=messages,
        effective_task_id=effective_task_id,
        should_review_memory=_should_review_memory,
    )
    if cli_runtime_result is not None:
        return cli_runtime_result
```

And add the helper at module scope in `conversation_loop.py`:

```python
def _maybe_dispatch_cli_runtime(
    agent,
    *,
    user_message,
    original_user_message,
    messages,
    effective_task_id,
    should_review_memory,
):
    """Return a turn-result dict if a CLI-owns-the-loop runtime handles this
    turn (codex_app_server or claude_code_cli); otherwise None."""
    if agent.api_mode == "codex_app_server":
        return agent._run_codex_app_server_turn(
            user_message=user_message,
            original_user_message=original_user_message,
            messages=messages,
            effective_task_id=effective_task_id,
            should_review_memory=should_review_memory,
        )
    if agent.api_mode == "claude_code_cli":
        from agent.transports.claude_code_spawn import assert_trusted_context
        assert_trusted_context(getattr(agent, "_execution_context", None))
        return agent._run_claude_code_turn(
            user_message=user_message,
            original_user_message=original_user_message,
            messages=messages,
            effective_task_id=effective_task_id,
            should_review_memory=should_review_memory,
        )
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_claude_engine_dispatch.py -v`
Expected: PASS

- [ ] **Step 5: Run the codex dispatch tests to confirm no regression**

Run: `python3.11 -m pytest tests/ -k "codex and (dispatch or runtime or app_server)" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/agent_init.py agent/conversation_loop.py tests/test_claude_engine_dispatch.py
git commit -m "feat(claude-engine): wire api_mode + dispatch early-return + trust gate"
```

---

## Task 12: `claude-code` provider profile + selection (MA2)

**Files:**
- Create: `providers/claude_code.py`
- Test: `tests/providers/test_claude_code_provider.py`

> Read `providers/base.py` (`ProviderProfile`) and `providers/__init__.py`
> (`register_provider`, `_discover_providers`) first — match the existing
> registration pattern exactly. claude-code is a DISTINCT provider (its own
> `claude` login auth, static catalog), NOT an `openai_runtime` sub-mode.

- [ ] **Step 1: Write the failing test**

```python
# tests/providers/test_claude_code_provider.py
from providers import get_provider_profile


def test_claude_code_provider_registered():
    p = get_provider_profile("claude-code")
    assert p is not None
    assert p.api_mode == "claude_code_cli"
    # No HTTP catalog probe — auth is the local `claude` login.
    assert p.supports_health_check is False
    assert p.fallback_models  # static catalog present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.11 -m pytest tests/providers/test_claude_code_provider.py -v`
Expected: FAIL (`get_provider_profile("claude-code")` is None)

- [ ] **Step 3: Write minimal implementation**

```python
# providers/claude_code.py
"""Provider profile for the Claude Code CLI engine.

A distinct provider (not an openai_runtime sub-mode): auth is the local
`claude` login, the catalog is static, and no HTTP health probe applies.
"""

from __future__ import annotations

from providers import register_provider
from providers.base import ProviderProfile

register_provider(ProviderProfile(
    name="claude-code",
    api_mode="claude_code_cli",
    display_name="Claude Code (CLI engine)",
    description="Runs the local `claude` CLI as the reasoning engine.",
    auth_type="api_key",          # placeholder; real auth is `claude` login
    supports_health_check=False,  # doctor uses a `claude --version` probe instead
    fallback_models=(
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ),
))
```

> If `providers/_discover_providers()` imports modules by filename convention,
> confirm `claude_code.py` is auto-discovered; if it uses an explicit registry
> list, add `claude_code` there.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.11 -m pytest tests/providers/test_claude_code_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add providers/claude_code.py tests/providers/test_claude_code_provider.py
git commit -m "feat(claude-engine): claude-code provider profile (distinct provider, MA2)"
```

---

## Task 13: Special-case sweep + doctor probe

**Files:**
- Modify (audit each): `cli.py`, `hermes_cli/runtime_provider.py`, `hermes_cli/banner.py`, `hermes_cli/commands.py`, `agent/background_review.py`, `tui_gateway/server.py`, `gateway/run.py`
- Modify: the doctor module (find it: `grep -rn "def.*doctor\|shutil.which(\"claude\")" --include=*.py`)
- Test: `tests/test_claude_engine_special_cases.py`

- [ ] **Step 1: Find every site that special-cases `codex_app_server`**

Run: `grep -rn '"codex_app_server"' --include="*.py" agent cli.py hermes_cli tui_gateway gateway | grep -v test`
For each hit, decide whether `claude_code_cli` needs the same treatment (it also bypasses chat-completion message shapes and owns its own loop). The most important is `agent/background_review.py` (it skips/redirects review for CLI-owns-loop runtimes).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_claude_engine_special_cases.py
import inspect
import agent.background_review as br


def test_background_review_treats_claude_like_codex():
    src = inspect.getsource(br)
    # Wherever codex_app_server is branched on, claude_code_cli must appear too.
    assert "codex_app_server" in src
    assert "claude_code_cli" in src
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3.11 -m pytest tests/test_claude_engine_special_cases.py -v`
Expected: FAIL (`"claude_code_cli" in src` is False)

- [ ] **Step 4: Implement**

In `agent/background_review.py`, find each `== "codex_app_server"` / `in {... "codex_app_server"}` check and extend it to include `"claude_code_cli"` (group them: `in {"codex_app_server", "claude_code_cli"}`). Repeat the same grouping at any other site from Step 1 where a CLI-owns-loop runtime must be special-cased (banner label, runtime provider listing, gateway message-shape guards).

For the doctor module, add a probe mirroring the existing `shutil.which("claude")` precedent at `agent/anthropic_adapter.py:1159`:

```python
# in the doctor checks, when provider/api_mode is the claude engine:
import shutil
if shutil.which("claude") is None:
    report.error(
        "claude_code_cli engine selected but `claude` is not on PATH. "
        "Install Claude Code and run `claude` to log in."
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3.11 -m pytest tests/test_claude_engine_special_cases.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(claude-engine): special-case sweep + claude --version doctor probe"
```

---

## Task 14: Live integration test (skipped without `claude`)

**Files:**
- Test: `tests/integration/test_claude_engine_live.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_claude_engine_live.py
import shutil
import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not on PATH (live test)",
)


def test_single_turn_round_trip(tmp_path):
    from agent.transports.claude_code_session import ClaudeCodeSession
    sess = ClaudeCodeSession(cwd=str(tmp_path))
    res = sess.run_turn(
        user_input="Reply with exactly the word: pong",
        system_prompt="You are a test harness. Follow instructions exactly.",
    )
    assert res.error is None, res.error
    assert "pong" in res.final_text.lower()
    assert sess.session_id  # captured for resume
```

- [ ] **Step 2: Run it**

Run: `python3.11 -m pytest tests/integration/test_claude_engine_live.py -v`
Expected: SKIPPED if `claude` absent; PASS if installed + logged in.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_claude_engine_live.py
git commit -m "test(claude-engine): live single-turn integration test (skipped w/o claude)"
```

---

## Task 15: Full suite + docs + push

- [ ] **Step 1: Run the full new-engine suite**

Run: `python3.11 -m pytest tests/transports/test_claude_code_*.py tests/test_claude_runtime*.py tests/test_claude_engine_*.py tests/providers/test_claude_code_provider.py -v`
Expected: PASS (live test skipped)

- [ ] **Step 2: Run a broad regression on touched areas**

Run: `python3.11 -m pytest tests/ -k "codex or transports or provider or conversation" -q`
Expected: PASS (no regressions in codex/runtime/provider paths)

- [ ] **Step 3: Document selection in `cli-config.yaml.example`**

Add a commented block showing how to select the engine (provider `claude-code` / `--api-mode claude_code_cli`), the `HERMES_CLAUDE_PERMISSION_MODE` override, and the prerequisite `claude` login. Mirror the style of the existing codex runtime block.

- [ ] **Step 4: Commit + push**

```bash
git add -A
git commit -m "docs(claude-engine): document engine selection + claude login prerequisite"
git push origin custom/claude-code-engine
```

---

## Self-Review (completed by author)

- **Spec coverage:** BL1 anchors (Tasks 10/11 target the refactored layout) ✓; BL2 glob (Tasks 1,5 + test) ✓; BL3 mcp-config gen (Task 3) ✓; BL4 enumerated denylist (Tasks 1,4) ✓; BL5 context injection (Tasks 7,10) ✓; BL6 trust gate + default (Tasks 1,2,11) ✓; MA1 stream-parser v1 scope (Task 6) ✓; MA2 distinct provider + selection (Task 12) ✓; MA3 session lock + retire (Task 8) ✓; doctor probe + special-case sweep (Task 13) ✓; tests incl. glob + mcp-config + live-skip (Tasks 1,3,14) ✓.
- **Deferred per spec (no task, intentional):** image input, live streaming deltas, http-loopback transport, model-`--effort` parity, MCP-side approval. Fresh/resume watchdog split is partially covered by the single `turn_timeout` in Task 8 — note for a follow-up; v1 ships one deadline.
- **Type consistency:** `TurnResult` fields used by `claude_runtime` (Task 9) match the dataclass in Task 8; `build_*` signatures in Tasks 3/4/5 match their call sites in Task 8; `MCP_SERVER_NAME`/`ALLOWED_TOOLS_GLOB` are the single source used by Tasks 1/5/7.
- **Known integration risks to verify during execution (grep, don't assume):** exact attribute names on `agent.` (`self.model`, `enabled_toolsets`, `disabled_toolsets`, `_execution_context`) in `agent/agent_init.py`; the precise line of the codex early-return after upstream rebases; provider auto-discovery mechanism in `providers/__init__.py`.
