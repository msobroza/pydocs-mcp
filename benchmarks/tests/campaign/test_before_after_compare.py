"""``before-after-compare``: up to three variant arms against one baseline, and the verdict.

Every arm here is a directory as a campaign arm or the chat runner writes one
(``arm.json`` + ``arm_settings.json``), measured off its traces, its stored
answers scored against the split's gold (the ``compare_split`` fixture). Ten
tasks per arm, so one task is 0.1; unless a test says otherwise, the A/A
replicate matches the baseline and the band is that one-task floor.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_arm import ArmSettings, write_arm_settings, write_arm_summary
from pydocs_eval.trajectory.ask_outcome import TaskOutcome

from ._fakes import COMPARE_TASK_IDS, FakeSplitTasks
from ._outcome_fixtures import (
    GOLD,
    arm_record,
    arm_summary,
    legacy_row,
    needle_trace,
    write_legacy_arm,
)

_OTHER = "pkg/other.py"
_CITED = f"It is in `{GOLD}`."
_BOTH_CITED = f"It is in `{GOLD}` and `{_OTHER}`."
_MISSED = "I could not find it."


@dataclass(frozen=True, slots=True)
class _Row:
    turns: int = 6
    answer: str = _CITED
    outcome: TaskOutcome = TaskOutcome.ANSWERED


def _rows(*, turns: Sequence[int] = (6,) * 10, cited: int = 8) -> list[_Row]:
    """Ten answered tasks: the first ``cited`` cite the needle, the rest miss it."""
    return [
        _Row(turns=turns[index], answer=_CITED if index < cited else _MISSED) for index in range(10)
    ]


def _write_arm(
    root: Path,
    name: str,
    rows: Sequence[_Row],
    *,
    runner_shaped: bool = False,
    commit: str = "a" * 40,
) -> Path:
    """An arm directory; ``runner_shaped`` writes it the way the chat runner does (no ceiling)."""
    arm_dir = root / name
    arm_dir.mkdir(parents=True)
    trace = needle_trace(root)
    records = [
        arm_record(
            trace,
            task_id=task_id,
            turns=row.turns,
            outcome=row.outcome,
            answer=row.answer,
            answer_chars=len(row.answer),
        )
        for task_id, row in zip(COMPARE_TASK_IDS, rows, strict=True)
    ]
    write_arm_summary(arm_dir, dataclasses.replace(arm_summary(*records), commit=commit))
    write_arm_settings(arm_dir, _settings(arm_dir, runner_shaped=runner_shaped))
    return arm_dir


def _settings(arm_dir: Path, *, runner_shaped: bool) -> ArmSettings:
    return ArmSettings(
        role="baseline",
        commit="a" * 40,
        workspace="/ws",
        model="m",
        trace_root=str(arm_dir),
        out_dir=str(arm_dir),
        max_agent_turns=12,
        estimated_usd_per_rollout=0.0 if runner_shaped else 0.01,
        cost_ceiling_usd=0.0 if runner_shaped else 5.0,
        pydocs_config="serve.yaml" if runner_shaped else None,
        llm_block={"temperature": 0} if runner_shaped else None,
    )


def _run(
    capsys: pytest.CaptureFixture[str],
    baseline: Path,
    *variants: Path,
    replicate: Path | None = None,
    extra: Sequence[str] = (),
) -> tuple[int, str]:
    """Run the verb against ``baseline`` (its own replicate unless one is given)."""
    from pydocs_eval.campaign.__main__ import main

    argv = ["before-after-compare", "--baseline", str(baseline), "--split", "repoqa-qa/dev"]
    argv += ["--aa-replicate", str(replicate or baseline)]
    for variant in variants:
        argv += ["--variant", str(variant)]
    code = main([*argv, *extra])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def _row_of(report: str, arm: Path) -> str:
    """``arm``'s row in the comparison table."""
    [row] = [line for line in report.splitlines() if line.startswith(f"| `{arm}`")]
    return row


def _verdict_of(report: str, arm: Path) -> str:
    """The verdict cell of ``arm``'s row."""
    return _row_of(report, arm).rstrip(" |").rsplit("|", 1)[1].strip()


def test_fewer_turns_with_correctness_held_passes(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))

    code, report = _run(capsys, baseline, faster)

    assert code == 0
    assert _verdict_of(report, faster) == "PASS (Q10)"
    assert "needle cited band 0.100" in report


