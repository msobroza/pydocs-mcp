"""Paired-delta report for the agent track (spec §D15).

The harness measures efficiency deltas *at answer-quality parity*, so the report
is a per-task-paired aggregation: for each admitted ``PairResult`` it takes the
``indexed - bare`` delta on every efficiency metric (cost, wall, turns, tool
calls, files read, cache tokens) plus the judge-mean delta, then reports the
mean delta and a 95% bootstrap CI per metric. A judge-parity line makes the
"at parity" claim auditable (if the judge-mean CI straddles zero, the arms are
at quality parity and the efficiency deltas are read honestly). A
``## By qa_type`` section repeats the cost/judge means per category, and an
honest footer states pairs admitted / discarded / total spend — no silent caps.

Everything here is pure: it consumes already-scored ``PairResult``s and renders
Markdown. ``bootstrap_ci`` uses ``random.Random(rng_seed)`` only (no numpy, no
global RNG) so a report is byte-identical for a fixed seed — the slice-6
comparison contract.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence

from pydocs_eval.agent_track._types import PairResult, RunMetrics

# Percentile bootstrap defaults (§"Default values"). 95% CI → the 2.5 / 97.5
# percentiles of the resampled means. Single source of truth for a bump.
_DEFAULT_N_RESAMPLES = 2000
_CI_LOW_PCT = 2.5
_CI_HIGH_PCT = 97.5

# The efficiency metrics the report aggregates, in table order. Each entry is
# (column label, RunMetrics accessor) — the single source of truth so adding a
# metric is one line and the header/rows stay in lockstep.
_METRICS: tuple[tuple[str, Callable[[RunMetrics], float]], ...] = (
    ("cost ($)", lambda m: m.cost_usd),
    ("wall (s)", lambda m: m.wall_seconds),
    ("turns", lambda m: float(m.turns)),
    ("tool calls", lambda m: float(m.tool_calls)),
    ("files read", lambda m: float(m.distinct_files_read)),
    ("cache tokens", lambda m: float(m.cache_read_tokens + m.cache_write_tokens)),
)


def bootstrap_ci(
    deltas: Sequence[float],
    *,
    n_resamples: int = _DEFAULT_N_RESAMPLES,
    rng_seed: int,
) -> tuple[float, float]:
    """Percentile bootstrap 95% CI of the mean of ``deltas``.

    Resamples ``deltas`` with replacement ``n_resamples`` times, takes each
    resample's mean, and returns the (2.5, 97.5) percentiles. Uses
    ``random.Random(rng_seed)`` only — no numpy, no global RNG — so the CI is
    identical for a fixed seed (a resumed / re-rendered report reproduces the
    exact interval). An empty ``deltas`` yields ``(0.0, 0.0)`` (nothing to
    bound); a single value yields ``(v, v)`` (its own only resample).

    Example:
        >>> lo, hi = bootstrap_ci([-0.4, -0.5, -0.3], n_resamples=500, rng_seed=1)
        >>> lo <= -0.4 <= hi
        True
    """
    values = list(deltas)
    if not values:
        return 0.0, 0.0
    rng = random.Random(rng_seed)
    n = len(values)
    means = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(n_resamples))
    return _percentile(means, _CI_LOW_PCT), _percentile(means, _CI_HIGH_PCT)


def _percentile(sorted_values: list[float], pct: float) -> float:
    # Nearest-rank percentile on an already-sorted list. Kept dependency-free
    # (no numpy) to match the harness's "no numpy in the report" contract.
    if not sorted_values:
        return 0.0
    idx = round(pct / 100.0 * (len(sorted_values) - 1))
    idx = max(0, min(idx, len(sorted_values) - 1))
    return sorted_values[idx]


def format_agent_track_report(
    pairs: Sequence[PairResult],
    *,
    dataset_name: str,
    rng_seed: int,
    discarded: int = 0,
    spend_usd: float = 0.0,
) -> str:
    """Render the paired agent-track report as Markdown.

    Emits: a header (dataset + admitted-pair count); one delta table over the
    efficiency metrics — mean ``indexed - bare`` delta, mean % delta, and 95%
    bootstrap CI per metric; a judge-parity line (mean judge-mean delta ± CI);
    a ``## By qa_type`` breakout (cost + judge means per category); and an honest
    footer (pairs admitted / discarded / total spend). ``rng_seed`` fixes every
    bootstrap so the whole report is deterministic (slice-6 contract). An empty
    ``pairs`` still renders the header + footer so an all-discarded run is legible.

    Example:
        >>> format_agent_track_report(  # doctest: +SKIP
        ...     pairs, dataset_name="swe-qa-pro", rng_seed=42
        ... ).splitlines()[0]
        '# Agent-track report — swe-qa-pro'
    """
    lines = [
        f"# Agent-track report — {dataset_name}",
        "",
        f"Admitted pairs: {len(pairs)} (indexed − bare deltas, per-task paired).",
        "",
    ]
    lines += _delta_table(pairs, rng_seed=rng_seed)
    lines += ["", _judge_parity_line(pairs, rng_seed=rng_seed), ""]
    lines += _by_qa_type_section(pairs)
    lines += ["", _footer(len(pairs), discarded=discarded, spend_usd=spend_usd)]
    return "\n".join(lines) + "\n"


def _delta_table(pairs: Sequence[PairResult], *, rng_seed: int) -> list[str]:
    # One row per efficiency metric: mean absolute delta, mean % delta, 95% CI.
    header = "| metric | mean Δ | mean %Δ | 95% CI |"
    sep = "| --- | ---: | ---: | :---: |"
    rows = [header, sep]
    for label, accessor in _METRICS:
        deltas = [_delta(p, accessor) for p in pairs]
        pcts = [_pct_delta(p, accessor) for p in pairs]
        mean_delta = statistics.fmean(deltas) if deltas else 0.0
        mean_pct = statistics.fmean(pcts) if pcts else 0.0
        lo, hi = bootstrap_ci(deltas, rng_seed=rng_seed)
        rows.append(f"| {label} | {mean_delta:+.3g} | {mean_pct:+.0f}% | [{lo:+.3g}, {hi:+.3g}] |")
    return rows


def _judge_parity_line(pairs: Sequence[PairResult], *, rng_seed: int) -> str:
    # The "at parity" audit. The as-landed ``PairResult`` stores only the
    # indexed arm's blind ``JudgeScore`` (the orchestrator keeps
    # ``judge=scores[indexed]``), so the parity signal is the mean indexed judge
    # score ± 95% CI on a 0–10 rubric: a high, tight interval means the indexed
    # arm's answers are high-quality, so the efficiency deltas above are read at
    # a real quality level rather than by trading answers away for speed.
    means = [p.judge.mean for p in pairs if p.judge is not None]
    mean_score = statistics.fmean(means) if means else 0.0
    lo, hi = bootstrap_ci(means, rng_seed=rng_seed)
    return (
        f"**Judge parity** (indexed arm, mean of 5-dim 0–10 blind score): "
        f"{mean_score:.2f} (95% CI [{lo:.2f}, {hi:.2f}])."
    )


def _by_qa_type_section(pairs: Sequence[PairResult]) -> list[str]:
    # Per-category cost + judge means (What / Where / How / Why). Always emitted
    # (even with one category) so the section header is a stable anchor the
    # README/runbook can point at; empty when there are no pairs.
    lines = [
        "## By qa_type",
        "",
        "| qa_type | pairs | mean cost Δ | mean judge (indexed) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for qa_type in _ordered_qa_types(pairs):
        group = [p for p in pairs if p.qa_type == qa_type]
        cost_deltas = [_delta(p, lambda m: m.cost_usd) for p in group]
        judge_means = [p.judge.mean for p in group if p.judge is not None]
        mean_cost = statistics.fmean(cost_deltas) if cost_deltas else 0.0
        mean_judge = statistics.fmean(judge_means) if judge_means else 0.0
        label = qa_type or "(untagged)"
        lines.append(f"| {label} | {len(group)} | {mean_cost:+.3g} | {mean_judge:.2f} |")
    return lines


def _ordered_qa_types(pairs: Sequence[PairResult]) -> list[str]:
    # Canonical taxonomy order first (What/Where/How/Why), then any extras in
    # first-seen order, so the breakout reads predictably across runs.
    canonical = ["What", "Where", "How", "Why"]
    present = {p.qa_type for p in pairs}
    ordered = [t for t in canonical if t in present]
    ordered += [p.qa_type for p in pairs if p.qa_type not in canonical and p.qa_type not in ordered]
    return ordered


def _footer(admitted: int, *, discarded: int, spend_usd: float) -> str:
    # Honest accounting (no-silent-caps): what ran, what was dropped, what it cost.
    return (
        f"---\nPairs admitted: {admitted} · discarded: {discarded} · total spend: ${spend_usd:.2f}."
    )


def _delta(pair: PairResult, accessor: Callable[[RunMetrics], float]) -> float:
    # ``__post_init__`` guarantees both arms; narrow for the type checker.
    assert pair.bare is not None
    assert pair.indexed is not None
    return accessor(pair.indexed) - accessor(pair.bare)


def _pct_delta(pair: PairResult, accessor: Callable[[RunMetrics], float]) -> float:
    # Percent change of the indexed arm relative to bare. A zero bare baseline
    # can't be a percent denominator → 0.0 (the absolute delta column still
    # carries the truth for that metric).
    assert pair.bare is not None
    assert pair.indexed is not None
    base = accessor(pair.bare)
    if base == 0.0:
        return 0.0
    return (accessor(pair.indexed) - base) / base * 100.0
