"""The single source of trajectory metrics (ADR 0011 + plan R3 metric library).

Every metric here is a pure function with an explicit formula docstring and its
own unit test — no duplicate metric code paths live anywhere else (R3). Four
layers, per the plan:

- **localization** — gold-file recall, wasted-read ratio, hunk overlap (emitted
  ONLY from span-bearing evidence, per-file fidelity-stamped), tool-calls-to-
  first-gold;
- **per-tool evidence yield by tier** — surfaced / inspected / used file counts
  each tool earned;
- **edit layer** — patch-applies, F2P fraction, P2P regression count (from the
  ``eval_report.GroundTruthOutcome``);
- **cost layer** — tokens deduped by ``message_id`` (the ``_parse.py`` over-count
  trap) with the result-envelope run total excluded and exposed separately, calls
  by tool, turns, wall-clock, ``cost_usd``.

Fidelity honesty (ADR 0011): the hunk-overlap report separates files with
hunk-level evidence from file-level-only files, so a hunk number is never
fabricated from a span-less source (member/decision/references rows, glob,
overview).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydocs_eval.trajectory.attribution import Attribution, Fidelity
from pydocs_eval.trajectory.eval_report import GroundTruthOutcome, normalize_test_name
from pydocs_eval.trajectory.path_normalizer import normalize_path
from pydocs_eval.trajectory.schema import LoopEvent, ToolEvent

# ---------------------------------------------------------------------------
# Localization layer
# ---------------------------------------------------------------------------


def gold_file_recall(surfaced_files: frozenset[str], gold_files: frozenset[str]) -> float:
    """Fraction of gold files the trajectory surfaced: ``|surfaced ∩ gold| / |gold|``.

    Empty gold (no files to find) is vacuously perfect → ``1.0``.

    Example:
        >>> gold_file_recall(frozenset({"a"}), frozenset({"a", "b"}))
        0.5
    """
    if not gold_files:
        return 1.0
    return len(surfaced_files & gold_files) / len(gold_files)


def wasted_read_ratio(inspected_files: frozenset[str], used_files: frozenset[str]) -> float:
    """Share of inspected files never edited: ``|inspected \\ used| / |inspected|``.

    No inspection → ``0.0`` (nothing was wasted).

    Example:
        >>> wasted_read_ratio(frozenset({"a", "b"}), frozenset({"a"}))
        0.5
    """
    if not inspected_files:
        return 0.0
    return len(inspected_files - used_files) / len(inspected_files)


def hunk_overlap(seen_lines: frozenset[int], gold_lines: frozenset[int]) -> float:
    """Fraction of gold-edited lines an inspection covered: ``|seen ∩ gold| / |gold|``.

    ``seen_lines`` is the union of inspected span line numbers for one file;
    ``gold_lines`` is that file's gold target-line set
    (:func:`gold_diff.target_line_map`). Empty gold lines → ``1.0``.

    Example:
        >>> hunk_overlap(frozenset({1, 2, 3}), frozenset({2, 3}))
        1.0
        >>> hunk_overlap(frozenset({1}), frozenset({2, 3}))
        0.0
    """
    if not gold_lines:
        return 1.0
    return len(seen_lines & gold_lines) / len(gold_lines)


@dataclass(frozen=True, slots=True)
class HunkOverlapReport:
    """Per-file hunk overlap with an explicit fidelity stamp (ADR 0011).

    ``by_file`` holds overlap ONLY for used gold files that carry hunk-level
    evidence; ``file_level_only`` names used gold files whose evidence was
    file-level (member/decision/references/glob/overview) — deliberately absent
    from the hunk metric so no line precision is fabricated.
    """

    by_file: dict[str, float]
    file_level_only: frozenset[str]

    def mean(self) -> float:
        """Mean overlap across hunk-evidenced files (``1.0`` if none qualify)."""
        if not self.by_file:
            return 1.0
        return sum(self.by_file.values()) / len(self.by_file)


def _span_lines(spans: Iterable[tuple[int, int]]) -> frozenset[int]:
    """Expand inclusive ``(start, end)`` spans to a set of 1-indexed lines."""
    lines: set[int] = set()
    for start, end in spans:
        lines.update(range(start, end + 1))
    return frozenset(lines)


def hunk_overlap_report(
    attribution: Attribution, gold_line_map: dict[str, frozenset[int]]
) -> HunkOverlapReport:
    """Build the fidelity-stamped hunk-overlap report over used gold files.

    A used gold file with any hunk-fidelity span lands in ``by_file``; one with
    only file-level evidence lands in ``file_level_only``. Files never surfaced
    are omitted entirely (no evidence to score).
    """
    by_file: dict[str, float] = {}
    file_level: set[str] = set()
    for path, gold_lines in gold_line_map.items():
        if path not in attribution.used_files:
            continue
        spans = attribution.spans_for(path)
        if spans:
            by_file[path] = hunk_overlap(_span_lines(spans), gold_lines)
        elif _has_file_level_evidence(attribution, path):
            file_level.add(path)
    return HunkOverlapReport(by_file=by_file, file_level_only=frozenset(file_level))


def _has_file_level_evidence(attribution: Attribution, path: str) -> bool:
    """True when ``path`` was surfaced only through file-level-fidelity rows."""
    return any(s.path == path and s.fidelity is Fidelity.FILE for s in attribution.surfacings)


def tool_calls_to_first_gold(
    tool_events: Iterable[ToolEvent], gold_files: frozenset[str], *, workspace_root: str
) -> int | None:
    """Number of tool calls (seq order) through the first to surface a gold file.

    ``None`` when no tool call ever surfaces a gold file. Counts tool events
    only (loop Reads are not MCP tool calls). 1-indexed: the first call
    surfacing a gold file returns ``1``.
    """
    ordered = sorted(tool_events, key=lambda e: e.seq)
    for index, event in enumerate(ordered, start=1):
        if _surfaces_gold(event, gold_files, workspace_root):
            return index
    return None


def _surfaces_gold(event: ToolEvent, gold_files: frozenset[str], workspace_root: str) -> bool:
    """True when any item path of ``event`` normalizes into ``gold_files``."""
    for item in event.result_ids or ():
        raw = item.get("path")
        if not isinstance(raw, str) or not raw:
            continue
        norm = normalize_path(raw, workspace_root=workspace_root)
        if norm.gold_matchable and norm.value in gold_files:
            return True
    return False


# ---------------------------------------------------------------------------
# Per-tool evidence yield
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolYield:
    """Distinct-file counts one tool earned in each tier (ADR 0011 diagnostic)."""

    surfaced: int
    inspected: int
    used: int


def per_tool_yield(attribution: Attribution) -> dict[str, ToolYield]:
    """Distinct files each tool surfaced / inspected / used.

    A file counts for a tool if that tool produced any surfacing edge for it;
    ``used`` intersects the tool's surfaced files with the trajectory patch set.
    """
    surfaced: dict[str, set[str]] = {}
    inspected: dict[str, set[str]] = {}
    for edge in attribution.surfacings:
        surfaced.setdefault(edge.tool, set()).add(edge.path)
        if edge.inspected:
            inspected.setdefault(edge.tool, set()).add(edge.path)
    return {
        tool: ToolYield(
            surfaced=len(files),
            inspected=len(inspected.get(tool, set())),
            used=len(files & attribution.used_files),
        )
        for tool, files in surfaced.items()
    }


# ---------------------------------------------------------------------------
# Edit layer
# ---------------------------------------------------------------------------


def patch_applies(outcome: GroundTruthOutcome) -> bool:
    """Whether the model's patch applied cleanly (``outcome.patch_applied``)."""
    return outcome.patch_applied


