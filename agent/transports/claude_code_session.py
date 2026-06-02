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

# Process-wide per-session locks (MA3): concurrent --resume on the same
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

    def close(self) -> None:
        self.session_id = None
