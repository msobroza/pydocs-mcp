"""Was the call needed? — the needless-call rate and its three companions.

Four metrics over the recorded tool events of ONE trajectory, each a pure
function with its own unit test (the R3 metric-library rule: no metric has a
second implementation anywhere):

- **needless-call rate** — the share of tool calls that resurfaced content the
  trajectory had already seen, yielded nothing, fanned out where one batch call
  would do, or used a tool that does not fit the shape of its input. The four
  components are exposed on their own as well as through the rate.
- **pointer-followed rate** — the share of the follow-up calls an earlier
  response offered that the model went on to issue.
- **parallel calls per turn** — the mean number of tool calls per turn that
  called any tool.
- **batch-versus-fan-out ratio** — the share of target-fetching calls issued as
  one batch call rather than one target at a time.

**Empty denominators, decided once and tested.** A rate whose denominator counts
CALLS reads ``0.0`` when the trajectory made none — no call was needless, no
call ran in parallel. A rate whose denominator counts OPPORTUNITIES the server
created — pointers offered, target-fetching calls — reads ``None`` when there
were none: an undefined value must not be averaged into a before/after
comparison as though it were a measured zero.

Vocabulary follows the repo glossary: needed call, needless call, resurfacing,
batch call, pointer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.trajectory.blob_store import canonical_json, read_result_blob
from pydocs_eval.trajectory.pointer_lines import PointerCall, parse_pointer_calls
from pydocs_eval.trajectory.schema import ToolEvent

# The batch counterpart of every tool whose single-target calls fan out. ONE
# constant is the whole mapping: a tool absent from it can never be charged as
# fan-out (the reference tool takes one target and has no batched form). The
# symbol tool's counterpart is the context tool, which takes several targets;
# the context tool is its own counterpart, since a one-target context call fans
# out where the multi-target form would do.
BATCH_COUNTERPART: Mapping[str, str] = {
    "get_symbol": "get_context",
    "get_context": "get_context",
}

# The single- and multi-target argument names on those tools.
_TARGET_ARGUMENT = "target"
_TARGETS_ARGUMENT = "targets"
_MIN_BATCH_TARGETS = 2

# Three single-target calls of one tool in one turn is the fan-out one batch
# call replaces (the pointer table's batch threshold).
_FAN_OUT_THRESHOLD = 3

# A query shaped like a dotted path — a name the symbol tool resolves directly,
# so sending it to the search tool is the wrong tool for the shape of the input.
_DOTTED_PATH_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")
_SEARCH_TOOL = "search_codebase"
_QUERY_ARGUMENT = "query"


# ---------------------------------------------------------------------------
# Call shapes
# ---------------------------------------------------------------------------


def is_batch_call(event: ToolEvent) -> bool:
    """True when one call carries several targets (a batch call).

    Example:
        >>> from pydocs_eval.trajectory.schema import ToolEvent
        >>> e = ToolEvent(event_id="e", trajectory_id="t", seq=1, ts=0.0, turn=1,
        ...     tool="get_context", args={"targets": ["a.B", "c.D"]}, latency_ms=1.0)
        >>> is_batch_call(e)
        True
    """
    targets = event.args.get(_TARGETS_ARGUMENT)
    return isinstance(targets, (list, tuple)) and len(targets) >= _MIN_BATCH_TARGETS


def is_single_target_call(event: ToolEvent) -> bool:
    """True when the call names exactly one target of a tool that has a batch form."""
    if event.tool not in BATCH_COUNTERPART:
        return False
    targets = event.args.get(_TARGETS_ARGUMENT)
    if isinstance(targets, (list, tuple)):
        return len(targets) == 1
    return isinstance(event.args.get(_TARGET_ARGUMENT), str)


# ---------------------------------------------------------------------------
# The four components
# ---------------------------------------------------------------------------


def resurfacing_calls(tool_events: Iterable[ToolEvent]) -> frozenset[int]:
    """Seqs of calls whose every result identifier was already seen earlier.

    Rows compare by their full identifier atoms, canonicalized, so two rows are
    the same row only when path, span and name all agree. A call that surfaced
    NO identifier is never resurfacing — it surfaced nothing, which is what
    :func:`zero_yield_calls` charges instead.
    """
    seen: set[str] = set()
    resurfaced: set[int] = set()
    for event in sorted(tool_events, key=lambda e: e.seq):
        rows = {canonical_json(row) for row in event.result_ids or ()}
        if rows and rows <= seen:
            resurfaced.add(event.seq)
        seen |= rows
    return frozenset(resurfaced)


def zero_yield_calls(tool_events: Iterable[ToolEvent]) -> frozenset[int]:
    """Seqs of calls that returned no identifier and did not fail.

    A failed call is excluded: it already reports its own failure, and charging
    it here would count one defect twice. A result the recorder could not
    distill into identifiers counts, because no identifier reached the trace.
    """
    return frozenset(e.seq for e in tool_events if e.error is None and not e.result_ids)


def fan_out_where_batch_calls(tool_events: Iterable[ToolEvent]) -> frozenset[int]:
    """Seqs of single-target calls that fanned out where one batch call would do.

    Grouped per (turn, tool): a group of :data:`_FAN_OUT_THRESHOLD` or more
    single-target calls of a tool listed in :data:`BATCH_COUNTERPART` is charged
    IN FULL — one batch call would have replaced the whole group, so every call
    in it is needless, not all but one.
    """
    groups: dict[tuple[int, str], list[int]] = {}
    for event in tool_events:
        if is_single_target_call(event):
            groups.setdefault((event.turn, event.tool), []).append(event.seq)
    return frozenset(
        seq for seqs in groups.values() if len(seqs) >= _FAN_OUT_THRESHOLD for seq in seqs
    )


def tool_mismatch_calls(tool_events: Iterable[ToolEvent]) -> frozenset[int]:
    """Seqs of searches whose query is shaped like a dotted path."""
    return frozenset(
        e.seq
        for e in tool_events
        if e.tool == _SEARCH_TOOL and _is_dotted_path(e.args.get(_QUERY_ARGUMENT))
    )


def _is_dotted_path(query: object) -> bool:
    """True for ``pkg.mod.Name``; false for prose, a bare name, or a file path."""
    return isinstance(query, str) and _DOTTED_PATH_RE.match(query) is not None


# ---------------------------------------------------------------------------
# The needless-call rate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NeedlessCallReport:
    """One trajectory's needless calls, by component and as a rate.

    Each component holds the seqs it charged, so a call charged by two
    components counts ONCE in :attr:`needless`: the rate is a share of calls,
    never a sum of component counts.
    """

    resurfacing: frozenset[int]
    zero_yield: frozenset[int]
    fan_out_where_batch: frozenset[int]
    tool_mismatch: frozenset[int]
    total_calls: int

    @property
    def needless(self) -> frozenset[int]:
        """Every seq at least one component charged."""
        return self.resurfacing | self.zero_yield | self.fan_out_where_batch | self.tool_mismatch

    @property
    def rate(self) -> float:
        """``|needless| / |calls|``; ``0.0`` when the trajectory made no call."""
        if not self.total_calls:
            return 0.0
        return len(self.needless) / self.total_calls

    def to_dict(self) -> dict[str, object]:
        """The rate, the call totals, and one count per component."""
        return {
            "needless_call_rate": self.rate,
            "needless_calls": len(self.needless),
            "total_calls": self.total_calls,
            "resurfacing": len(self.resurfacing),
            "zero_yield": len(self.zero_yield),
            "fan_out_where_batch": len(self.fan_out_where_batch),
            "tool_mismatch": len(self.tool_mismatch),
        }


def needless_call_report(tool_events: Iterable[ToolEvent]) -> NeedlessCallReport:
    """Charge every component over one trajectory's tool calls."""
    events = tuple(tool_events)
    return NeedlessCallReport(
        resurfacing=resurfacing_calls(events),
        zero_yield=zero_yield_calls(events),
        fan_out_where_batch=fan_out_where_batch_calls(events),
        tool_mismatch=tool_mismatch_calls(events),
        total_calls=len(events),
    )