def f2p_fraction(outcome: GroundTruthOutcome, gold_f2p: Iterable[str]) -> float:
    """Fraction of gold FAIL_TO_PASS tests now passing: ``|gold ∩ passed| / |gold|``.

    Names are harness-normalized (``eval_report.normalize_test_name``) both
    sides. Empty gold F2P → ``1.0``.
    """
    gold = frozenset(normalize_test_name(n) for n in gold_f2p)
    if not gold:
        return 1.0
    return len(gold & outcome.f2p_passed) / len(gold)


def p2p_regression_count(outcome: GroundTruthOutcome, gold_p2p: Iterable[str]) -> int:
    """Count of gold PASS_TO_PASS tests NOT passing after the edit (regressions).

    A gold P2P name absent from the passed set counts as a regression (missing
    ⇒ failed, matching the strict resolve semantics of ``eval_report``).
    """
    gold = frozenset(normalize_test_name(n) for n in gold_p2p)
    return len(gold - outcome.p2p_passed)


# ---------------------------------------------------------------------------
# Cost layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenTotals:
    """Usage totals deduped by ``message_id`` (ADR 0010 / the _parse.py trap)."""

    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

# The ``result`` LoopEvent is the stream-json result envelope. Its usage is the
# client's own RUN TOTAL, not a per-message increment (see
# ``docs/superpowers/research/2026-07-18-phase2-evidence-claude-code-artifacts.md``),
# so it is excluded from the computed per-message sum and surfaced separately.
_RESULT_KIND = "result"


