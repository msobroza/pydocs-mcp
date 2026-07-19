"""Deterministic feedback strings — facts only, no advice (ADR 0012).

Templates over computed facts: gold files and which tool first surfaced each,
wasted reads, failing test names (trimmed), and budget consumption. **Facts, no
advice, no speculation** — the reflector is the interpreter (R5). Bounded at 2000
chars by default (configurable); non-empty on every failure (a failure with empty
feedback is a bug); error-carrying on degenerate cases; **never raises** — any
internal error falls back to the DSPy score-only floor.

There is no consumer-side length bound (GEPA inlines records verbatim, SkillOpt
refuses to truncate), so the producer self-caps — the gskill precedent.
"""

from __future__ import annotations

from pydocs_eval.trajectory import taxonomy as tax
from pydocs_eval.trajectory.attribution import Attribution
from pydocs_eval.trajectory.eval_report import GroundTruthOutcome, normalize_test_name
from pydocs_eval.trajectory.metrics import TrajectoryMetrics

_DEFAULT_MAX_CHARS = 2000

# How many failing test names to inline before summarizing the remainder, and the
# per-name character cap (the "trimmed output" bound — per-test stdout is not
# carried on ``GroundTruthOutcome``, so names are the reported fact).
_MAX_TESTS_LISTED = 10
_MAX_TEST_NAME_CHARS = 120

# Human-readable one-liners for the degenerate labels (ADR 0012 error-carrying).
_DEGENERATE_LINES = {
    tax.INFRA_ERROR: "Eval-harness infrastructure error: this rollout is excluded from score aggregates.",
    tax.EMPTY_TRAJECTORY: "Empty trajectory: no tool calls, no assistant output, empty patch.",
    tax.CRASH_BEFORE_FIRST_TOOL: "Crash before the first tool call: the run errored before any tool executed.",
    tax.PATCH_APPLY_FAILED: "Patch apply failed: a non-empty patch was produced but the harness could not apply it.",
    tax.BUDGET_EXHAUSTED: "Budget exhausted: a recorded run cap was hit with no patch produced.",
}


def build_feedback(
    *,
    label: str,
    metrics: TrajectoryMetrics,
    attribution: Attribution,
    outcome: GroundTruthOutcome,
    gold_files: frozenset[str],
    gold_f2p: frozenset[str],
    turn_cap: int | None,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Render the deterministic, fact-only feedback string, bounded to ``max_chars``.

    Non-empty for every input (degenerate labels get an error-carrying line);
    never raises — an internal error returns the score-only floor.
    """
    try:
        text = "\n".join(
            _sections(label, metrics, attribution, outcome, gold_files, gold_f2p, turn_cap)
        )
        capped = text[:max_chars]
        return capped or _floor(label)
    except Exception:  # never raises (ADR 0012 consumer contract) — floor on any error.
        return _floor(label)


def _floor(label: str) -> str:
    """The DSPy score-only floor — the guaranteed non-empty fallback."""
    return f"Trajectory outcome: {label}."


def _sections(
    label: str,
    metrics: TrajectoryMetrics,
    attribution: Attribution,
    outcome: GroundTruthOutcome,
    gold_files: frozenset[str],
    gold_f2p: frozenset[str],
    turn_cap: int | None,
) -> list[str]:
    """Ordered non-empty feedback lines for one trajectory."""
    lines = [f"Outcome: {label}."]
    degenerate = _DEGENERATE_LINES.get(label)
    if degenerate is not None:
        lines.append(degenerate)
    lines.append(_gold_line(attribution, gold_files))
    lines.append(_wasted_line(attribution))
    lines.append(_tests_line(outcome, gold_f2p))
    lines.append(_budget_line(metrics, turn_cap))
    return lines


def _gold_line(attribution: Attribution, gold_files: frozenset[str]) -> str:
    """Each gold file + the tool that first surfaced it (or 'not surfaced')."""
    if not gold_files:
        return "Gold files: none."
    parts = [f"{path} ({_surfacer(attribution, path)})" for path in sorted(gold_files)]
    return "Gold files: " + ", ".join(parts) + "."


def _surfacer(attribution: Attribution, path: str) -> str:
    """The first-surfacing tool for ``path``, or 'not surfaced'."""
    tool = attribution.first_touch.get(path)
    return f"first surfaced by {tool}" if tool else "not surfaced"


def _wasted_line(attribution: Attribution) -> str:
    """Files inspected but never edited (wasted reads)."""
    wasted = sorted(attribution.wasted_reads)
    if not wasted:
        return "Wasted reads: none."
    return "Wasted reads (inspected, never edited): " + ", ".join(wasted) + "."


def _tests_line(outcome: GroundTruthOutcome, gold_f2p: frozenset[str]) -> str:
    """Failing gold FAIL_TO_PASS test names (trimmed), with an overflow summary."""
    gold = frozenset(normalize_test_name(n) for n in gold_f2p)
    failing = sorted(gold - outcome.f2p_passed)
    if not failing:
        return "Failing target tests: none."
    listed = [_trim(name) for name in failing[:_MAX_TESTS_LISTED]]
    suffix = (
        "" if len(failing) <= _MAX_TESTS_LISTED else f" (+{len(failing) - _MAX_TESTS_LISTED} more)"
    )
    return "Failing target tests: " + ", ".join(listed) + suffix + "."


def _trim(name: str) -> str:
    """Truncate a long test name to the per-name cap."""
    if len(name) <= _MAX_TEST_NAME_CHARS:
        return name
    return name[: _MAX_TEST_NAME_CHARS - 1] + "…"


def _budget_line(metrics: TrajectoryMetrics, turn_cap: int | None) -> str:
    """Turn + token consumption against the recorded turn cap."""
    cap = "no cap recorded" if not turn_cap else str(turn_cap)
    return (
        f"Budget: turns {metrics.turns}/{cap}; "
        f"tokens in/out {metrics.tokens.input_tokens}/{metrics.tokens.output_tokens}; "
        f"tool calls {metrics.tool_calls}."
    )
