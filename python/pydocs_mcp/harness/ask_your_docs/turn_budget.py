"""One turn budget, two callers: the eval binding and the chat UI.

A budget is counted in LangGraph super-steps, never in tool calls — a
super-step alternates model turn / tool execution, and the prebuilt tool node
runs ALL of one message's tool calls inside a single step. So a turn that
issues four searches at once costs exactly what a turn that issues one costs,
which is what makes parallel calls affordable.

Both turn paths derive their run config here so the chat page and a campaign
cannot answer "how long may one turn run" differently.

The budget's END lives here too. The prebuilt ReAct agent never raises at the
cap: it swaps the reply that would have called tools past the budget for a
canned apology and returns normally, so a run that exhausted its budget looks
like a run that answered. :func:`is_budget_exhausted_reply` is the one test for
that reply.

Example:
    >>> turn_run_config(12)
    {'recursion_limit': 24}
"""

from __future__ import annotations

from typing import Any

from pydocs_mcp.retrieval.config.ask_your_docs_models import _DEFAULT_MAX_AGENT_TURNS

# WHY 2: a super-step is model turn + tool execution, so one agent turn costs
# two graph steps. This mapping is the eval runner's established one; changing
# it changes every budget at once, which is the point of keeping it here.
_SUPER_STEPS_PER_TURN = 2

# The reply LangGraph's prebuilt agent returns in place of a tool-calling reply
# once too few steps remain to run the tools (langgraph 1.2.8
# ``prebuilt/chat_agent_executor.py`` lines 689 and 716). Copied, never imported:
# langgraph exports no name for it. ``test_turn_budget.py`` drives the real
# prebuilt past its budget, so a changed literal fails that one test; the eval's
# ``ASK_BUDGET_EXHAUSTED_REPLY`` mirrors this constant under its own parity test.
BUDGET_EXHAUSTED_REPLY = "Sorry, need more steps to process this request."

# langchain's tag for a model message; duck-typed like ``model_usage`` so the
# check needs no langchain import.
_AI_MESSAGE_TYPE = "ai"


def turn_run_config(max_agent_turns: int | None = None) -> dict[str, int]:
    """The LangGraph run config bounding one turn; ``None`` = the YAML block's default."""
    turns = _DEFAULT_MAX_AGENT_TURNS if max_agent_turns is None else max_agent_turns
    return {"recursion_limit": _SUPER_STEPS_PER_TURN * turns}


def is_budget_exhausted_reply(message: Any) -> bool:
    """Whether ``message`` is the prebuilt agent's budget-exhausted apology.

    A model message with no tool calls whose text is exactly
    :data:`BUDGET_EXHAUSTED_REPLY`. Deliberately no second signal: the apology
    also carries no ``usage_metadata``, but so does any reply from an endpoint
    that reports none, so that absence proves nothing.

    Example:
        >>> class _Reply:
        ...     type, tool_calls, content = "ai", [], BUDGET_EXHAUSTED_REPLY
        >>> is_budget_exhausted_reply(_Reply())
        True
    """
    return (
        getattr(message, "type", "") == _AI_MESSAGE_TYPE
        and not getattr(message, "tool_calls", None)
        and getattr(message, "content", None) == BUDGET_EXHAUSTED_REPLY
    )


__all__ = ("BUDGET_EXHAUSTED_REPLY", "is_budget_exhausted_reply", "turn_run_config")
