"""Run one agent turn and report its activity as events (activity panel, PROPOSAL §6).

Two ways to run the SAME turn, both returning exactly what ``ainvoke`` returns (the
finished state's ``messages``), so ``agent.ask`` answers the same text either way:

- :func:`stream_turn` — the live path: ``astream(stream_mode=["messages", "updates",
  "values"], version="v2", subgraphs=True)``. ``subgraphs=True`` is what lets the
  ``vision_subagent`` graph's nested ReAct agent stream at all; the ROOT's last
  ``values`` part is the finished state.
- :func:`invoke_turn` — the ``ask_your_docs.ui.activity.live: false`` path: one plain
  ``ainvoke`` (the model is never asked to stream, for servers that stream tool calls
  badly), then this turn's messages replayed as untimed events.

WHY no ``asyncio.shield`` around in-flight tool calls: the page's held serve session
(``serve_session.py``) owns cancellation. A turn the user stops is cancelled whole, and
the next turn's liveness probe replaces a session a cancelled call left unusable.

Pure asyncio: langchain arrives only through the graph object the caller hands in.

Example:
    messages = await stream_turn(graph, {"messages": [question]}, events.append)
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from pydocs_mcp.harness.ask_your_docs.activity_events import (
    ActivityEvent,
    events_from_messages,
    events_from_stream_part,
)

# "values" rides along only for the ROOT's finished state; the translation ignores it.
STREAM_MODES = ("messages", "updates", "values")

ActivitySink = Callable[[ActivityEvent], None]


async def stream_turn(agent: Any, payload: Mapping[str, Any], sink: ActivitySink) -> list[Any]:
    """Stream one turn, handing ``sink`` every event stamped with seconds since it began."""
    started = time.perf_counter()
    final: list[Any] = []
    parts = agent.astream(payload, stream_mode=list(STREAM_MODES), version="v2", subgraphs=True)
    async for part in parts:
        for event in events_from_stream_part(part, at=time.perf_counter() - started):
            sink(event)
        final = _root_state_messages(part) or final
    return final


async def invoke_turn(agent: Any, payload: Mapping[str, Any], sink: ActivitySink) -> list[Any]:
    """Run one turn with ``ainvoke``, then hand ``sink`` this turn's events, untimed."""
    result = await agent.ainvoke(payload)
    messages = list(result["messages"])
    for event in events_from_messages(messages[len(payload["messages"]) :]):
        sink(event)
    return messages


def _root_state_messages(part: Mapping[str, Any]) -> list[Any] | None:
    """The finished-so-far ROOT state's messages; subgraph states (non-empty ``ns``) skipped."""
    if part.get("type") != "values" or part.get("ns"):
        return None
    state = part.get("data")
    messages = state.get("messages") if isinstance(state, Mapping) else None
    return list(messages) if messages else None


__all__ = ("STREAM_MODES", "ActivitySink", "invoke_turn", "stream_turn")
