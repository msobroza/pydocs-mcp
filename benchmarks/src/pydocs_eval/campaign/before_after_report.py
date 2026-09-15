"""Both arms' numbers, side by side, as a markdown report fit to post on a ticket.

The metrics themselves live in the trajectory layer and are computed here
EXACTLY once per arm, from that arm's recorded traces — never re-derived, never
re-implemented. This module only aggregates and renders:

- per trajectory, the needed-call block plus tool calls to first gold;
- across trajectories, the mean of each, **dropping undefined values**. A rate
  over opportunities the server created (pointers offered, target-fetching
  calls) reads ``None`` when there were none; averaging that in as zero would
  report "every pointer was ignored" for a run that was offered no pointer.
  An arm whose every trajectory is undefined reports ``n/a``, not ``0``.

Response text comes from the run's blob store, not from each event's preview:
a response renders its follow-up pointers at its very end, past the byte cap, so
the preview would systematically under-report the pointer-followed rate.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest, MeasurementPlan
from pydocs_eval.campaign.before_after_arm import ArmSummary, ArmTaskRecord
from pydocs_eval.trajectory.ask_events import load_ask_tool_events
from pydocs_eval.trajectory.blob_store import BLOBS_DIRNAME
from pydocs_eval.trajectory.call_efficiency import (
    CallEfficiency,
    ResponseTextFromBlobs,
    compute_call_efficiency,
)
from pydocs_eval.trajectory.metrics import tool_calls_to_first_gold

_UNDEFINED = "n/a"


@dataclass(frozen=True, slots=True)
class ArmMetrics:
    """One arm's aggregate — every value a mean over the arm's trajectories."""

    commit: CommitUnderTest
    trajectories: int
    tool_calls: int
    needless_call_rate: float | None
    resurfacing: int
    zero_yield: int
    fan_out_where_batch: int
    tool_mismatch: int
    pointer_followed_rate: float | None
    parallel_calls_per_turn: float | None
    batch_vs_fanout_ratio: float | None
    tool_calls_to_first_gold: float | None


def measure_arm(summary: ArmSummary, commit: CommitUnderTest, *, workspace: Path) -> ArmMetrics:
    """Aggregate one arm's recorded trajectories into its metric block."""
    per_task = [_measure_task(task, workspace=workspace) for task in summary.tasks]
    efficiencies = [efficiency for efficiency, _ in per_task]
    return ArmMetrics(
        commit=commit,
        trajectories=len(per_task),
        tool_calls=sum(e.needless.total_calls for e in efficiencies),
        needless_call_rate=_mean(e.needless.rate for e in efficiencies),
        resurfacing=sum(len(e.needless.resurfacing) for e in efficiencies),
        zero_yield=sum(len(e.needless.zero_yield) for e in efficiencies),
        fan_out_where_batch=sum(len(e.needless.fan_out_where_batch) for e in efficiencies),
        tool_mismatch=sum(len(e.needless.tool_mismatch) for e in efficiencies),
        pointer_followed_rate=_mean(e.pointer_followed_rate for e in efficiencies),
        parallel_calls_per_turn=_mean(e.parallel_calls_per_turn for e in efficiencies),
        batch_vs_fanout_ratio=_mean(e.batch_fanout.ratio for e in efficiencies),
        tool_calls_to_first_gold=_mean(first_gold for _, first_gold in per_task),
    )


def _measure_task(task: ArmTaskRecord, *, workspace: Path) -> tuple[CallEfficiency, int | None]:
    """One trajectory's needed-call block and its tool calls to first gold."""
    trace_dir = Path(task.trace_dir)
    events = load_ask_tool_events(trace_dir)
    efficiency = compute_call_efficiency(
        events, response_text=ResponseTextFromBlobs(trace_dir.parent / BLOBS_DIRNAME)
    )
    first_gold = tool_calls_to_first_gold(
        events, frozenset(task.gold_files), workspace_root=str(workspace)
    )
    return efficiency, first_gold


