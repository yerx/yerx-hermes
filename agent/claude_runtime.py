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
            "claude_session_id": None,
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
