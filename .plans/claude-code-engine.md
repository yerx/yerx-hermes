# Claude Code CLI engine for Hermes — design

**Status:** Approved (revised after two review rounds; code-verified against the working repo)
**Date:** 2026-06-01 (rev. 2026-06-02)
**Branch:** `custom/claude-code-engine` (fork: `yerx/yerx-hermes`, upstream: `NousResearch/hermes-agent`)

> **Note on code anchors:** all line numbers below are verified against the **current
> working repo** (`yerx-hermes`, fresh from upstream — `run_agent.py` is 4831 lines).
> An earlier draft cited a stale May-15 reference clone (16k-line `run_agent.py`); those
> anchors were wrong. The repo was refactored upstream (`conversation_loop.py`,
> `codex_runtime.py`, `agent_init.py` extracted out of `run_agent.py`).

## Goal

Add a `claude_code_cli` engine that runs the **Claude Code CLI (`claude`) as the
reasoning brain** instead of an LLM HTTP API. Each Hermes turn spawns `claude -p
--output-format stream-json`; Claude runs its own agentic loop and returns a final
answer. Hermes orchestrates: assembles the system prompt + new user message, manages the
Claude session, parses the JSONL stream, persists the result, and bridges Hermes' tools
to Claude over MCP.

**Security framing (corrected).** `--allowedTools` is an **auto-approve allowlist, not a
sandbox** — it does **not** strip Claude's native `Bash`/`Write`/`Edit`/`Read`
(OpenClaw's Claude backend is `nativeToolMode: "always-on"` and *throws* if you try to
disable native tools — `extensions/anthropic/.../prepare.ts:168`). Therefore this engine
necessarily grants Claude its **full native tool surface**, and Hermes'
`tools.terminal.security_mode` / edit guardrails do **not** apply to native-tool actions.
The MCP bridge is *additive* (gives Claude Hermes' web/browser/vision tools it lacks); it
is not a containment boundary. See "Permission posture & trust scope".

This ports the `claude-code-engine` pattern from `yerx-openclaw`
(`extensions/anthropic/cli-*.ts`, `src/agents/cli-runner/*`).

## What already exists vs. what is net-new

Modeled on Hermes' existing `codex_app_server` runtime (a CLI-owns-the-loop pattern), but
the reuse is narrower than the first draft claimed:

- **Exists & reused:** the runtime *shape* — early-return dispatch in
  `agent/conversation_loop.py:787` (`if agent.api_mode == "codex_app_server": return
  agent._run_codex_app_server_turn(...)`), the forwarder `run_agent.py:4602` →
  `agent/codex_runtime.py:28`, and the MCP server **module**
  `agent/transports/hermes_tools_mcp_server.py` (a FastMCP stdio server wrapping
  `model_tools.get_tool_definitions` + `handle_function_call`).
- **Net-new (do NOT assume it exists):**
  - **Env isolation.** `codex_app_server.py:77` does `os.environ.copy()` and clears
    **nothing**. `CLEAR_ENV` is entirely new code (BL4).
  - **`--mcp-config` generation + spawn wiring.** Codex registers the MCP server via the
    user's `~/.codex/config.toml`; there is no `--mcp-config` file generation anywhere.
    Authoring the stdio config JSON + resolving interpreter/cwd/PYTHONPATH is all new (BL3).
  - **Per-turn context injection** into the bridge (BL5).

## Architecture

"Claude drives the loop." We mirror codex's **seam and return contract**. Difference:
`codex app-server` is a persistent JSON-RPC server held live across turns; Claude's `-p
--resume` is **process-per-turn** — each turn spawns a fresh `claude` resuming Claude's
stored session. `systemPromptWhen: always` re-passes the system prompt every turn
(verified correct: `helpers.ts:383`).

Rejected alternative: "Claude as a one-shot model" where Hermes extracts `tool_calls` and
runs them itself — `claude -p` owns its tool execution; feeding results back out-of-band
is fragile.

### Components (corrected anchors)

1. **`agent/claude_runtime.py`** (new) — the turn handler, mirroring
   `agent/codex_runtime.py`. Returns codex's `TurnResult`-style result, **not** a
   synthetic OpenAI response object.
2. **`agent/transports/claude_code_session.py`** (new) — mirrors
   `codex_app_server_session.py`: build argv, generate `--mcp-config`, write the
   system-prompt file, spawn the per-turn subprocess (reader thread, env denylist),
   parse stream-json, capture `session_id`, enforce serialization/watchdog/interrupt.
3. **MCP bridge** — reuse `agent/transports/hermes_tools_mcp_server.py`; generate the
   `--mcp-config` pointing at it over stdio (net-new — see BL3).
4. **`run_agent.py` wiring** (minimal): add `"claude_code_cli"` to the api_mode set at
   `agent/agent_init.py:291`; add the dispatch early-return beside
   `agent/conversation_loop.py:787` → a 2-line forwarder to `agent/claude_runtime.py`
   (mirror `run_agent.py:4602`).
5. **Selection & provider** — a **distinct `claude-code` provider** (own auth = `claude`
   login, static catalog). NOT an `openai_runtime` sub-mode (that's codex-specific and
   reuses OpenAI auth). Selection sets `api_mode="claude_code_cli"`; add a
   `/claude-runtime`-style selector and special-case it in `background_review.py`
   alongside `codex_app_server` (it bypasses chat-completion message shapes).

### `claude` invocation

```
claude -p --output-format stream-json --verbose \
  --setting-sources user \
  --permission-mode bypassPermissions \
  --mcp-config <tmp.json> --strict-mcp-config \
  --allowedTools 'mcp__hermes-tools__*' \
  --append-system-prompt-file <tmp.txt> \
  --model <resolved> \
  --session-id <uuid>           # fresh turn; resume → --resume <session_id>
```

**BL2 fix:** the FastMCP server is `FastMCP("hermes-tools")`
(`hermes_tools_mcp_server.py:126`), so Claude namespaces its tools as
`mcp__hermes-tools__*`. The allowlist glob must match that prefix exactly (or rename the
server to a hyphen-free `hermes`). A unit test asserts
`allowedTools_prefix == "mcp__" + fastmcp_server_name + "__"`.

`--include-partial-messages` is **omitted in v1** (it is dead weight until the parser
consumes streaming `stream_event` partials — see Stream parsing). Prompt is fed via stdin.

### Permission posture & trust scope (BL6)

**Decision: trusted-local-operator only, default `--permission-mode bypassPermissions`.**

- Because native tools cannot be sandboxed via `--allowedTools`, and `bypassPermissions`
  runs them with zero approval, the engine is **hard-gated to trusted local-operator
  contexts**. It must **refuse with a clear error** when invoked from any
  untrusted-carrying context — channel/gateway-delivered messages, `delegate_task`
  sub-agents, or any non-interactive remote trigger. (Mechanism: the runtime inspects the
  execution context Hermes already tracks for these paths; exact predicate resolved in the
  implementation plan.)
- Default posture `bypassPermissions`; overridable via `--claude-permission-mode <value>`,
  `HERMES_CLAUDE_PERMISSION_MODE`, or `tools.terminal.security_mode`
  (`approval-required` → `plan` read-only; everything else → `bypassPermissions`).
- The **resolved posture and the trusted-context gate result are logged** at turn start
  and surfaced by doctor — never silent.
- `--setting-sources user` additionally loads the user's `~/.claude` hooks/settings — a
  documented trust expansion, acceptable only under the trusted-local scope above.

### Per-turn context injection (BL5 — security gate, not just tenancy)

The bridge currently calls `handle_function_call(name, kwargs)` with no `task_id` /
`session_id` / `enabled_toolsets` (`hermes_tools_mcp_server.py:165`). `enabled_toolsets`
is the **gate that blocks out-of-scope tool use** (`model_tools.py:885`); `task_id`
scopes terminal/browser session isolation (`model_tools.py:820`). Losing them is a
security regression. Because Claude is process-per-turn, inject per-turn context as **env
on each spawn** (`HERMES_SESSION_ID`, `HERMES_KANBAN_TASK`, `task_id`,
`enabled_toolsets`) and have the stdio bridge thread them into `handle_function_call`.
This dovetails with the BL4 denylist (kanban env already works this way —
`kanban_tools.py:74/101/124`).

### Env isolation (BL4 — enumerated denylist, never wildcard)

- `CLEAR_ENV` is **new code** (codex clears nothing). It must be an **explicit enumerated
  auth-var list ported verbatim from OpenClaw `cli-shared.ts:20`** — **not** a
  `ANTHROPIC_*/CLAUDE_CODE_*/OTEL_*` wildcard. A blanket clear would wipe the
  `HERMES_*` context env the MCP bridge depends on (BL5).
- Clear `CLAUDE_CONFIG_DIR` (use the default login tree; OpenClaw clears it —
  `cli-shared.test.ts:310`) and **require interactive `claude` login**. Explicitly flag
  the breakage for users who authed Hermes via `claude setup-token` (env-var token):
  Hermes has that path at `agent/anthropic_adapter.py:1159`, and clearing the env means
  the spawned CLI won't see it.

### Session, serialization, watchdog, retire (MA3)

- **Process-wide lock map keyed by resolved `session_id`** (Claude is per-turn spawn, not
  a live session) so concurrent `--resume <same-id>` can't interleave. (`serialize: true`
  — `cli-backend.ts:79`.)
- **Fresh vs. resume watchdog split** (resume turns are slower to first byte) + overall
  turn deadline; classify `no-output-timeout` / `overall-timeout` / non-zero-exit.
- **`should_retire` / drop-`session_id`-and-fall-back-to-fresh** on corrupted/desynced
  sessions (mirror `codex_runtime.py:96-105`). Account "one turn == one api_call"
  (`codex_runtime.py:167`).
- **Interrupt** — wire Hermes' `interrupt()` to kill the subprocess / close stdin.

### Stream parsing (MA1 — explicit v1 scope)

`cli-output.ts` handles much more than the final `result`. **v1 scope:** parse `result`
(final text + `session_id` + `usage` + error via `is_error`/`subtype`), `type:"assistant"`
text, `type:"error"`, and `tool_use` / `*_tool_result` (+ `input_json_delta`
accumulation) **for message-row projection** — because the curator/sessions DB expects
tool-event rows (`codex_runtime.py:110`). **Deferred:** live `stream_event` /
`content_block_delta` streaming deltas (hence `--include-partial-messages` is omitted; the
UI shows the result at turn completion, not incrementally). Tolerate per-line JSON parse
errors; cap parse buffer; refuse to parse truncated output. Capture `session_id` on the
empty-result path (tool-only turns finish with empty text — not a failure).
`SESSION_ID_FIELDS = [session_id, sessionId, conversation_id, conversationId]`.

### MCP bridge details

- Generated stdio `--mcp-config` shape:
  `{"mcpServers":{"hermes-tools":{"command":"<python>","args":["-m",
  "agent.transports.hermes_tools_mcp_server"], "env": {<per-turn HERMES_* context>}}}}`
  — resolve interpreter / cwd / PYTHONPATH so the spawned server imports `model_tools`.
- `--strict-mcp-config` so Claude can't reach the user's other MCP servers (contains MCP
  leakage only — does NOT restrict native tools).
- **`EXPOSED_TOOLS` is additive, not a sandbox.** The existing list
  (`hermes_tools_mcp_server.py:59`) omits terminal/file tools "because codex has
  built-ins" — for Claude, native tools stay on anyway, so the bridge's value is the
  *additive* tools Claude lacks (web/browser/vision/image/tts). Document this rationale.

## Documented limitations / ceilings

- **`_AGENT_LOOP_TOOLS = {todo, memory, session_search, delegate_task}`**
  (`model_tools.py:556`) need live `AIAgent` context → cannot cross the MCP boundary.
- **Native-tool guardrail gap:** Hermes' terminal/edit security does not govern Claude's
  native `Bash`/`Write`/`Edit` — the trusted-local gate (BL6) is the mitigation.
- Browser/terminal **in-memory session continuity** can't cross the stdio boundary even
  with `task_id` env (separate process per turn).
- Transport is **stdio** (single trusted local agent). OpenClaw's http-loopback +
  per-request token/scope headers (multi-tenant) is out of scope.

## Git / fork strategy (mirrors `yerx-openclaw`)

`origin` → `yerx/yerx-hermes`, `upstream` → `NousResearch/hermes-agent`. Work on
`custom/claude-code-engine`. Upstream sync: `git fetch upstream && git rebase
upstream/main` onto a dated `custom/claude-code-engine-rebase-YYYY-MM-DD` branch.

## Out of scope (deferred)

Image input; persistent Claude live-session (`--input-format stream-json`); live
streaming deltas; transcript re-seeding beyond `systemPromptWhen: always`; http-loopback
MCP transport / per-request scoping; full model-alias + `--effort`/thinking-level parity;
true per-call MCP-side approval (the `approval-required` interactive gate).

## Testing

`pytest` units: argv builder (fresh/resume); **allowedTools glob prefix ==
`mcp__<fastmcp-name>__`** (catches BL2); **`--mcp-config` file authoring** (riskiest
net-new bit, BL3); enumerated env denylist (BL4) + per-turn context env injection (BL5);
stream-json parser fixtures incl. empty-result, `type:"error"`, and tool_use/tool_result
projection; session lock-map + fresh/resume watchdog classification + `should_retire`
drop-session; trusted-context gate refuses untrusted-carrying contexts (BL6). Live
integration test **skipped when `claude` not on PATH** (reuse `shutil.which("claude")`
precedent, `agent/anthropic_adapter.py:1159`; mirrors OpenClaw `liveTest`).

## Key files to read before implementing

Hermes: `agent/conversation_loop.py:787`, `run_agent.py:4602`, `agent/codex_runtime.py`,
`agent/transports/codex_app_server_session.py`, `agent/transports/codex_app_server.py:77`,
`agent/transports/hermes_tools_mcp_server.py` (`:59` EXPOSED_TOOLS, `:126` name, `:165`
dispatch), `agent/agent_init.py:291`, `model_tools.py:556`/`:820`/`:885`,
`agent/anthropic_adapter.py:1159`, `background_review.py` (codex special-case),
`tools/kanban_tools.py:74`.
OpenClaw: `extensions/anthropic/cli-shared.ts:20`, `cli-backend.ts:31`/`:43`/`:79`,
`src/agents/cli-runner/prepare.ts:168`, `helpers.ts:383`, `src/agents/cli-output.ts`.
