# Claude Code CLI engine for Hermes — design

**Status:** Approved (post two-reviewer consensus)
**Date:** 2026-06-01
**Branch:** `custom/claude-code-engine` (fork: `yerx/yerx-hermes`, upstream: `NousResearch/hermes-agent`)

## Goal

Add a new Hermes engine that runs the **Claude Code CLI (`claude`) as the reasoning
brain** instead of calling an LLM HTTP API. Each Hermes turn spawns `claude -p
--output-format stream-json`; Claude runs its own agentic tool-calling loop and
returns a final answer. Hermes is the orchestrator: it assembles the system prompt and
the new user message, manages the Claude session, parses the JSONL stream, persists the
result, and **bridges Hermes' own tool registry to Claude over MCP** so Claude operates
with Hermes' tools and guardrails — not its own built-ins.

This ports the `claude-code-engine` pattern from `yerx-openclaw`
(`extensions/anthropic/cli-*.ts`, `src/agents/cli-runner/*`,
`src/gateway/mcp-http.loopback-runtime.ts`).

## Key finding: Hermes already ships this pattern

The original plan (a standalone `claude_code_engine.py` returning an OpenAI-shaped
response object, dispatched inside `_interruptible_api_call`) was **wrong**. Two
independent design reviews converged on the same conclusion, verified against the code:

- `agent/transports/codex_app_server.py` + `agent/transports/codex_app_server_session.py`
  already implement a **CLI-owns-the-loop** runtime (spawn subprocess, env isolation,
  per-agent session, interrupt plumbing, message projection).
- It is dispatched via a **single early-return** in `run_conversation` at
  `run_agent.py:12472` (`if self.api_mode == "codex_app_server": return
  self._run_codex_app_server_turn(...)`), with the handler at `run_agent.py:15946` — it
  **bypasses** the chat-completion call sites (`_interruptible_api_call` ~7694,
  `_interruptible_streaming_api_call` ~8012) entirely.
- `agent/transports/hermes_tools_mcp_server.py` is a **FastMCP stdio server** that
  already re-exposes the Hermes tool registry (`model_tools.get_tool_definitions` +
  `model_tools.handle_function_call`) to a spawned CLI subprocess. The "MCP tool bridge"
  is therefore largely **already built**.

**Decision:** model the Claude engine directly on `codex_app_server`. Reuse the existing
MCP tools server. This minimizes the footprint inside the 832 KB `run_agent.py`, matches
the established pattern, and dramatically reduces upstream-rebase pain.

## Architecture

"Claude drives the loop." We mirror `codex_app_server`'s **seam, return contract, and
MCP bridge**. The one difference: `codex app-server` is a persistent JSON-RPC server held
live across turns, whereas Claude's `-p --resume` is **process-per-turn** — each turn
spawns a fresh `claude` subprocess that resumes Claude's stored session. `systemPromptWhen:
always` keeps Hermes' steering aligned across resumes.

Rejected alternative: "Claude as a one-shot model" where Hermes extracts `tool_calls`
and executes them in its own loop. `claude -p` is built to own its tool execution;
feeding results back out-of-band fights the tool and is fragile.

### Components

1. **`agent/transports/claude_code_session.py`** (new) — mirrors
   `codex_app_server_session.py`:
   - Builds the `claude` argv (fresh vs resume — see below).
   - Generates the `--mcp-config` file pointing at the Hermes tools MCP server (stdio).
   - Writes the system prompt to a temp file for `--append-system-prompt-file`.
   - Spawns the per-turn subprocess with a reader thread and cleared env.
   - Parses the stream-json output (text deltas, tool events, the `result` event).
   - Captures the Claude `session_id` for the next turn's `--resume`.
   - Enforces per-session serialization, a no-output watchdog, and interrupt → kill.
   - Returns the existing **`TurnResult`-style dict**
     (`final_response`/`messages`/`completed`/`partial`/`error`/usage), **not** a
     synthetic OpenAI response object.

2. **MCP tool bridge** — reuse/extend `agent/transports/hermes_tools_mcp_server.py`
   (FastMCP stdio). Generate the claude MCP config pointing at it; restrict Claude with
   `--allowedTools mcp__hermes__*` and `--strict-mcp-config` (so Claude cannot reach the
   user's other MCP servers).

3. **`run_agent.py` wiring** (minimal):
   - Add `"claude_code_cli"` to the valid `api_mode` set (line 1274).
   - Add an early-return branch beside line 12472 → `_run_claude_code_turn(...)`, a thin
     handler mirroring `_run_codex_app_server_turn` (~15946).
   - No edits to `_interruptible_api_call` / `_interruptible_streaming_api_call`.

4. **Selection & provider** — an explicit opt-in selector parallel to codex's runtime
   gate (NOT via `determine_api_mode`). A `claude-code` `ProviderProfile` with a **static
   model catalog** (no HTTP fetch) and a `claude --version` / auth doctor probe so doctor
   and catalog code don't assume an HTTP endpoint.

### `claude` invocation

Fresh turn:

```
claude -p --output-format stream-json --include-partial-messages --verbose \
  --setting-sources user \
  --permission-mode <posture> \
  --mcp-config <tmp.json> --strict-mcp-config \
  --allowedTools mcp__hermes__* \
  --append-system-prompt-file <tmp.txt> \
  --model <resolved> \
  --session-id <uuid>
