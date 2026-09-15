"""One turn budget, two callers: the eval binding and the chat UI.

A budget is counted in LangGraph super-steps, never in tool calls — a
super-step alternates model turn / tool execution, and the prebuilt tool node
runs ALL of one message's tool calls inside a single step. So a turn that
issues four searches at once costs exactly what a turn that issues one costs,
which is what makes parallel calls affordable.

Both turn paths derive their run config here so the chat page and a campaign
cannot answer "how long may one turn run" differently.

Example:
    >>> turn_run_config(12)
    {'recursion_limit': 24}
"""

from __future__ import annotations

from pydocs_mcp.retrieval.config.ask_your_docs_models import _DEFAULT_MAX_AGENT_TURNS

# WHY 2: a super-step is model turn + tool execution, so one agent turn costs
# two graph steps. This mapping is the eval runner's established one; changing
# it changes every budget at once, which is the point of keeping it here.
_SUPER_STEPS_PER_TURN = 2


def turn_run_config(max_agent_turns: int | None = None) -> dict[str, int]:
    """The LangGraph run config bounding one turn; ``None`` = the YAML block's default."""
    turns = _DEFAULT_MAX_AGENT_TURNS if max_agent_turns is None else max_agent_turns
    return {"recursion_limit": _SUPER_STEPS_PER_TURN * turns}


__all__ = ("turn_run_config",)