def needless_call_rate(tool_events: Iterable[ToolEvent]) -> float:
    """Share of tool calls at least one component charged as needless.

    ``0.0`` for a trajectory with no tool call (nothing was needless).
    """
    return needless_call_report(tool_events).rate


# ---------------------------------------------------------------------------
# Where a response's text comes from
# ---------------------------------------------------------------------------

ResponseTextReader = Callable[[ToolEvent], str | None]


def response_text_from_preview(event: ToolEvent) -> str | None:
    """The default text source: the preview the recorder stored on the event.

    The preview is a byte-capped prefix of the serialized result, so a response
    long enough to push its pointers past the cap will under-report them; a run
    with its blob store at hand should read through :class:`ResponseTextFromBlobs`
    instead.
    """
    return event.result_preview


@dataclass(frozen=True, slots=True)
class ResponseTextFromBlobs:
    """Reads the FULL response text out of the run's content-addressed blob store.

    Each tool event names the blob holding its whole serialized result envelope;
    the text body is the ``text`` key of that envelope. A blob that is missing or
    unreadable — an older capture, a pruned store — reads as no text rather than
    an error, because a metric must never fail a run it is only measuring.
    """

    blobs_dir: Path

    def __call__(self, event: ToolEvent) -> str | None:
        if event.result_blob is None:
            return None
        raw = read_result_blob(self.blobs_dir, event.result_blob)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        text = payload.get("text") if isinstance(payload, dict) else None
        return text if isinstance(text, str) else None