def _mean(values: Iterable[float | int | None]) -> float | None:
    """Mean of the DEFINED values; ``None`` when every value is undefined."""
    defined = [float(value) for value in values if value is not None]
    if not defined:
        return None
    return sum(defined) / len(defined)


def render_report(plan: MeasurementPlan, arms: Sequence[ArmMetrics]) -> str:
    """The markdown a run posts on the ticket: one column per arm, plus the delta."""
    baseline, candidate = arms
    return "\n".join(
        [
            "## Before/after measurement",
            "",
            *_provenance_lines(plan, baseline, candidate),
            "",
            "| Metric | baseline | candidate | delta |",
            "|---|---|---|---|",
            *(_metric_row(name, baseline, candidate) for name in _ROWS),
            "",
            *_reading_lines(),
        ]
    )


def _provenance_lines(
    plan: MeasurementPlan, baseline: ArmMetrics, candidate: ArmMetrics
) -> list[str]:
    """What ran, so the numbers can be re-derived from the report alone."""
    return [
        f"- split: `{plan.split}` — {len(plan.task_ids)} task(s) per arm",
        f"- baseline: `{baseline.commit.sha[:12]}` {baseline.commit.subject}",
        f"- candidate: `{candidate.commit.sha[:12]}` {candidate.commit.subject}",
        f"- model: `{plan.model}` @ `{plan.endpoint}`, "
        f"{plan.max_agent_turns} agent turn(s) per task",
        f"- estimated spend: ${plan.estimated_usd:.2f} "
        "(the in-process harness reports no price; this is the plan's estimate)",
    ]


@dataclass(frozen=True, slots=True)
class _Row:
    """One report row: its label and how to read it off an arm."""

    label: str
    field: str
    lower_is_better: bool


# The metric block, in reporting order. ``description_tokens`` rides the
# commit, not the trajectories, so it reads off ``ArmMetrics.commit``.
_ROWS: tuple[_Row, ...] = (
    _Row("needless-call rate", "needless_call_rate", True),
    _Row("— resurfacing calls", "resurfacing", True),
    _Row("— zero-yield calls", "zero_yield", True),
    _Row("— fan-out-where-batch calls", "fan_out_where_batch", True),
    _Row("— tool-mismatch calls", "tool_mismatch", True),
    _Row("pointer-followed rate", "pointer_followed_rate", False),
    _Row("parallel calls per turn", "parallel_calls_per_turn", False),
    _Row("batch-versus-fan-out ratio", "batch_vs_fanout_ratio", False),
    _Row("tool calls to first gold", "tool_calls_to_first_gold", True),
    _Row("tool calls (total)", "tool_calls", True),
    _Row("description tokens", "description_tokens", True),
)


def _metric_row(row: _Row, baseline: ArmMetrics, candidate: ArmMetrics) -> str:
    before, after = _value_of(baseline, row.field), _value_of(candidate, row.field)
    arrow = "↓" if row.lower_is_better else "↑"
    return f"| {row.label} {arrow} | {_fmt(before)} | {_fmt(after)} | {_delta(before, after)} |"


def _value_of(arm: ArmMetrics, field: str) -> float | None:
    if field == "description_tokens":
        return float(arm.commit.description_tokens)
    value = getattr(arm, field)
    return None if value is None else float(value)


def _fmt(value: float | None) -> str:
    if value is None:
        return _UNDEFINED
    return f"{value:.0f}" if value == int(value) else f"{value:.3f}"


def _delta(before: float | None, after: float | None) -> str:
    """The signed change; ``n/a`` when either side is undefined."""
    if before is None or after is None:
        return _UNDEFINED
    change = after - before
    return f"{change:+.3f}" if change else "0"


def _reading_lines() -> list[str]:
    """How to read the table — the success criterion and the undefined values."""
    return [
        "`↓` marks a metric that is better lower, `↑` one that is better higher. "
        "The change succeeds when the needless-call rate goes DOWN while tool calls "
        "to first gold stay flat or improve.",
        "",
        f"`{_UNDEFINED}` means undefined, not zero: a rate over opportunities the "
        "server created is undefined when there were none, and such trajectories are "
        "dropped from the mean rather than counted as zero.",
    ]
