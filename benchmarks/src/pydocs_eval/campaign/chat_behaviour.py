"""The seven pre-registered chat behaviour counts — what the turn-efficiency ladder watches.

The step's primary endpoints (the turn-efficiency analysis, ``final_report`` item 5):
``get_overview`` calls, an overview issued beside the model's first search, example-chain
calls, source→``read_file`` pairs, unbounded file reads, gap-marker responses and zero-hit
greps. The spec names them without defining them, so each definition below is operational
and grounded in the behaviour the analysis observed. They count the MODEL's calls: the
harness's seeded search is not one of them.

Duck-typed on langchain messages (``.type``, ``.content``, ``.tool_calls``,
``.artifact``); langchain-free and product-free at import.

Example:
    >>> behaviour_counts(observed_calls(messages))["get_overview_calls"]  # doctest: +SKIP
    1
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Tool names from the frozen nine-tool contract (docs/tool-contracts.md).
_OVERVIEW = "get_overview"
_SEARCH = "search_codebase"
_READ_FILE = "read_file"
_GREP = "grep"
_SOURCE_VIEWS = frozenset({"get_symbol", "get_context"})
# What a source view says about lines no chunk claims ("[lines 10-36 not in the index]").
_GAP_MARKER = "not in the index"


@dataclass(frozen=True, slots=True)
class ObservedCall:
    """One model-issued call and what came back: the turn that proposed it (from 1)."""

    turn: int
    tool: str
    args: Mapping[str, object]
    result_text: str
    result_items: int | None


def observed_calls(messages: Sequence[Any]) -> tuple[ObservedCall, ...]:
    """The model's calls in order, each joined to its result by ``tool_call_id``."""
    results = {getattr(m, "tool_call_id", ""): m for m in messages if _is_tool(m)}
    calls: list[ObservedCall] = []
    for turn, message in enumerate(model_turns(messages), start=1):
        for call in getattr(message, "tool_calls", None) or []:
            calls.append(_observed(turn, call, results.get(call.get("id", ""))))
    return tuple(calls)


def model_turns(messages: Sequence[Any]) -> list[Any]:
    """The model's replies — every AI message except the harness's seeded proposal."""
    return [m for m in messages if getattr(m, "type", "") == "ai" and not _is_seeded(m)]


def message_text(content: Any) -> str:
    """A message ``content`` as plain text (a string, or the text of its text blocks)."""
    from pydocs_mcp.harness.ask_your_docs.activity_events import content_text

    return content_text(content)


def behaviour_counts(calls: Sequence[ObservedCall]) -> dict[str, int]:
    """The seven pre-registered chat behaviour counts, one stdlib pass each."""
    return {name: count(calls) for name, count in _BEHAVIOUR_COUNTS.items()}


def calls_by_tool(calls: Sequence[ObservedCall]) -> dict[str, int]:
    return dict(Counter(call.tool for call in calls))


# WHY the product is imported inside ``message_text`` and ``_is_seeded``: campaign modules
# never import pydocs_mcp at module import time — the before/after parent drives two
# product commits, and only the process under test may load one.
def _is_seeded(message: Any) -> bool:
    from pydocs_mcp.harness.ask_your_docs.first_turn import is_seeded_search

    return is_seeded_search(message)


def _is_tool(message: Any) -> bool:
    return getattr(message, "type", "") == "tool"


def _observed(turn: int, call: Mapping[str, Any], result: Any) -> ObservedCall:
    return ObservedCall(
        turn=turn,
        tool=str(call.get("name", "")),
        args=dict(call.get("args") or {}),
        result_text=message_text(result.content) if result is not None else "",
        result_items=_result_items(result),
    )


def _result_items(result: Any) -> int | None:
    """How many rows the tool's structured envelope returned; ``None`` when it sent none."""
    artifact = getattr(result, "artifact", None)
    structured = artifact.get("structured_content") if isinstance(artifact, Mapping) else None
    items = structured.get("items") if isinstance(structured, Mapping) else None
    return len(items) if isinstance(items, list) else None


# ── the seven counts ──


def _get_overview_calls(calls: Sequence[ObservedCall]) -> int:
    return sum(call.tool == _OVERVIEW for call in calls)


def _overview_parallel_with_first_search(calls: Sequence[ObservedCall]) -> int:
    """1 when a ``get_overview`` shares the turn of the model's first search, else 0."""
    first_search = next((call.turn for call in calls if call.tool == _SEARCH), None)
    if first_search is None:
        return 0
    return int(any(c.tool == _OVERVIEW and c.turn == first_search for c in calls))


def _example_chain_calls(calls: Sequence[ObservedCall]) -> int:
    """Every source view on a target the question asks more than once — the whole chain.

    The chain system_v2 rule 5 ("use get_symbol for the exact signature") prompted: a card,
    then its context, then its source, all of one symbol. q04's chain was 5 of its 8 calls;
    its first call belongs to the chain too, so it counts.
    """
    views = [call for call in calls if call.tool in _SOURCE_VIEWS]
    asked = Counter(target for call in views for target in _targets_of(call))
    return sum(any(asked[target] > 1 for target in _targets_of(call)) for call in views)


def _targets_of(call: ObservedCall) -> set[str]:
    target, targets = call.args.get("target"), call.args.get("targets")
    named = {target} if isinstance(target, str) else set()
    if isinstance(targets, list):
        named.update(t for t in targets if isinstance(t, str))
    return named


def _source_then_read_same_file(calls: Sequence[ObservedCall]) -> int:
    """``read_file`` calls on a path an EARLIER turn's source view already showed."""
    return sum(
        call.tool == _READ_FILE and _shown_before(str(call.args.get("path", "")), call.turn, calls)
        for call in calls
    )


def _shown_before(path: str, turn: int, calls: Sequence[ObservedCall]) -> bool:
    views = (c for c in calls if c.tool in _SOURCE_VIEWS and c.turn < turn)
    return bool(path) and any(path in view.result_text for view in views)


def _whole_file_reads(calls: Sequence[ObservedCall]) -> int:
    """``read_file`` calls with no ``limit`` — an offset-only read still runs to EOF."""
    return sum(call.tool == _READ_FILE and call.args.get("limit") is None for call in calls)


def _gap_marker_responses(calls: Sequence[ObservedCall]) -> int:
    return sum(_GAP_MARKER in call.result_text for call in calls)


def _grep_zero_hits(calls: Sequence[ObservedCall]) -> int:
    return sum(call.tool == _GREP and call.result_items == 0 for call in calls)


_BEHAVIOUR_COUNTS: Mapping[str, Callable[[Sequence[ObservedCall]], int]] = {
    "get_overview_calls": _get_overview_calls,
    "overview_parallel_with_first_search": _overview_parallel_with_first_search,
    "example_chain_calls": _example_chain_calls,
    "source_then_read_same_file": _source_then_read_same_file,
    "whole_file_reads": _whole_file_reads,
    "gap_marker_responses": _gap_marker_responses,
    "grep_zero_hits": _grep_zero_hits,
}


__all__ = (
    "ObservedCall",
    "behaviour_counts",
    "calls_by_tool",
    "message_text",
    "model_turns",
    "observed_calls",
)