# ---------------------------------------------------------------------------
# The three companions
# ---------------------------------------------------------------------------


def pointer_followed_rate(
    tool_events: Iterable[ToolEvent],
    *,
    response_text: ResponseTextReader = response_text_from_preview,
) -> float | None:
    """Share of the DISTINCT pointers offered that a later call issued.

    A pointer counts as followed when a call that comes AFTER the response
    offering it matches the pointer's tool and every argument it named. Distinct
    pointers, not pointer renderings: one pointer repeated by five responses is
    one opportunity, so the rate stays inside [0, 1].

    ``None`` when the trajectory was offered no pointer at all — an undefined
    rate, not a zero, so that "nothing was offered" never averages in as "every
    pointer was ignored".
    """
    offered: dict[PointerCall, bool] = {}
    for event in sorted(tool_events, key=lambda e: e.seq):
        _mark_followed(offered, event)
        _record_offered(offered, parse_pointer_calls(response_text(event) or ""))
    if not offered:
        return None
    return sum(offered.values()) / len(offered)


def _mark_followed(offered: dict[PointerCall, bool], event: ToolEvent) -> None:
    """Flag every not-yet-followed pointer this call issues."""
    followed = [p for p, done in offered.items() if not done and p.matches(event.tool, event.args)]
    offered.update(dict.fromkeys(followed, True))


def _record_offered(offered: dict[PointerCall, bool], pointers: Sequence[PointerCall]) -> None:
    """Add newly offered pointers, keeping the flag of ones already offered."""
    for pointer in pointers:
        offered.setdefault(pointer, False)


def parallel_calls_per_turn(tool_events: Iterable[ToolEvent]) -> float:
    """Mean tool calls per turn that called any tool; ``0.0`` when none did.

    Turns without a tool call are not in the denominator: the question is how
    many calls the model issues at once, which a silent turn cannot dilute.
    """
    events = tuple(tool_events)
    turns = {event.turn for event in events}
    if not turns:
        return 0.0
    return len(events) / len(turns)


@dataclass(frozen=True, slots=True)
class BatchFanoutSplit:
    """How many target-fetching calls were batched, and how many fanned out."""

    batch_calls: int
    fan_out_calls: int

    @property
    def ratio(self) -> float | None:
        """Batched share ``batch / (batch + fan-out)``; ``None`` when neither happened.

        A share rather than a literal batch-to-fan-out quotient, so a trajectory
        with no fan-out reads ``1.0`` instead of dividing by zero, and the number
        stays comparable across runs of different lengths.
        """
        total = self.batch_calls + self.fan_out_calls
        if not total:
            return None
        return self.batch_calls / total


def batch_fanout_split(tool_events: Iterable[ToolEvent]) -> BatchFanoutSplit:
    """Count the batch calls and the single-target fan-out calls."""
    events = tuple(tool_events)
    return BatchFanoutSplit(
        batch_calls=sum(1 for event in events if is_batch_call(event)),
        fan_out_calls=sum(1 for event in events if is_single_target_call(event)),
    )


def batch_vs_fanout_ratio(tool_events: Iterable[ToolEvent]) -> float | None:
    """Batched share of the target-fetching calls; ``None`` when there were none."""
    return batch_fanout_split(tool_events).ratio


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallEfficiency:
    """The four needed-call metrics of one trajectory, bundled for the metric layer."""

    needless: NeedlessCallReport
    pointer_followed_rate: float | None
    parallel_calls_per_turn: float
    batch_fanout: BatchFanoutSplit

    def to_dict(self) -> dict[str, object]:
        """Report-ready values, JSON types only (``None`` means undefined, not zero)."""
        return {
            **self.needless.to_dict(),
            "pointer_followed_rate": self.pointer_followed_rate,
            "parallel_calls_per_turn": self.parallel_calls_per_turn,
            "batch_vs_fanout_ratio": self.batch_fanout.ratio,
            "batch_calls": self.batch_fanout.batch_calls,
            "fan_out_calls": self.batch_fanout.fan_out_calls,
        }


def compute_call_efficiency(
    tool_events: Iterable[ToolEvent],
    *,
    response_text: ResponseTextReader = response_text_from_preview,
) -> CallEfficiency:
    """Compute all four metrics over one trajectory's tool events."""
    events = tuple(tool_events)
    return CallEfficiency(
        needless=needless_call_report(events),
        pointer_followed_rate=pointer_followed_rate(events, response_text=response_text),
        parallel_calls_per_turn=parallel_calls_per_turn(events),
        batch_fanout=batch_fanout_split(events),
    )