```

Resume turn: identical, but `--session-id <uuid>` is replaced by `--resume <session_id>`,
and (per `systemPromptWhen: always`) the system prompt file is **re-written and
re-passed** every turn. The new user message is fed via **stdin**.

### Reliability (NOT deferrable)

These prevent hung/incorrect turns and ship in the first cut:

- **`--permission-mode`** — without it, `claude -p` blocks forever on an interactive
  approval prompt. **Default: `bypassPermissions`.** See "Permission posture" below.
- **No-output watchdog** + overall turn deadline; classify `no-output-timeout` /
  `overall-timeout` / non-zero-exit from stderr.
- **Interrupt** — wire Hermes' `interrupt()` to kill the subprocess / close stdin.
- **Session-keyed serialization** — concurrent `--resume <same-id>` must not interleave.

### Permission posture

The `<posture>` in the argv resolves to a `claude --permission-mode` value. **Default:
`bypassPermissions`** — the only mode that reliably completes a headless `claude -p`
turn, since any prompt-requiring mode would hang waiting on stdin that never comes.
Constraint then comes from `--allowedTools mcp__hermes__*` + `--strict-mcp-config` +
Hermes' own tool guardrails, not per-call approval.

Resolution precedence (first match wins), mirroring how `codex_app_server` derives its
permission profile from `tools.terminal.security_mode`:

1. **CLI flag** `--claude-permission-mode <value>` (one-off override).
2. **Env var** `HERMES_CLAUDE_PERMISSION_MODE`.
3. **Config** `tools.terminal.security_mode` → mapped via
   `_HERMES_TO_CLAUDE_PERMISSION_MODE`:
   - `unrestricted` / `yolo` / `auto` → `bypassPermissions`
   - `approval-required` → `plan` (read-only; Claude analyzes but does not execute)
   - unset / unknown → `bypassPermissions` (**the default**)
4. Fallback → `bypassPermissions`.

Accepted values: `bypassPermissions`, `acceptEdits`, `plan`, `default`. **Caveat:**
`default` and `acceptEdits` can still hang on a prompt in headless mode, so only
`bypassPermissions` (run) and `plan` (read-only) are fully supported in the first cut.
True per-call approval (mapping `approval-required` to an interactive gate) requires
MCP-side approval handling — deferred, matching OpenClaw's loopback approval model.

### Stream parsing

- Parse the `result` event for `session_id`, `usage`, and errors **even when the final
  text is empty** (tool-only turns legitimately finish empty — do not treat as failure).
- Tolerate per-line JSON parse errors; cap the parse buffer; refuse to parse truncated
  output.
- `SESSION_ID_FIELDS = [session_id, sessionId, conversation_id, conversationId]`; capture
  on the empty-result path too.

### Auth & env isolation

- `CLEAR_ENV` mirrors OpenClaw's list: all `ANTHROPIC_*`, `CLAUDE_CODE_*`, and `OTEL_*`.
  Decide explicitly whether to clear `CLAUDE_CONFIG_DIR` (clearing → use the default
  login tree; keeping → pin a config dir).
- The spawned `claude` uses **its own login** (env is cleared). The user must have run
  `claude` login beforehand; surface a clear "run `claude` login" error via the doctor
  probe.
- `--setting-sources user` loads the user's `~/.claude` settings/hooks — a non-obvious
  trust expansion; documented. `--strict-mcp-config` contains MCP-server leakage.

## Git / fork strategy (mirrors `yerx-openclaw`)

- `origin` → `yerx/yerx-hermes` (fork), `upstream` → `NousResearch/hermes-agent`.
- Work on `custom/claude-code-engine`.
- Upstream sync: `git fetch upstream && git rebase upstream/main`, landing on a dated
  `custom/claude-code-engine-rebase-YYYY-MM-DD` branch (same cadence as openclaw).

## Documented limitations / ceilings

- **`_AGENT_LOOP_TOOLS = {todo, memory, session_search, delegate_task}`**
  (`model_tools.py:484`) require live `AIAgent` context and **cannot** be dispatched
  statelessly over MCP — so they cannot cross the Claude boundary. Stated up front.
- Transport is **stdio** (reuse the existing server) for a single trusted local agent.
  OpenClaw's http-loopback with per-request token/scope headers is only needed for
  multi-tenant scoping — out of scope here.

## Out of scope (deferred)

- Image input.
- A persistent Claude live-session (`--input-format stream-json` long-lived process) —
  process-per-turn `--resume` is the first cut.
- Transcript re-seeding beyond `systemPromptWhen: always`.
- http-loopback MCP transport / per-request scoping.
- Full model-alias + `--effort`/thinking-level parity (basic alias map only).

## Testing

- `pytest` units: argv builder (fresh/resume), stream-json parser (fixture incl.
  empty-result and error lines), env clearing, session capture → resume, serialization
  gate, watchdog timeout classification.
- Live integration test **skipped when `claude` is not on PATH** (mirrors OpenClaw's
  `liveTest`).

## Key files to read before implementing

`agent/transports/codex_app_server.py`, `agent/transports/codex_app_server_session.py`,
`agent/transports/hermes_tools_mcp_server.py`, `run_agent.py:1274`, `run_agent.py:12472`,
`run_agent.py:15946`, `model_tools.py:484` / `:688` / `:718`.
On the openclaw side: `extensions/anthropic/cli-shared.ts`, `cli-backend.ts`;
`src/agents/cli-runner/execute.ts`, `helpers.ts`, `bundle-mcp-claude.ts`;
`src/agents/cli-output.ts`.