def deduped_token_totals(loop_events: Iterable[LoopEvent]) -> TokenTotals:
    """The COMPUTED per-message usage sum, counting each ``message_id`` ONCE.

    ``message.usage`` is byte-identical across every content-block record of one
    message id, so summing per-record over-counts several-fold — the verified
    ``_parse.py`` failure mode. We dedupe by ``message_id`` (records without one
    are summed individually, as they cannot alias another).

    The ``result`` envelope is EXCLUDED here: its usage is the client-reported
    RUN TOTAL (see :func:`reported_token_totals`), not a per-message increment, so
    adding it on top of the per-message usage would double-count. The two totals
    are cross-checkable — ``computed = per-message dedup``, ``reported = client run
    total`` — and a divergence between them is a diagnostic capture signal, NOT an
    error.

    Example:
        >>> e = LoopEvent(event_id="x", trajectory_id="t", kind="assistant",
        ...     turn=0, message_id="m", usage={"input_tokens": 5})
        >>> deduped_token_totals([e, e]).input_tokens
        5
    """
    seen: dict[str, Mapping[str, object]] = {}
    anonymous: list[Mapping[str, object]] = []
    for event in loop_events:
        if event.kind == _RESULT_KIND:
            continue  # run total, not an increment — counted by reported_token_totals
        _bucket_usage(event, seen, anonymous)
    return _sum_usages([*seen.values(), *anonymous])


def reported_token_totals(loop_events: Iterable[LoopEvent]) -> TokenTotals:
    """The client-REPORTED run total from the ``result`` envelope's usage.

    The stream-json result line carries the CLI's own run-total usage. It is
    surfaced alongside the computed :func:`deduped_token_totals` so the two are
    cross-checkable; a divergence is diagnostic (capture drift), not an error.
    Zero totals when no result envelope carried usage.
    """
    envelopes = [
        event.usage
        for event in loop_events
        if event.kind == _RESULT_KIND and isinstance(event.usage, Mapping)
    ]
    return _sum_usages(envelopes)


def _sum_usages(usages: Iterable[Mapping[str, object]]) -> TokenTotals:
    """Sum the four ``_USAGE_KEYS`` across a collection of usage mappings."""
    totals: Counter[str] = Counter()
    for usage in usages:
        for key in _USAGE_KEYS:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
    return TokenTotals(*(totals[k] for k in _USAGE_KEYS))


def _bucket_usage(
    event: LoopEvent, seen: dict[str, Mapping[str, object]], anonymous: list[Mapping[str, object]]
) -> None:
    """Route one event's usage into the dedupe map or the anonymous list."""
    if event.usage is None:
        return
    if event.message_id is None:
        anonymous.append(event.usage)
    else:
        seen.setdefault(event.message_id, event.usage)


def calls_by_tool(tool_events: Iterable[ToolEvent]) -> dict[str, int]:
    """Count of tool calls per tool name.

    Example:
        >>> from pydocs_eval.trajectory.schema import ToolEvent
        >>> e = ToolEvent(event_id="e", trajectory_id="t", seq=1, ts=0.0,
        ...     turn=1, tool="grep", args={}, latency_ms=1.0)
        >>> calls_by_tool([e, e])
        {'grep': 2}
    """
    return dict(Counter(event.tool for event in tool_events))