def test_budget_exhaustion_rising_fails(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    rows = _rows(turns=(5,) * 10)
    rows[9] = _Row(turns=12, answer=_MISSED, outcome=TaskOutcome.BUDGET_EXHAUSTED)
    exhausting = _write_arm(tmp_path, "exhausting", rows)

    code, report = _run(capsys, baseline, exhausting)

    assert code == 1
    assert _verdict_of(report, exhausting) == "FAIL (Q10)"
    assert "budget exhaustion rose (+0.100)" in report


def test_needle_cited_falling_past_the_band_fails(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    careless = _write_arm(tmp_path, "careless", _rows(turns=(5,) * 10, cited=6))

    code, report = _run(capsys, baseline, careless)

    assert code == 1
    assert _verdict_of(report, careless) == "FAIL (Q10)"
    assert "needle cited fell past the band (0.800 → 0.600, band 0.100)" in report


def test_the_band_widens_to_the_aa_pairs_difference(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    replicate = _write_arm(tmp_path, "replicate", _rows(cited=6))
    careless = _write_arm(tmp_path, "careless", _rows(turns=(5,) * 10, cited=6))

    code, report = _run(capsys, baseline, careless, replicate=replicate)

    assert code == 0
    assert "needle cited band 0.200" in report
    assert _verdict_of(report, careless) == "PASS (Q10)"


def test_a_replicate_without_stored_answers_leaves_no_band_and_no_verdict(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    legacy = _write_arm(tmp_path, "legacy", [_Row(answer="", outcome=TaskOutcome.UNRECORDED)] * 10)
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))

    code, report = _run(capsys, baseline, faster, replicate=legacy)

    assert code == 2
    assert "needle cited band n/a: the A/A pair shares no task that defines it" in report
    assert _verdict_of(report, faster) == "no verdict"


def test_a_replicate_from_another_commit_is_refused_by_name(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    """An A/A pair runs one commit twice: across two commits the difference is not noise."""
    baseline = _write_arm(tmp_path, "baseline", _rows())
    other = _write_arm(tmp_path, "other", _rows(), commit="b" * 40)
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))

    code, report = _run(capsys, baseline, faster, replicate=other)

    assert code == 2
    assert "is not the baseline's A/A twin" in report
    assert "b" * 40 in report and "a" * 40 in report


def test_the_aa_replicate_is_required(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    from pydocs_eval.campaign.__main__ import main

    baseline = str(_write_arm(tmp_path, "baseline", _rows()))
    argv = ["before-after-compare", "--baseline", baseline, "--variant", baseline]

    with pytest.raises(SystemExit) as refused:
        main([*argv, "--split", "repoqa-qa/dev"])

    assert refused.value.code == 2
    assert "--aa-replicate" in capsys.readouterr().err


@pytest.mark.parametrize("runner_side", ["baseline", "variant"])
def test_a_chat_runner_output_reads_as_either_side(
    tmp_path: Path,
    compare_split: FakeSplitTasks,
    capsys: pytest.CaptureFixture[str],
    runner_side: str,
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows(), runner_shaped=runner_side == "baseline")
    faster = _write_arm(
        tmp_path, "faster", _rows(turns=(5,) * 10), runner_shaped=runner_side == "variant"
    )

    code, report = _run(capsys, baseline, faster)

    assert (code, _verdict_of(report, faster)) == (0, "PASS (Q10)")


def test_more_than_three_variants_are_refused_by_count(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    variants = [_write_arm(tmp_path, f"v{index}", _rows()) for index in range(4)]

    code, report = _run(capsys, baseline, *variants)

    assert code == 2
    assert "4 variants, expected 1 to 3" in report


def test_the_p_values_are_holm_adjusted_across_variants(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))
    same = _write_arm(tmp_path, "same", _rows())

    _code, report = _run(capsys, baseline, faster, same)

    # Ten ties on turns: Wilcoxon one-sided p = 2**-10 raw, x2 under Holm for the smaller.
    assert "Holm-adjusted across 2 variant(s)" in report
    assert "| 0.00195 |" in _row_of(report, faster)


@pytest.mark.parametrize(("slower_tasks", "verdict"), [(4, "PASS (Q44)"), (6, "FAIL (Q10)")])
def test_the_bounded_rule_takes_a_small_rise_for_better_coverage(
    tmp_path: Path,
    compare_split: FakeSplitTasks,
    capsys: pytest.CaptureFixture[str],
    slower_tasks: int,
    verdict: str,
) -> None:
    """Coverage 0.5 → 1.0 with penalised turns up +0.4 passes (on sign-off); +0.6 fails."""
    compare_split.gold_by_task.update(dict.fromkeys(COMPARE_TASK_IDS, (GOLD, _OTHER)))
    baseline = _write_arm(tmp_path, "baseline", [_Row(answer=_CITED)] * 10)
    turns = (7,) * slower_tasks + (6,) * (10 - slower_tasks)
    thorough = _write_arm(
        tmp_path, "thorough", [_Row(turns=count, answer=_BOTH_CITED) for count in turns]
    )

    code, report = _run(capsys, baseline, thorough, extra=["--completeness-arm"])

    assert _verdict_of(report, thorough) == verdict
    assert ("  - Owner sign-off required" in report) is (slower_tasks == 4)
    assert code == (0 if slower_tasks == 4 else 1)


def test_an_arm_older_than_its_recorded_cap_reads_outcomes_against_its_settings_cap(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pre-outcome ``arm.json``: its 47-character reply at the 12-turn cap was the apology."""
    trace = needle_trace(tmp_path)
    arms = []
    for name in ("baseline", "legacy"):
        arm_dir = write_legacy_arm(tmp_path, name, [legacy_row(trace, answer_chars=47, turns=12)])
        write_arm_settings(arm_dir, _settings(arm_dir, runner_shaped=False))
        arms.append(arm_dir)

    _code, report = _run(capsys, arms[0], arms[1])

    assert "| 13 → 13 |" in _row_of(report, arms[1]), "budget exhausted: the cap + 1"


def test_without_stored_answers_there_is_no_verdict(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    unrecorded = [_Row(answer="", outcome=TaskOutcome.UNRECORDED)] * 10
    baseline = _write_arm(tmp_path, "baseline", unrecorded)
    legacy = _write_arm(tmp_path, "legacy", unrecorded)

    code, report = _run(capsys, baseline, legacy)

    assert code == 2
    assert _verdict_of(report, legacy) == "no verdict"
