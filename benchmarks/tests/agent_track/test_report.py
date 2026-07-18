"""Report aggregation + bootstrap CI for the agent track (spec §D15).

Fully offline: builds ``PairResult``s directly (no runner, no judge, no
subprocess) and asserts the paired report renders the mean per-metric delta, a
95% CI, and the per-``qa_type`` breakout, and that ``bootstrap_ci`` is
deterministic for a fixed seed (the slice-6 comparison contract).
"""

from __future__ import annotations

from pydocs_eval.agent_track._types import JudgeScore, PairResult, RunMetrics
from pydocs_eval.agent_track.report import (
    bootstrap_ci,
    format_agent_track_report,
)


def _metrics(*, cost: float, qa_hint: str = "") -> RunMetrics:
    # The report only aggregates the numeric fields; ``qa_hint`` keeps the
    # answers distinct so nothing collapses by identity.
    return RunMetrics(
        cost_usd=cost,
        wall_seconds=cost * 10,
        turns=int(cost * 5) + 1,
        tool_calls=int(cost * 8) + 1,
        distinct_files_read=int(cost * 4) + 1,
        cache_read_tokens=int(cost * 1000),
        cache_write_tokens=int(cost * 200),
        answer=f"answer {qa_hint} {cost}",
    )


def _judge(mean: float) -> JudgeScore:
    return JudgeScore(
        correctness=mean,
        completeness=mean,
        relevance=mean,
        clarity=mean,
        reasoning="",
        mean=mean,
    )


def _pairs(
    *,
    bare_cost: list[float],
    indexed_cost: list[float],
    qa_types: list[str] | None = None,
    judge_means: list[float] | None = None,
) -> tuple[PairResult, ...]:
    n = len(bare_cost)
    qa = qa_types if qa_types is not None else ["How"] * n
    means = judge_means if judge_means is not None else [8.0] * n
    pairs: list[PairResult] = []
    for i in range(n):
        pairs.append(
            PairResult(
                task_id=f"repo@sha/{i}",
                qa_type=qa[i],
                bare=_metrics(cost=bare_cost[i], qa_hint=qa[i]),
                indexed=_metrics(cost=indexed_cost[i], qa_hint=qa[i]),
                judge=_judge(means[i]),
            )
        )
    return tuple(pairs)


def test_report_pairs_deltas_and_bootstrap_ci() -> None:
    pairs = _pairs(
        bare_cost=[1.0, 1.2, 0.8],
        indexed_cost=[0.6, 0.7, 0.5],
        qa_types=["How", "How", "Why"],
    )
    report = format_agent_track_report(pairs, dataset_name="swe-qa-pro", rng_seed=42)
    assert "cost" in report and "-4" in report  # ~-42% mean delta rendered
    assert "95% CI" in report
    assert "## By qa_type" in report


def test_bootstrap_ci_deterministic_for_seed() -> None:
    lo1, hi1 = bootstrap_ci([0.1, -0.2, 0.3, 0.0], n_resamples=1000, rng_seed=7)
    lo2, hi2 = bootstrap_ci([0.1, -0.2, 0.3, 0.0], n_resamples=1000, rng_seed=7)
    assert (lo1, hi1) == (lo2, hi2)


def test_bootstrap_ci_brackets_the_mean() -> None:
    # A percentile bootstrap CI of the mean must straddle the sample mean.
    deltas = [-0.4, -0.5, -0.3]
    lo, hi = bootstrap_ci(deltas, n_resamples=500, rng_seed=1)
    mean = sum(deltas) / len(deltas)
    assert lo <= mean <= hi


def test_report_footer_reports_pairs_and_spend() -> None:
    pairs = _pairs(bare_cost=[1.0, 1.0], indexed_cost=[0.5, 0.5])
    report = format_agent_track_report(
        pairs,
        dataset_name="swe-qa-pro",
        rng_seed=0,
        discarded=3,
        spend_usd=12.5,
    )
    # Honest footer: pairs admitted, pairs discarded, total spend.
    assert "2" in report and "3" in report and "12.5" in report


def test_report_judge_parity_line_present() -> None:
    pairs = _pairs(bare_cost=[1.0], indexed_cost=[0.5], judge_means=[8.0])
    report = format_agent_track_report(pairs, dataset_name="swe-qa-pro", rng_seed=0)
    # The parity line reports the judge-mean delta so a reader can confirm the
    # efficiency deltas are read at answer-quality parity.
    assert "judge" in report.lower()
