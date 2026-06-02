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
            res.error = "claude stream output exceeded parse cap (truncated)"
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