def turn_count(events: Iterable[ToolEvent | LoopEvent]) -> int:
    """Number of distinct ``turn`` indices present across the trajectory."""
    return len({event.turn for event in events})


def wall_clock_seconds(tool_events: Iterable[ToolEvent]) -> float:
    """Span of server ``ts`` (wall-clock ``time.time`` seconds) across tool calls.

    ``max(ts) - min(ts)``; fewer than two tool events → ``0.0``. The server records
    ``ts`` via ``time.time()`` (``trace_recorder.py``); only ``latency_ms`` is a
    ``perf_counter`` measurement. This span is authoritative for duration; loop
    wall clock is not recorded here.
    """
    times = sorted(event.ts for event in tool_events)
    if len(times) < 2:
        return 0.0
    return times[-1] - times[0]


def total_cost_usd(raw: object) -> float:
    """Coerce a runner-reported ``total_cost_usd`` to a non-negative float.

    Cost lives in the result envelope / run trailer, not in an event; this
    validates it at the metric boundary. A negative or non-numeric value raises
    with the offending value and the expected shape.
    """
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise ValueError(f"cost_usd must be a number: got {raw!r}, expected float >= 0")
    if raw < 0:
        raise ValueError(f"cost_usd must be >= 0: got {raw!r}")
    return float(raw)


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrajectoryMetrics:
    """All R3 metrics for one trajectory, bundled for the derived-output layer."""

    gold_file_recall: float
    wasted_read_ratio: float
    mean_hunk_overlap: float
    hunk_overlap: HunkOverlapReport
    tool_calls_to_first_gold: int | None
    per_tool_yield: dict[str, ToolYield]
    patch_applies: bool
    f2p_fraction: float
    p2p_regression_count: int
    tokens: TokenTotals
    calls_by_tool: dict[str, int]
    turns: int
    wall_clock_seconds: float
    cost_usd: float
    tool_calls: int = field(default=0)
    # Dataclass field ordering forces the defaulted ``reported_tokens`` to the end,
    # away from the ``tokens`` field it cross-checks; ``tokens`` is the computed
    # per-message dedup, ``reported_tokens`` the client run total (see the cost layer).
    reported_tokens: TokenTotals = field(default_factory=lambda: TokenTotals(0, 0, 0, 0))


def compute_metrics(
    *,
    attribution: Attribution,
    tool_events: Iterable[ToolEvent],
    loop_events: Iterable[LoopEvent],
    gold_files: frozenset[str],
    gold_line_map: dict[str, frozenset[int]],
    gold_f2p: Iterable[str],
    gold_p2p: Iterable[str],
    outcome: GroundTruthOutcome,
    cost_usd: object,
    workspace_root: str,
) -> TrajectoryMetrics:
    """Assemble the full :class:`TrajectoryMetrics` bundle from parsed inputs."""
    tools = tuple(tool_events)
    loops = tuple(loop_events)
    overlap = hunk_overlap_report(attribution, gold_line_map)
    return TrajectoryMetrics(
        gold_file_recall=gold_file_recall(attribution.surfaced_files, gold_files),
        wasted_read_ratio=wasted_read_ratio(attribution.inspected_files, attribution.used_files),
        mean_hunk_overlap=overlap.mean(),
        hunk_overlap=overlap,
        tool_calls_to_first_gold=tool_calls_to_first_gold(
            tools, gold_files, workspace_root=workspace_root
        ),
        per_tool_yield=per_tool_yield(attribution),
        patch_applies=patch_applies(outcome),
        f2p_fraction=f2p_fraction(outcome, gold_f2p),
        p2p_regression_count=p2p_regression_count(outcome, gold_p2p),
        tokens=deduped_token_totals(loops),
        reported_tokens=reported_token_totals(loops),
        calls_by_tool=calls_by_tool(tools),
        turns=turn_count((*tools, *loops)),
        wall_clock_seconds=wall_clock_seconds(tools),
        cost_usd=total_cost_usd(cost_usd),
        tool_calls=len(tools),
    )
